"""Bounded local immutable fragment records. Structural validation, not chemistry.

Only the production editor render/adoption path may turn a record into a drawing.
No caller paths, replacements, deletions, network access or chemistry imports.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import uuid
from .persistence import atomic_write

SCHEMA = 'nivram-fragment/1'
MAX_RECORDS = 128
MAX_DIRECTORY_ENTRIES = 256
MAX_BYTES = 400_000
MAX_TOTAL_BYTES = 4_000_000
ELEMENTS = set(('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og').split())


def _reject():
    raise ValueError('Invalid or unsupported local fragment record.')


def _keys(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        _reject()


def _id(value):
    if type(value) is not str or not 1 <= len(value) <= 80:
        _reject()
    return value


def _number(value, low, high, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not low <= value <= high or not math.isfinite(value):
        _reject()


def validate_graph(graph):
    _keys(graph, ('atoms', 'bonds'))
    atoms, bonds = graph['atoms'], graph['bonds']
    if type(atoms) is not list or not 1 <= len(atoms) <= 512 or type(bonds) is not list or len(bonds) > 1024:
        _reject()
    ids, bids, edges = set(), set(), set()
    for atom in atoms:
        _keys(atom, ('id', 'symbol', 'x', 'y'), ('charge', 'isotope', 'explicitHs', 'noImplicit', 'aromatic', 'chiralTag', 'radicals', 'mapNum'))
        aid = _id(atom['id'])
        if aid in ids or type(atom['symbol']) is not str or atom['symbol'] not in ELEMENTS:
            _reject()
        ids.add(aid)
        for axis in ('x', 'y'):
            _number(atom[axis], -10000, 10000)
        for key, low, high in [('charge', -8, 8), ('isotope', 0, 400), ('explicitHs', 0, 8), ('chiralTag', 0, 2), ('radicals', 0, 8), ('mapNum', 0, 999999)]:
            if key in atom:
                _number(atom[key], low, high, True)
        for key in ('aromatic', 'noImplicit'):
            if key in atom and type(atom[key]) is not bool:
                _reject()
    for bond in bonds:
        _keys(bond, ('id', 'begin', 'end', 'order'), ('direction', 'stereo', 'stereoAtoms'))
        bid = _id(bond['id'])
        begin, end = _id(bond['begin']), _id(bond['end'])
        edge = tuple(sorted((begin, end)))
        if bid in bids or begin not in ids or end not in ids or begin == end or edge in edges:
            _reject()
        bids.add(bid); edges.add(edge)
        if type(bond['order']) not in (int, float) or bond['order'] not in (1, 1.5, 2, 3):
            _reject()
        for key, high in [('direction', 6), ('stereo', 5)]:
            if key in bond:
                _number(bond[key], 0, high, True)
        refs = bond.get('stereoAtoms', [])
        if type(refs) is not list or len(refs) not in (0, 2) or any(type(ref) is not str or ref not in ids for ref in refs):
            _reject()
    return graph


def validate_record(record):
    _keys(record, ('schema', 'id', 'name', 'graph'))
    if record['schema'] != SCHEMA or type(record['id']) is not str or not re.fullmatch('[0-9a-f]{32}', record['id']):
        _reject()
    name = record['name']
    if type(name) is not str or not 1 <= len(name) <= 80 or not name.strip() or any(ord(c) < 32 or ord(c) == 127 for c in name):
        _reject()
    validate_graph(record['graph'])
    data = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(data) > MAX_BYTES:
        _reject()
    return json.loads(data), data


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _reject()
        result[key] = value
    return result


def _plain_path(path, *, directory=False, missing=False):
    # Reject every existing link/reparse component, including a redirected root.
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked or redirected fragment storage is not supported.')
        if part != path and not stat.S_ISDIR(info.st_mode):
            _reject()
        if part == path and ((directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode))):
            _reject()
    return path


def _read(path):
    _plain_path(path)
    before = path.lstat()
    if before.st_size > MAX_BYTES:
        _reject()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or opened.st_size > MAX_BYTES:
            _reject()
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        _reject()
    return data


class FragmentStore:
    def __init__(self, directory):
        self.directory = Path(os.path.abspath(directory))
        self._lock = threading.RLock()

    def _inventory(self):
        _plain_path(self.directory, directory=True, missing=True)
        if not self.directory.exists():
            return [], 0, 0, 0
        paths = []
        count = 0
        # Count every entry, including unrelated names and staging files.
        # A record-only cap would still allow unbounded directory enumeration.
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_DIRECTORY_ENTRIES:
                    raise ValueError('The local fragment directory exceeds its entry limit.')
                if entry.name.endswith('.json'):
                    paths.append(Path(entry.path))
                    if len(paths) > MAX_RECORDS:
                        raise ValueError('The local fragment shelf exceeds its record limit.')
        records, invalid, total = [], 0, 0
        for path in paths:
            try:
                if not re.fullmatch('[0-9a-f]{32}\\.json', path.name):
                    _reject()
                data = _read(path)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise OverflowError('The local fragment shelf exceeds its byte limit.')
                record, _ = validate_record(json.loads(data, object_pairs_hook=_pairs))
                if path.name != record['id'] + '.json':
                    _reject()
                records.append(record)
            except (ValueError, OSError, UnicodeError, RecursionError):
                invalid += 1
        return records, invalid, total, count

    def list(self):
        with self._lock:
            records, invalid, _, _ = self._inventory()
            return {'fragments': sorted(records, key=lambda r: (r['name'].casefold(), r['id'])), 'invalidRecords': invalid}

    def save(self, name, graph):
        with self._lock:
            # All failures before atomic publication are known not committed.
            try:
                record, data = validate_record({'schema': SCHEMA, 'id': uuid.uuid4().hex, 'name': name, 'graph': graph})
                records, invalid, total, entries = self._inventory()
                # Reserve both a staging entry and the final destination. The
                # exclusive POSIX writer briefly keeps both links before cleanup.
                # Do not publish a record that makes this shelf unlistable.
                if (invalid or len(records) >= MAX_RECORDS
                        or total + len(data) > MAX_TOTAL_BYTES
                        or entries + 2 > MAX_DIRECTORY_ENTRIES):
                    raise ValueError('The local fragment shelf is full or contains unreadable records.')
                _plain_path(self.directory, directory=True, missing=True)
                self.directory.mkdir(parents=True, exist_ok=True)
                _plain_path(self.directory, directory=True)
                destination = self.directory / (record['id'] + '.json')
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError('An immutable fragment identifier already exists.')
            except (ValueError, OSError, UnicodeError, OverflowError, RecursionError):
                return {'status': 'failed'}
            try:
                atomic_write(destination, data, exclusive=True)
                if _read(destination) != data:
                    raise OSError('Fragment readback differs.')
                return {'status': 'saved', 'fragment': record}
            except (ValueError, OSError):
                # Publication may have completed before cleanup/readback failed.
                # Retain the record and never retry or delete it here.
                return {'status': 'unconfirmed'}
