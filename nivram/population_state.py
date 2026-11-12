"""Explicit supplied 2D state preparation; no structure or thermodynamics inference.

The parent owns bounded request identities/cancellation. Chemistry runs only in
the protected import worker, after its OS quotas have been installed.
"""
from __future__ import annotations
import hashlib
import json
import re
import threading
import time

from .import_protocol import DEFAULT_STYLE, checked_style, strict_json_loads
from .worker_processes import CancellationToken, WorkerCancelled

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_HISTORY = 128
HISTORY_SECONDS = 120.0
REQUEST_KEYS = {'schemaVersion', 'requestId', 'documentRevision', 'graphHash', 'modelText',
                'modelInputHash', 'structuresText', 'structuresInputHash', 'stateId', 'style'}
RECEIPT_KEYS = {'schemaVersion', 'requestId', 'documentRevision', 'graphHash', 'modelInputHash',
    'structuresInputHash', 'modelSignature', 'stateId', 'structureId', 'referenceStateId', 'source',
    'referenceSource', 'coreAtomMaps', 'formalCharge', 'relativeProtons', 'observedHydrogenDifference',
    'identity', 'referenceIdentity', 'depiction', 'modelApplicability', 'thermodynamics',
    'conformerGeometry', 'limitations'}
FLAGS = {'depiction': '2d', 'modelApplicability': 'unverified',
         'thermodynamics': 'supplied; not independently validated', 'conformerGeometry': 'not adopted'}
LIMITATIONS = [
    'Explicit whole-drawing replacement with a supplied 2D depiction; model applicability to the current drawing remains unverified.',
    'Mapped core, charge, hydrogen stoichiometry and supported chemical identity were checked; assignment of energies to structures was not independently validated.',
    'This operation does not adopt or validate a 3D conformer geometry.',
]
_HASH = re.compile('[a-f0-9]{64}\\Z')
_ID = re.compile('[A-Za-z0-9_-]{8,80}\\Z')
_ERRORS = {
    'invalid_input': (422, 'Supply a bounded state request with matching model, structure-file and drawing-snapshot identifiers.'),
    'state_binding': (422, 'The structure file must match this exact model and contain the selected and reference states.'),
    'state_structure': (422, 'Supply one supported 2D molecular structure in its explicitly declared SMILES or MOL format.'),
    'state_mapping': (422, 'Each declared core atom must map once, preserve its element and isotope, and include every non-hydrogen or isotopic hydrogen atom.'),
    'state_stoichiometry': (422, 'The supplied structures do not match the model charges or relative hydrogen stoichiometry.'),
    'state_identity': (422, 'Preparation could not preserve the supplied supported chemical identity, atom maps or stereochemistry.'),
    'busy': (429, 'Another population-state preparation is running. Cancel it or wait for completion.'),
    'history_full': (429, 'The bounded recent state-request registry is full. Try again shortly.'),
    'reused_request': (409, 'This state-request identifier was already used. Start a new request.'),
    'worker_failed': (502, 'The state-preparation worker returned an inconsistent response. The drawing is unchanged.'),
}


class PopulationStateError(ValueError):
    def __init__(self, code):
        self.code = code
        self.http_status, message = _ERRORS[code]
        super().__init__(message)


def _require(value, code='invalid_input'):
    if not value:
        raise PopulationStateError(code)


def _text(value, maximum=8192):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and '\0' not in value)
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        raise PopulationStateError('invalid_input') from None


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _request_id(data, keys):
    _require(isinstance(data, dict) and set(data) == keys
             and type(data['schemaVersion']) is int and data['schemaVersion'] == 1
             and isinstance(data['requestId'], str) and _ID.fullmatch(data['requestId']))
    return data['requestId']


