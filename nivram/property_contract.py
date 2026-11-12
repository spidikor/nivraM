"""Data-only report admission; not independent verification of chemical values."""
from __future__ import annotations
import hashlib
import json
import math
import re
from .mass_contract import MASS_MESSAGE, valid_mass_unavailable

SCHEMA = 'nivram-properties/1'
MULTI = 'Multiple disconnected fragments are present. Select an analyte explicitly; a whole-mixture LogP is not calculated.'
CHARGED = 'The as-drawn structure contains charged atoms. Its fragment descriptor is not a validated neutral-species partition coefficient or pH-dependent LogD.'
RADICAL = 'Radical LogP is outside the evaluated application domain.'
NONFINITE = 'The descriptor engine returned a nonfinite result.'
DOMAIN = frozenset(('H', 'C', 'N', 'O', 'F', 'P', 'S', 'Cl', 'Br', 'I'))
DESCRIPTORS = {
    'molecularWeight': {'unit': 'g/mol', 'label': 'Molecular weight / molar mass', 'method': 'RDKit average atomic weights, with explicit isotope labels'},
    'exactMass': {'unit': 'Da', 'label': 'Exact mass', 'method': 'RDKit CalcExactMolWt; explicit isotopes and formal charge retained'},
    'logP': {'unit': 'dimensionless', 'label': 'Calculated LogP - RDKit Wildman-Crippen', 'method': 'Wildman-Crippen atom-fragment estimate', 'applicability': 'As-drawn structure only; not experimental LogP or LogD'},
}

def require(ok):
    if not ok:
        raise ValueError('Invalid or mismatched molecular property report.')

def exact(value, keys):
    return isinstance(value, dict) and set(value) == set(keys)

def text(value, maximum, *, empty=False):
    return isinstance(value, str) and len(value) <= maximum and (empty or bool(value))

def bounded_json(value):
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop(); count += 1
        require(count <= 250000 and depth <= 64)
        if isinstance(item, dict):
            require(all(isinstance(k, str) and k not in ('__proto__', 'constructor', 'prototype') for k in item))
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
        elif type(item) in (int, float):
            try:
                finite = math.isfinite(item)
            except OverflowError:
                finite = False
            require(finite)
        else:
            require(item is None or type(item) is bool or isinstance(item, str))
    require(len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) <= 8 * 1024 * 1024)

def graph_facts(graph):
    require(isinstance(graph, dict) and isinstance(graph.get('atoms'), list) and isinstance(graph.get('bonds'), list))
    atoms, bonds = graph['atoms'], graph['bonds']
    require(len(atoms) <= 512 and len(bonds) <= 1024)
    links = {}
    for a in atoms:
        require(isinstance(a, dict) and text(a.get('id'), 80) and a['id'] not in links and text(a.get('symbol'), 3))
        require(type(a.get('charge', 0)) is int and -8 <= a.get('charge', 0) <= 8)
        require(type(a.get('radicals', 0)) is int and 0 <= a.get('radicals', 0) <= 8)
        links[a['id']] = []
    for b in bonds:
        require(isinstance(b, dict) and isinstance(b.get('begin'), str) and isinstance(b.get('end'), str) and b['begin'] in links and b['end'] in links)
        links[b['begin']].append(b['end']); links[b['end']].append(b['begin'])
    unseen = set(links); fragments = 0
    while unseen:
        fragments += 1; todo = [unseen.pop()]
        while todo:
            for neighbor in links[todo.pop()]:
                if neighbor in unseen:
                    unseen.remove(neighbor); todo.append(neighbor)
    return {'empty': not atoms, 'fragments': fragments, 'charge': sum(a.get('charge', 0) for a in atoms),
            'charged': any(a.get('charge', 0) for a in atoms), 'radicals': any(a.get('radicals', 0) for a in atoms),
            'outside': sorted({a['symbol'] for a in atoms if a['symbol'] not in DOMAIN})}

