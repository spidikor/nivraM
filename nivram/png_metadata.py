"""Bounded native PNG claims. Integrity is not authorship or chemical truth.

This module deliberately imports no chemistry engine: batch PNG publication runs
outside the disposable chemistry worker. Import performs chemistry checks later.
"""
import hashlib
import json
import math
import re

from . import __version__

KEYWORD = 'nivraM Coordinates'
MAX_METADATA_BYTES = 1024 * 1024
_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result or key in ('__proto__', 'constructor', 'prototype'):
            raise ValueError('Duplicate or unsafe metadata key.')
        result[key] = value
    return result


def _json(data):
    def reject(_value):
        raise ValueError('Nonfinite metadata number.')
    try:
        result = json.loads(data, object_pairs_hook=_pairs, parse_constant=reject)
    except (RecursionError, UnicodeError) as exc:
        raise ValueError('Invalid metadata encoding or nesting.') from exc
    def walk(value, depth=0):
        if depth > 12:
            raise ValueError('Metadata nesting exceeds its limit.')
        if isinstance(value, str):
            value.encode('utf-8')
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Nonfinite metadata number.')
        elif isinstance(value, (dict, list)):
            for child in (value.values() if isinstance(value, dict) else value):
                walk(child, depth + 1)
    walk(result)
    return result


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError('Unsupported coordinate metadata fields.')


def _id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise ValueError('Invalid molecular identifier.')
    return value


