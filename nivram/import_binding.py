"""Data-only source import association, not independent chemical verification."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import secrets
from .import_protocol import MAX_SOURCE_BYTES, MAX_RESULT_BYTES, checked_style, strict_json_loads, validate_prepared_result
from .property_contract import valid_identity, validate_properties

SCHEMA = 'nivram-import/1'
CHILD_SCHEMA = 'nivram-import-child/1'
TRANSPORT = frozenset(('sourceBinding', 'requestId', 'documentRevision'))
FIELDS = {'schema', 'invocationId', 'sourceSha256', 'sourceBytes', 'style'}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)

def same(a, b):
    return canonical(a) == canonical(b)

def require(value):
    if not value: raise ValueError('Invalid source import association.')

def capture(source, style, invocation_id=None):
    require(isinstance(source, str) and 0 < len(source) <= MAX_SOURCE_BYTES)
    raw = source.encode('utf-8')
    require(0 < len(raw) <= MAX_SOURCE_BYTES)
    return {'schema': CHILD_SCHEMA, 'invocationId': invocation_id or secrets.token_hex(16),
            'sourceSha256': hashlib.sha256(raw).hexdigest(), 'sourceBytes': len(raw),
            'style': copy.deepcopy(checked_style(style))}

def validate_binding(binding, source, style):
    require(isinstance(binding, dict) and set(binding) == FIELDS)
    require(isinstance(binding['invocationId'], str) and re.fullmatch('[0-9a-f]{32}', binding['invocationId']) is not None)
    require(type(binding['sourceBytes']) is int)
    require(same(binding, capture(source, style, binding['invocationId'])))
    return copy.deepcopy(binding)

def validate_receipt(receipt, expected, result):
    require(isinstance(receipt, dict) and set(receipt) == FIELDS | {'outputIdentity', 'outputGraphJson'})
    require(same({k: receipt[k] for k in FIELDS}, expected))
    identity, witness = receipt['outputIdentity'], receipt['outputGraphJson']
    valid_identity(identity, None)
    require(isinstance(witness, str) and len(witness) <= MAX_RESULT_BYTES)
    require(same(strict_json_loads(witness), result['graph']))
    require(witness == canonical(result['graph']))
    require(hashlib.sha256(witness.encode('utf-8')).hexdigest() == identity['graphHash'])
    require(hashlib.sha256(identity['smiles'].encode('utf-8')).hexdigest() == identity['structureHash'])
    validate_prepared_result(result, mode='import')
    validate_properties(result['scene']['properties'], result['graph'], expected_identity=identity)
    require(same(result['scene']['style'], expected['style']))
    return copy.deepcopy(receipt)

def begin_http(data):
    require(isinstance(data, dict))
    if not TRANSPORT.intersection(data): return None
    require(set(data) <= {'source', 'style'} | TRANSPORT and 'source' in data)
    require(data.get('sourceBinding') == SCHEMA)
    require(isinstance(data.get('requestId'), str) and re.fullmatch('[0-9a-f]{32}', data['requestId']) is not None)
    require(type(data.get('documentRevision')) is int and 0 <= data['documentRevision'] <= 2**53 - 1)
    # Validate before dispatch; preserve exact submitted source bytes.
    capture(data['source'], data.get('style'))
    return copy.deepcopy(data)

def finish_http(request, admitted):
    result, receipt = admitted
    return {'schema': SCHEMA, 'action': 'import', 'requestId': request['requestId'],
            'documentRevision': request['documentRevision'],
            'source': {'sha256': receipt['sourceSha256'], 'bytes': receipt['sourceBytes']},
            'style': copy.deepcopy(receipt['style']), 'outputIdentity': copy.deepcopy(receipt['outputIdentity']),
            'outputGraphJson': receipt['outputGraphJson'], **copy.deepcopy(result)}
