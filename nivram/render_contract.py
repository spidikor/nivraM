"""Opt-in local response association, not independent science/SVG verification.

The authoritative snapshots never enter transformation or rendering producers.
Persisted scene identities keep a null revision; transport revision is separate.
"""
from __future__ import annotations
import copy
import json
import re
from dataclasses import dataclass
from .properties import input_identity
from .property_contract import bounded_json, validate_properties
from .scene import checked_style

SCHEMA = 'nivram-render/1'
TRANSPORT = frozenset(('sourceBinding', 'requestId', 'documentRevision'))
REQUEST_FIELDS = {
    'render': {'graph', 'style'},
    'clean': {'graph', 'style', 'selected'},
    'reflect': {'graph', 'style', 'selected', 'mode', 'axis'},
    'rotate': {'graph', 'style', 'selected', 'degrees'},
}

def _same(a, b):
    # Exact serialized values, ignoring object order; detect numeric type edits
    # as well as Python's otherwise equal True/1 or signed-zero values.
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)

@dataclass(frozen=True)
class Binding:
    request_id: str
    revision: int
    action: str
    source: dict
    identity: dict
    style: dict

def begin_binding(action, data):
    """Capture before native work; return None for untouched legacy requests."""
    if not isinstance(data, dict):
        raise ValueError('Invalid render request.')
    if not TRANSPORT.intersection(data):
        return None
    if (action not in REQUEST_FIELDS or data.get('sourceBinding') != SCHEMA
            or not isinstance(data.get('requestId'), str)
            or re.fullmatch('[0-9a-f]{32}', data['requestId']) is None
            or type(data.get('documentRevision')) is not int
            or not 0 <= data['documentRevision'] <= 2**53 - 1
            or 'graph' not in data
            or set(data) - (REQUEST_FIELDS[action] | TRANSPORT)):
        raise ValueError('Invalid bound render request.')
    bounded_json(data)
    source = copy.deepcopy({k: v for k, v in data.items() if k not in TRANSPORT})
    style = checked_style(source.get('style'))
    identity = input_identity(copy.deepcopy(source['graph']))
    return Binding(data['requestId'], data['documentRevision'], action, source, identity, style)

def finish_binding(binding, graph, producer):
    """Independently bind exact output before calling a separate-copy producer."""
    snapshot = copy.deepcopy(graph)
    identity = input_identity(copy.deepcopy(snapshot))
    # Carry the original Python hash preimage; clients must not recreate Python
    # number/Unicode serialization when checking the exact returned graph.
    output_json = json.dumps(snapshot, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True, allow_nan=False)
    if binding.action == 'render' and any(identity[k] != binding.identity[k] for k in ('structureHash', 'smiles')):
        raise ValueError('Rendered structure differs from the submitted structure.')
    producer_graph = copy.deepcopy(snapshot)
    producer_style = copy.deepcopy(binding.source.get('style'))
    scene = producer(producer_graph, producer_style)
    if not _same(producer_graph, snapshot) or not _same(producer_style, binding.source.get('style')):
        raise ValueError('The scene producer mutated its input.')
    if not isinstance(scene, dict) or 'properties' not in scene:
        raise ValueError('The bound scene requires molecular properties.')
    validate_properties(scene['properties'], snapshot, expected_identity=identity)
    report = scene['properties']
    mass = report['molecularWeight']['value']
    expected_mass = 0 if report['status'] == 'empty' else None if mass is None else round(mass, 3)
    if (not _same(scene.get('style'), binding.style)
            or scene.get('smiles') != identity['smiles']
            or scene.get('formula') != report['formula']
            or type(scene.get('mass')) is bool
            or scene.get('mass') != expected_mass
            or 'mass' not in scene):
        raise ValueError('The scene does not match its output identity, properties or requested style.')
    return {'schema': SCHEMA, 'requestId': binding.request_id,
            'documentRevision': binding.revision, 'action': binding.action,
            'sourceRequest': copy.deepcopy(binding.source),
            'sourceIdentity': copy.deepcopy(binding.identity), 'outputIdentity': identity,
            'outputGraphJson': output_json, 'graph': snapshot, 'scene': copy.deepcopy(scene)}