def valid_identity(identity, revision):
    require(exact(identity, ('graphHash', 'structureHash', 'smiles', 'documentRevision')))
    for key in ('graphHash', 'structureHash'):
        require(isinstance(identity[key], str) and re.fullmatch('[0-9a-f]{64}', identity[key]) is not None)
    require(text(identity['smiles'], 65536, empty=True))
    r = identity['documentRevision']
    require(r is None or type(r) is int or isinstance(r, str) and len(r) <= 120)
    require(type(r) is type(revision) and r == revision)

def validate_properties(report, graph, *, revision=None, expected_identity=None):
    bounded_json(report)
    facts = graph_facts(graph)
    keys = {'status', 'inputIdentity', 'engine', 'calculatedAt', 'transformations', 'formula', 'smiles', 'formalCharge', 'fragmentCount', 'molecularWeight', 'exactMass', 'logP', 'warnings'}
    unavailable = isinstance(report, dict) and 'massUnavailable' in report
    require(exact(report, keys | ({'massUnavailable'} if unavailable else set())))
    identity = report['inputIdentity']; valid_identity(identity, revision)
    encoded = json.dumps(graph, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    require(identity['graphHash'] == hashlib.sha256(encoded).hexdigest())
    require(identity['structureHash'] == hashlib.sha256(identity['smiles'].encode()).hexdigest())
    if expected_identity is not None:
        valid_identity(expected_identity, revision); require(identity == expected_identity)
    empty = facts['empty']
    require(report['status'] == ('empty' if empty else 'calculated'))
    require(text(report['formula'], 65536, empty=empty) and bool(report['formula']) != empty)
    require(text(report['smiles'], 65536, empty=empty) and bool(report['smiles']) != empty and report['smiles'] == identity['smiles'])
    require(type(report['formalCharge']) is int and report['formalCharge'] == facts['charge'])
    require(type(report['fragmentCount']) is int and report['fragmentCount'] == facts['fragments'])
    require(report['transformations'] == [] and type(report['transformations']) is list)
    require(exact(report['engine'], ('name', 'version')) and report['engine']['name'] == 'RDKit' and text(report['engine']['version'], 80))
    require(text(report['calculatedAt'], 80))
    if unavailable:
        require(not empty and valid_mass_unavailable(report['massUnavailable'], graph))
    for key, constants in DESCRIPTORS.items():
        descriptor = report[key]
        require(exact(descriptor, {'value', *constants} | ({'methodVersion', 'status', 'warnings'} if key == 'logP' else set())))
        require(all(descriptor[k] == v for k, v in constants.items()))
        v = descriptor['value']
        if key != 'logP':
            require(v is None if empty or unavailable else type(v) in (int, float) and math.isfinite(v) and v >= 0)
        else:
            require(descriptor['status'] in ('calculated', 'calculated_with_warning', 'not_applicable'))
            require(v is None if descriptor['status'] == 'not_applicable' else type(v) in (int, float) and math.isfinite(v))
            require(text(descriptor['methodVersion'], 80))
    logp = report['logP']; warnings = logp['warnings']
    require(isinstance(warnings, list) and len(warnings) <= 32 and all(text(w, 2000) for w in warnings))
    expected = []
    if facts['fragments'] > 1: expected.append(MULTI)
    # Native sanitization can introduce charge separation (e.g. neutral nitro).
    charged = facts['charged'] or CHARGED in warnings
    if charged: expected.append(CHARGED)
    # graph_to_mol rejects native radical counts differing from source annotations.
    radical = facts['radicals']
    if radical: expected.append(RADICAL)
    if facts['outside']: expected.append('LogP is outside the evaluated element domain: ' + ', '.join(facts['outside']) + '.')
    inapplicable = empty or facts['fragments'] != 1 or radical or bool(facts['outside'])
    if NONFINITE in warnings:
        require(not inapplicable); expected.append(NONFINITE); inapplicable = True
    require(warnings == expected)
    require(logp['status'] == ('not_applicable' if inapplicable else 'calculated_with_warning' if charged else 'calculated'))
    require(report['warnings'] == warnings + ([MASS_MESSAGE] if unavailable else []))
    return report