def _number(value, lo, hi, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not lo <= value <= hi or not math.isfinite(value):
        raise ValueError('Invalid coordinate metadata number.')


def validate_coordinates(payload):
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_METADATA_BYTES:
        raise ValueError('Coordinate metadata exceeds its byte limit.')
    record = _json(payload.decode('utf-8'))
    if isinstance(record, dict) and record.get('schema') in ('nivram-png-coordinates/2','nivram-png-coordinates/3','nivram-png-coordinates/4','nivram-png-coordinates/5','nivram-png-coordinates/6'):
        return validate_3d_coordinates(payload)
    _keys(record, ['schema', 'producer', 'version', 'dimension', 'origin', 'units', 'graphJson', 'graphSha256'])
    if (record['schema'] != 'nivram-png-coordinates/1' or record['producer'] != 'nivraM'
            or type(record['dimension']) is not int or record['dimension'] != 2
            or record['origin'] != 'drawing-2d' or record['units'] != 'drawing'
            or not isinstance(record['version'], str) or not _VERSION.fullmatch(record['version'])):
        raise ValueError('Unsupported coordinate metadata contract.')
    if not isinstance(record['graphJson'], str) or not isinstance(record['graphSha256'], str):
        raise ValueError('Invalid graph integrity fields.')
    raw = record['graphJson'].encode('utf-8')
    if len(raw) > 512 * 1024:
        raise ValueError('Molecular graph exceeds its 512 KiB byte limit.')
    if not re.fullmatch('[0-9a-f]{64}', record['graphSha256']) or hashlib.sha256(raw).hexdigest() != record['graphSha256']:
        raise ValueError('Molecular graph checksum mismatch.')
    graph = _json(record['graphJson'])
    _keys(graph, ['atoms', 'bonds'])
    atoms, bonds = graph['atoms'], graph['bonds']
    if not isinstance(atoms, list) or not isinstance(bonds, list) or not 0 < len(atoms) <= 512 or len(bonds) > 1024:
        raise ValueError('Molecular graph exceeds its limits.')
    ids, bids, edges = set(), set(), set()
    for atom in atoms:
        _keys(atom, ['id', 'symbol', 'x', 'y'], ['charge', 'isotope', 'explicitHs', 'noImplicit', 'aromatic', 'chiralTag', 'radicals', 'mapNum'])
        aid = _id(atom['id'])
        if aid in ids:
            raise ValueError('Duplicate atom identifier.')
        ids.add(aid)
        if not isinstance(atom['symbol'], str) or not re.fullmatch('[A-Z][a-z]?', atom['symbol']):
            raise ValueError('Invalid element symbol.')
        for axis in ('x', 'y'):
            _number(atom[axis], -10000, 10000)
        for key, lo, hi in [('charge', -8, 8), ('isotope', 0, 400), ('explicitHs', 0, 8), ('chiralTag', 0, 2), ('radicals', 0, 8), ('mapNum', 0, 999999)]:
            if key in atom:
                _number(atom[key], lo, hi, True)
        for flag in ('noImplicit', 'aromatic'):
            if flag in atom and type(atom[flag]) is not bool:
                raise ValueError('Invalid molecular flag.')
    for bond in bonds:
        _keys(bond, ['id', 'begin', 'end', 'order'], ['direction', 'stereo', 'stereoAtoms'])
        bid = _id(bond['id'])
        begin, end = _id(bond['begin']), _id(bond['end'])
        edge = tuple(sorted((begin, end)))
        if bid in bids or edge in edges or begin not in ids or end not in ids or begin == end:
            raise ValueError('Invalid or duplicate bond.')
        bids.add(bid); edges.add(edge)
        _number(bond['order'], 1, 3)
        if bond['order'] not in (1, 1.5, 2, 3):
            raise ValueError('Unsupported bond order.')
        for key, hi in [('direction', 6), ('stereo', 5)]:
            if key in bond:
                _number(bond[key], 0, hi, True)
        refs = bond.get('stereoAtoms', [])
        if not isinstance(refs, list) or len(refs) not in (0, 2) or any(not isinstance(ref, str) or ref not in ids for ref in refs):
            raise ValueError('Invalid stereochemistry references.')
    return record, graph


def validate_export_text(kind, payload):
    """Only our exact uncompressed encodings are admitted to batch outputs."""
    if kind == b'tEXt' and payload.startswith(b'Software\0'):
        software = payload[9:].decode('ascii')
        if software != 'nivraM ' + __version__ or not _VERSION.fullmatch(software[7:]):
            raise ValueError('Invalid software identification.')
        return 'Software', software[7:]
    prefix = KEYWORD.encode('ascii') + b'\0\0\0\0\0'
    if kind == b'iTXt' and payload.startswith(prefix):
        record, _graph = validate_coordinates(payload[len(prefix):])
        if record['version'] != __version__:
            raise ValueError('PNG generator version does not match this runtime.')
        return KEYWORD, record['version']
    raise ValueError('Unsupported PNG text metadata.')


THREE_D_FIELDS = 'schema producer version dimension recordedOrigin format units molText molSha256 molBytes provenanceJson provenanceSha256 sourceContextText sourceContextSha256 sourceAtomIds'.split()
SUPPLIED_FIELDS = 'schema origin format sourceSha256 sourceBytes atomCount bondCount chemistryVerified coordinatesAltered generated experimentalStructureClaim units limitations'.split()
GENERATED_FIELDS = 'schema origin generated method engineVersion seed maxIterations threads hydrogens minimization sourceRevision sourceSha256 outputSha256 format canonicalIsomericSmiles chemicalIdentityRoundtripVerified experimentalStructureClaim limitations sourceContext sourceContextSha256'.split()


def _js_integer(value):
    """JSON numbers have JS Number semantics; booleans are never numbers."""
    return type(value) in (int, float) and math.isfinite(value) and value == int(value)


def _utf16_length(value):
    """Match JavaScript String.length; UTF-8 byte limits remain independent."""
    return len(value.encode('utf-16-le')) // 2


def _same_json(left, right):
    """Type-sensitive structural equality without Python bool/int coercion."""
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def validate_3d_coordinates(payload):
    """Structural integrity only. Imported generation claims are never authenticated."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_METADATA_BYTES:
        raise ValueError('Coordinate metadata exceeds its byte limit.')
    r = _json(payload.decode('utf-8'))
    with_h = isinstance(r, dict) and r.get('schema') == 'nivram-png-coordinates/6'
    _keys(r, THREE_D_FIELDS + ['generatedHydrogensJson', 'generatedHydrogensSha256'] if with_h else THREE_D_FIELDS)
    if (r['schema'] not in ('nivram-png-coordinates/2','nivram-png-coordinates/3','nivram-png-coordinates/4','nivram-png-coordinates/5','nivram-png-coordinates/6') or r['producer'] != 'nivraM'
        or not _js_integer(r['dimension']) or r['dimension'] != 3
        or not isinstance(r['version'], str) or not _VERSION.fullmatch(r['version'])):
        raise ValueError('Unsupported 3D coordinate metadata contract.')
    def need(ok):
        if not ok: raise ValueError('Invalid 3D coordinate metadata.')
    def sha(text, digest, bound):
        need(isinstance(text, str) and isinstance(digest, str))
        raw = text.encode('utf-8')
        need(0 < len(raw) <= bound and re.fullmatch('[a-f0-9]{64}', digest) is not None and hashlib.sha256(raw).hexdigest() == digest)
        return raw
    raw = sha(r['molText'], r['molSha256'], 262144)
    need(_js_integer(r['molBytes']) and r['molBytes'] == len(raw))
    sha(r['provenanceJson'], r['provenanceSha256'], 131072)
    p = _json(r['provenanceJson'])
    radical = r['schema'] == 'nivram-png-coordinates/5'
    need(not radical or r['recordedOrigin'] == 'supplied_coordinates')
    native = r['schema'] == 'nivram-png-coordinates/4'
    need(not native or r['recordedOrigin'] == 'supplied_coordinates' and r['format'] == 'MOL V3000')
    need(not with_h or r['recordedOrigin'] == 'generated_coordinates')
    need(r['schema'] == ('nivram-png-coordinates/6' if with_h else 'nivram-png-coordinates/5' if radical else 'nivram-png-coordinates/4' if native else 'nivram-png-coordinates/3' if r['recordedOrigin'] == 'supplied_coordinates' and r['format'] == 'MOL V3000' else 'nivram-png-coordinates/2'))
    generated = r['recordedOrigin'] == 'generated_coordinates'
    need(generated or r['recordedOrigin'] == 'supplied_coordinates')
    _keys(p, GENERATED_FIELDS if generated else SUPPLIED_FIELDS + ['validation'] if native else SUPPLIED_FIELDS)
    need(p['origin'] == r['recordedOrigin'] and p['format'] == r['format'])
    need(isinstance(p['limitations'], list) and 1 <= len(p['limitations']) <= 32)
    for value in p['limitations']:
        need(isinstance(value, str) and _utf16_length(value) <= 2048 and not re.search(r'[\x00-\x1f\x7f]', value))
    need(type(p['generated']) is bool and p['generated'] == generated and p['experimentalStructureClaim'] is False)
    if generated:
        from .conformer_context import checked_context
        need(r['format'] == 'MOL V3000' and r['units'] == 'angstrom')
        for key in ('method','engineVersion','hydrogens','minimization','canonicalIsomericSmiles'):
            need(isinstance(p[key], str) and 0 < _utf16_length(p[key]) <= (16384 if key == 'canonicalIsomericSmiles' else 512) and not re.search(r'[\x00-\x1f\x7f]', p[key]))
        need(p['schema'] == 'nivram-generated-3d/1' and type(p['chemicalIdentityRoundtripVerified']) is bool)
        for key in ('seed','maxIterations','threads','sourceRevision'):
            need(_js_integer(p[key]) and 0 <= p[key] <= 2147483647)
        need(isinstance(p['sourceSha256'], str) and re.fullmatch('[a-f0-9]{64}',p['sourceSha256']) is not None)
        need(p['outputSha256'] == r['molSha256'] and p['sourceContextSha256'] == r['sourceContextSha256'])
        need(_same_json(checked_context(r['sourceContextText'],r['sourceContextSha256']), p['sourceContext']))
        ids = r['sourceAtomIds']
        need(isinstance(ids,list) and 1 <= len(ids) <= 128)
        for aid in ids:
            need(isinstance(aid, str) and 1 <= _utf16_length(aid) <= 80)
            need(not re.search(r'[\x00-\x1f\x7f]',aid))
        need(len(set(ids)) == len(ids))
        if with_h:
            from .generated_hydrogens import validate_attachment
            sha(r['generatedHydrogensJson'], r['generatedHydrogensSha256'], 65536)
            validate_attachment(_json(r['generatedHydrogensJson']), r['molText'], len(ids))
    else:
        need(r['format'] in ('MOL V2000','MOL V3000') and r['units'] == 'MOL-convention/unverified')
        need(all(r[k] is None for k in ('sourceContextText','sourceContextSha256','sourceAtomIds')))
        need(p['schema'] == ('nivram-supplied-3d-view/3' if radical else 'nivram-supplied-3d-view/2' if native else 'nivram-supplied-3d-view/1') and p['sourceSha256'] == r['molSha256'] and _js_integer(p['sourceBytes']) and p['sourceBytes'] == len(raw))
        need(p['chemistryVerified'] is False and p['coordinatesAltered'] is False)
        need(p['units'] == 'MOL convention: angstrom; caller declaration, not independently verified')
        if native:
            # Structural historical evidence only. Viewer must revalidate the
            # exact embedded MOL in a fresh native worker before adoption.
            from .supplied_validation import _validation
            evidence = p['validation']
            need(isinstance(evidence, dict))
            smiles = evidence.get('engineDerivedIsomericSmiles')
            need(isinstance(smiles, str) and 1 <= len(smiles) <= 8192 and re.fullmatch(r'[!-~]+', smiles) is not None)
            need(_same_json(evidence, _validation(smiles)))
        atoms, bonds, has_radicals = _strict_supplied(r['molText'], allow_native_declarations=native, allow_radicals=radical, include_radical_presence=True)
        need(has_radicals == radical)
        need(r['format'] == ('MOL V3000' if r['molText'].splitlines()[3].endswith('V3000') else 'MOL V2000'))
        need(_js_integer(p['atomCount']) and _js_integer(p['bondCount']) and p['atomCount'] == atoms and p['bondCount'] == bonds)
    return r, p


def _strict_supplied(text, *, allow_native_declarations=False, allow_radicals=False, include_radical_presence=False):
    def need(ok):
        if not ok: raise ValueError('Unsupported supplied MOL metadata.')
    def integer(value):
        need(re.fullmatch(r'\s*\d+\s*',value) is not None)
        return int(value)
    def zero(value):
        return not value.strip() or all(re.fullmatch('0+',v) for v in value.split())
    need(all(c in '\t\r\n' or 32 <= ord(c) <= 126 for c in text) and not re.search(r'\r(?!\n)',text))
    lines = re.split(r'\r?\n',text)
    need(len(lines)>=5 and lines[1][20:22]=='3D' and '$$$$' not in text)
    count=lines[3]
    if count.endswith('V3000'):
        # Reuse the native recovery preflight grammar, without invoking RDKit.
        # Legacy records preserve CHG/MASS/maps only. Schema4 may retain bounded
        # CFG/VAL as historical declarations, requiring fresh viewer native validation.
        from .coordinate_recovery import _v3000
        if lines[-1] == '': lines = lines[:-1]
        need(all(len(line)<=512 and not re.search(r'[\x00-\x1f]',line) for line in lines))
        need(bool(lines[-1]))
        atoms,bonds = _v3000('\n'.join(lines), allow_radicals=allow_radicals)
        need(allow_native_declarations or all(set(a[3]) <= ({'CHG','MASS','RAD'} if allow_radicals else {'CHG','MASS'}) for a in atoms) and all(not b[3] for b in bonds))
        # Match canonical map spelling in the JS declaration grammar.
        need(all(re.fullmatch(r'(0|[1-9]\d{0,5})',line.strip().split()[7]) for line in lines[7:7+len(atoms)]))
        counts = (len(atoms),len(bonds))
        return (*counts, any('RAD' in a[3] for a in atoms)) if include_radical_presence else counts
    need(len(count)>=39 and count[33:].strip()=='V2000')
    na,nb=integer(count[:3]),integer(count[3:6])
    need(1<=na<=256 and nb<=512 and zero(count[6:30]) and integer(count[30:33])==999 and len(lines)>=5+na+nb)
    elements=set('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og'.split())
    for line in lines[4:4+na]:
        need(len(line)>=34 and line[30]==' ' and line[31:34].strip() in elements and zero(line[34:36]) and zero(line[39:]))
        code=integer(line[36:39]) if line[36:39].strip() else 0
        need(code in (0,1,2,3,5,6,7) and (code==0 or len(line)>=39))
        for field in (line[:10],line[10:20],line[20:30]):
            need(re.fullmatch(r'\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*',field) is not None)
            need(abs(float(field))<=10000)
    edges=set()
    for line in lines[4+na:4+na+nb]:
        need(len(line)>=9)
        a,b,order=integer(line[:3]),integer(line[3:6]),integer(line[6:9]);edge=tuple(sorted((a,b)))
        need(1<=a<=na and 1<=b<=na and a!=b and order in (1,2,3,4) and zero(line[9:]) and edge not in edges)
        edges.add(edge)
    # Declaration-only V2000 subset; inline mass differences stay unsupported.
    # M CHG supersedes the whole charge channel. M ISO is absolute mass only.
    cursor=4+na+nb
    assigned={'CHG':set(),'ISO':set(),'RAD':set()}
    while cursor<len(lines) and lines[cursor]!='M  END':
        line=lines[cursor];cursor+=1
        need(re.match(r'^M  (CHG|ISO|RAD)',line) is not None and len(line)>=9)
        kind=line[3:6];need(kind!='RAD' or allow_radicals);n=integer(line[6:9])
        need(1<=n<=8 and len(line)==9+8*n)
        for j in range(n):
            atom=integer(line[9+8*j:13+8*j]);raw=line[13+8*j:17+8*j]
            need(re.fullmatch(r' *-?[0-9]+ *',raw) is not None)
            value=int(raw)
            need(1<=atom<=na and atom not in assigned[kind] and (-8<=value<=8 if kind=='CHG' else 0<=value<=3 if kind=='RAD' else 1<=value<=400))
            assigned[kind].add(atom)
    need(cursor<len(lines) and lines[cursor]=='M  END' and all(not v.strip() for v in lines[cursor+1:]))
    return (na,nb,bool(assigned['RAD'])) if include_radical_presence else (na,nb)