def validate_request(data):
    _request_id(data, REQUEST_KEYS)
    _require(type(data['documentRevision']) is int and 0 <= data['documentRevision'] < 2**53
             and _hash(data['graphHash']) and _hash(data['modelInputHash']) and _hash(data['structuresInputHash']))
    _text(data['stateId'])
    for key, digest in (('modelText', 'modelInputHash'), ('structuresText', 'structuresInputHash')):
        _text(data[key], MAX_FILE_BYTES)
        raw = data[key].encode('utf-8')
        _require(len(raw) <= MAX_FILE_BYTES and hashlib.sha256(raw).hexdigest() == data[digest])
    _require(isinstance(data['style'], dict) and not set(data['style']) - set(DEFAULT_STYLE))
    result = {**data, 'style': checked_style(data['style'])}
    _require(len(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')) <= MAX_REQUEST_BYTES)
    return result


def validate_receipt(receipt, graph):
    """Strict parent-side data checks, with no molecule parsing or model claims."""
    _require(isinstance(receipt, dict) and set(receipt) == RECEIPT_KEYS)
    _require(type(receipt['schemaVersion']) is int and receipt['schemaVersion'] == 1
             and isinstance(receipt['requestId'], str) and _ID.fullmatch(receipt['requestId'])
             and type(receipt['documentRevision']) is int and 0 <= receipt['documentRevision'] < 2**53)
    for key in ('graphHash', 'modelInputHash', 'structuresInputHash', 'modelSignature'):
        _require(_hash(receipt[key]))
    for key in ('stateId', 'structureId', 'referenceStateId'):
        _text(receipt[key])
    for key in ('source', 'referenceSource'):
        row = receipt[key]
        _require(isinstance(row, dict) and set(row) == {'format', 'sha256'}
                 and row['format'] in ('smiles', 'mol') and _hash(row['sha256']))
    for key in ('identity', 'referenceIdentity'):
        row = receipt[key]
        _require(isinstance(row, dict) and set(row) == {'canonicalIsomericSmiles', 'structureHash'} and _hash(row['structureHash']))
        _text(row['canonicalIsomericSmiles'], 32768)
        _require(hashlib.sha256(row['canonicalIsomericSmiles'].encode()).hexdigest() == row['structureHash'])
    for key in ('formalCharge', 'relativeProtons', 'observedHydrogenDifference'):
        _require(type(receipt[key]) is int and abs(receipt[key]) <= 1_000_000_000)
    _require(all(receipt[key] == value for key, value in FLAGS.items()) and receipt['limitations'] == LIMITATIONS)
    rows = receipt['coreAtomMaps']
    _require(isinstance(rows, list) and 1 <= len(rows) <= 512)
    atoms = {atom['id']: atom for atom in graph['atoms']}
    seen_ids, seen_maps, seen_graph = set(), set(), set()
    for row in rows:
        _require(isinstance(row, dict) and set(row) == {'atomId', 'mapNum', 'graphAtomId', 'element', 'isotope'})
        _text(row['atomId'])
        _text(row['graphAtomId'], 80)
        _require(row['atomId'] not in seen_ids and type(row['mapNum']) is int and 1 <= row['mapNum'] <= 999999
                 and row['mapNum'] not in seen_maps and row['graphAtomId'] in atoms and row['graphAtomId'] not in seen_graph)
        atom = atoms[row['graphAtomId']]
        _require(row['element'] == atom['symbol'] and type(row['isotope']) is int and row['isotope'] == atom.get('isotope', 0)
                 and row['mapNum'] == atom.get('mapNum', 0))
        seen_ids.add(row['atomId']); seen_maps.add(row['mapNum']); seen_graph.add(row['graphAtomId'])
    for atom in atoms.values():
        _require(atom['id'] in seen_graph or atom['symbol'] == 'H' and atom.get('isotope', 0) == 0 and atom.get('mapNum', 0) == 0)
    _require(sum(atom.get('charge', 0) for atom in atoms.values()) == receipt['formalCharge'])
    return receipt


def _structures(text, model, model_hash):
    value = strict_json_loads(text)
    _require(isinstance(value, dict) and set(value) == {'schemaVersion', 'fileKind', 'modelInputHash', 'structures'}
             and type(value['schemaVersion']) is int and value['schemaVersion'] == 1
             and value['fileKind'] == 'nivram-population-state-structures' and value['modelInputHash'] == model_hash, 'state_binding')
    rows = value['structures']
    _require(isinstance(rows, list) and 1 <= len(rows) <= 4096, 'state_binding')
    states, result = {state.state_id: state for state in model.states}, {}
    for row in rows:
        _require(isinstance(row, dict) and set(row) == {'stateId', 'structureId', 'format', 'source', 'coreAtomMaps'}, 'state_binding')
        _text(row['stateId']); _text(row['structureId'])
        _require(row['stateId'] in states and row['stateId'] not in result
                 and row['structureId'] == states[row['stateId']].structure_id, 'state_binding')
        _require(row['format'] in ('smiles', 'mol'), 'state_structure')
        _text(row['source'], MAX_FILE_BYTES)
        maps = row['coreAtomMaps']
        _require(isinstance(maps, list) and 1 <= len(maps) <= 512, 'state_mapping')
        labels, numbers = set(), set()
        for item in maps:
            _require(isinstance(item, dict) and set(item) == {'atomId', 'mapNum'}, 'state_mapping')
            _text(item['atomId'])
            _require(item['atomId'] not in labels and type(item['mapNum']) is int and 1 <= item['mapNum'] <= 999999
                     and item['mapNum'] not in numbers, 'state_mapping')
            labels.add(item['atomId']); numbers.add(item['mapNum'])
        _require(labels == set(states[row['stateId']].atom_ids), 'state_mapping')
        result[row['stateId']] = row
    return result


def _prepare_structure(record):
    """Worker-only parsing with explicit-H retention and an identity round trip."""
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    from .chemistry import (import_structure, graph_to_mol, mol_to_graph, export_mol, _check_supported)
    from .import_limits import check_smiles
    source = record['source']
    try:
        if record['format'] == 'mol':
            lines = source.splitlines()
            _require(len(lines) >= 5 and re.search(r'V(?:2000|3000)\s*$', lines[3]), 'state_structure')
            # RDKit may reinterpret a declared 3D block with all-zero Z as 2D.
            # This operation explicitly accepts 2D source, not a projection.
            _require(lines[1][20:22].upper() != '3D' and not re.search(r'\b3D\b', lines[1], re.I), 'state_structure')
            graph = import_structure(source)
            original = Chem.MolFromMolBlock(source, sanitize=True, removeHs=False, strictParsing=True)
        else:
            source = source.strip()
            _require(bool(source) and not re.search(r'\s|[|>]', source), 'state_structure')
            check_smiles(source, 512, 1024)
            parser = Chem.SmilesParserParams()
            parser.allowCXSMILES = False; parser.parseName = False; parser.removeHs = False
            original = Chem.MolFromSmiles(source, parser)
            _require(original is not None and original.GetNumAtoms() > 0, 'state_structure')
            _check_supported(original)
            rdDepictor.Compute2DCoords(original, canonOrient=True, useRingTemplates=True)
            Chem.AssignStereochemistry(original, cleanIt=True, force=True)
            copy = Chem.Mol(original)
            Chem.Kekulize(copy, clearAromaticFlags=True)
            graph = mol_to_graph(copy)
        _require(original is not None, 'state_structure')
        mol = graph_to_mol(graph)
        original_identity = Chem.MolToSmiles(original, canonical=True, isomericSmiles=True)
        _require(Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True) == original_identity, 'state_identity')
        # The existing exporter also verifies configured stereo at saved 2D precision.
        restored = Chem.MolFromMolBlock(export_mol(graph), sanitize=True, removeHs=False, strictParsing=True)
        _require(restored is not None and Chem.MolToSmiles(restored, canonical=True, isomericSmiles=True) == original_identity, 'state_identity')
        mapped = {}
        for atom in graph['atoms']:
            number = atom['mapNum']
            if number:
                _require(number not in mapped, 'state_mapping')
                mapped[number] = atom
        requested = {row['mapNum'] for row in record['coreAtomMaps']}
        _require(set(mapped) == requested, 'state_mapping')
        for atom in graph['atoms']:
            _require(atom['mapNum'] in requested or atom['symbol'] == 'H' and atom['isotope'] == 0 and atom['mapNum'] == 0,
                     'state_mapping')
        core = [{'atomId': row['atomId'], 'mapNum': row['mapNum'], 'graphAtomId': mapped[row['mapNum']]['id'],
                 'element': mapped[row['mapNum']]['symbol'], 'isotope': mapped[row['mapNum']]['isotope']}
                for row in record['coreAtomMaps']]
        unmapped = Chem.Mol(mol)
        for atom in unmapped.GetAtoms():
            atom.SetAtomMapNum(0)
        canonical = Chem.MolToSmiles(unmapped, canonical=True, isomericSmiles=True)
        # Count implicit/bracket H plus explicit H vertices without double counting.
        hydrogen_count = sum(int(atom.GetAtomicNum() == 1) + atom.GetNumImplicitHs() + atom.GetNumExplicitHs()
                             for atom in mol.GetAtoms())
        return {'graph': graph, 'core': core, 'charge': sum(a.GetFormalCharge() for a in mol.GetAtoms()),
                'hydrogens': hydrogen_count, 'identity': {'canonicalIsomericSmiles': canonical,
                    'structureHash': hashlib.sha256(canonical.encode()).hexdigest()}}
    except PopulationStateError:
        raise
    except (ValueError, TypeError, KeyError, RuntimeError, OverflowError):
        raise PopulationStateError('state_structure') from None


def prepare_state(data):
    """Called only inside the quota-protected worker."""
    request = validate_request(data)
    from .population_jobs import _complete_model
    from .scene import render_scene
    try:
        model = _complete_model(request['modelText'])
        records = _structures(request['structuresText'], model, request['modelInputHash'])
    except PopulationStateError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise PopulationStateError('state_binding') from None
    states = {state.state_id: state for state in model.states}
    _require(request['stateId'] in records and model.reference_state_id in records, 'state_binding')
    selected_record, reference_record = records[request['stateId']], records[model.reference_state_id]
    selected = _prepare_structure(selected_record)
    reference = selected if request['stateId'] == model.reference_state_id else _prepare_structure(reference_record)
    state, reference_state = states[request['stateId']], states[model.reference_state_id]
    core = lambda result: {row['atomId']: (row['element'], row['isotope']) for row in result['core']}
    _require(core(selected) == core(reference), 'state_mapping')
    difference = selected['hydrogens'] - reference['hydrogens']
    _require(selected['charge'] == state.formal_charge and reference['charge'] == reference_state.formal_charge
             and difference == state.relative_protons - reference_state.relative_protons, 'state_stoichiometry')
    if state.structure_id == reference_state.structure_id:
        _require(selected['identity'] == reference['identity'], 'state_identity')
    receipt = {key: request[key] for key in ('schemaVersion', 'requestId', 'documentRevision', 'graphHash', 'modelInputHash', 'structuresInputHash', 'stateId')}
    receipt.update(modelSignature=model.signature, structureId=state.structure_id, referenceStateId=model.reference_state_id,
        source={'format': selected_record['format'], 'sha256': hashlib.sha256(selected_record['source'].encode()).hexdigest()},
        referenceSource={'format': reference_record['format'], 'sha256': hashlib.sha256(reference_record['source'].encode()).hexdigest()},
        coreAtomMaps=selected['core'], formalCharge=state.formal_charge, relativeProtons=state.relative_protons,
        observedHydrogenDifference=difference, identity=selected['identity'], referenceIdentity=reference['identity'],
        limitations=list(LIMITATIONS), **FLAGS)
    validate_receipt(receipt, selected['graph'])
    return {'graph': selected['graph'], 'scene': render_scene(selected['graph'], request['style']), 'populationState': receipt}


class PopulationStateJobs:
    """Bounded cancellation identities over the existing owned import service."""
    def __init__(self, preparation):
        self._preparation = preparation
        self._lock = threading.Lock()
        self._closed, self._active, self._history = False, None, {}

    def _purge(self):
        now = time.monotonic()
        self._history = {key: row for key, row in self._history.items() if row[1] > now}

    def snapshot(self):
        with self._lock:
            self._purge()
            return {'acceptingJobs': not self._closed, 'activeJobs': int(self._active is not None),
                    'maximumActiveJobs': 1, 'recentRequestIds': len(self._history)}

    def cancel(self, data):
        request_id = _request_id(data, {'schemaVersion', 'requestId'})
        with self._lock:
            self._purge()
            if self._active and self._active[0] == request_id:
                self._active[1].cancel()
                return {'status': 'cancellation_requested'}
            if request_id in self._history:
                return {'status': self._history[request_id][0]}
            if self._closed:
                return {'status': 'closed'}
            _require(len(self._history) + int(self._active is not None) < MAX_HISTORY, 'history_full')
            self._history[request_id] = ('cancelled', time.monotonic() + HISTORY_SECONDS)
            return {'status': 'cancelled_before_registration'}

    def prepare(self, data):
        request = validate_request(data)
        request_id, token = request['requestId'], CancellationToken()
        with self._lock:
            self._purge()
            if self._closed:
                raise WorkerCancelled('State preparation is closed.')
            if request_id in self._history:
                if self._history[request_id][0] == 'cancelled':
                    raise WorkerCancelled('State preparation was cancelled before registration.')
                raise PopulationStateError('reused_request')
            _require(self._active is None, 'busy')
            _require(len(self._history) < MAX_HISTORY, 'history_full')
            self._active = (request_id, token)
        try:
            result = self._preparation.prepare_population_state(request, cancellation_token=token)
            receipt = result['populationState']
            validate_receipt(receipt, result['graph'])
            _require(all(receipt[key] == request[key] for key in ('schemaVersion', 'requestId', 'documentRevision',
                'graphHash', 'modelInputHash', 'structuresInputHash', 'stateId')), 'worker_failed')
            with self._lock:
                if self._closed or token.cancelled:
                    raise WorkerCancelled('State preparation was cancelled.')
            return result
        finally:
            with self._lock:
                if self._active == (request_id, token):
                    self._active = None
                    self._history[request_id] = ('cancelled' if token.cancelled else 'finished', time.monotonic() + HISTORY_SECONDS)

    def close(self):
        with self._lock:
            self._closed = True
            if self._active:
                self._active[1].cancel()
