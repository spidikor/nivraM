"""Bounded data validation for preparation results; no chemistry imports.

Chemical validation belongs to the limited child. These checks protect the
parent's data/size boundary and bind duplicated identities without reparsing a
molecule or recalculating descriptors in the parent.
"""
from __future__ import annotations
import copy
import json
import math
import re

MAX_SOURCE_BYTES = 4_000_000
MAX_TRANSPORT_BYTES = 16 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 65_536
DEFAULT_STYLE = {'bondWidth': 4.2, 'labelSize': 36, 'bondSpacing': 0.14, 'labelPadding': 0.04, 'color': True, 'hashBondStyle': 'engine'}
ELEMENTS = frozenset(('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn '
    'Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu '
    'Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm '
    'Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og').split())


def _require(condition):
    if not condition:
        raise ValueError('The preparation data is malformed or outside its supported limits.')


def _number(value, low, high):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high)


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high)


def _text(value, maximum, *, empty=True):
    _require(isinstance(value, str) and (empty or bool(value)) and len(value) <= maximum)


def _tree(value):
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        _require(visited <= 250_000 and depth <= 64)
        if isinstance(item, dict):
            # Metadata keys use the existing document byte boundary; avoid a
            # narrower arbitrary key-length/count policy than the editor.
            _require(len(item) <= 100_000 and all(isinstance(key, str) for key in item))
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            _require(len(item) <= 100_000)
            pending.extend((child, depth + 1) for child in item)
        elif type(item) in (int, float):
            _require(math.isfinite(item))
        else:
            _require(item is None or type(item) is bool or isinstance(item, str))
    return value


def strict_json_loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    def reject(_):
        raise ValueError('Nonfinite JSON is unsupported.')
    try:
        return _tree(json.loads(text, object_pairs_hook=pairs, parse_constant=reject))
    except (RecursionError, OverflowError) as error:
        raise ValueError('Preparation JSON exceeds its supported limits.') from error


def checked_style(value):
    _require(value is None or isinstance(value, dict))
    _require(value is None or not set(value) - set(DEFAULT_STYLE))
    result = {**DEFAULT_STYLE, **{key: item for key, item in (value or {}).items() if key in DEFAULT_STYLE}}
    for key, low, high in (('bondWidth', .5, 5), ('labelSize', 10, 40), ('bondSpacing', .06, .28), ('labelPadding', 0, .20)):
        _number(result[key], low, high)
    _require(result['labelSize'] == int(result['labelSize']) and type(result['color']) is bool)
    _require(isinstance(result['hashBondStyle'], str) and result['hashBondStyle'] in ('engine', 'spaced'))
    return result


def checked_export(value):
    _require(isinstance(value, dict))
    _integer(value.get('width'), 1, 9999)
    _require(type(value.get('transparentBackground', False)) is bool)
    margin = value.get('margin')
    _require(type(margin) in (int, float) and math.isfinite(margin) and margin >= 0)
    mode = value.get('marginMode')
    _require(mode in ('percent', 'pixels'))
    padding = math.ceil(value['width'] * margin / 100) if mode == 'percent' else math.ceil(margin)
    _require(value['width'] - 2 * padding > 0)
    return copy.deepcopy(value)


def checked_batch_settings(value):
    _require(isinstance(value, dict) and not set(value) - {'style', 'export', 'profile', 'removeChemistryMetadata'})
    style, options = value.get('style', {}), value.get('export', {})
    _require(isinstance(style, dict) and not set(style) - set(DEFAULT_STYLE))
    _require(isinstance(options, dict) and not set(options) - {'width', 'marginMode', 'margin', 'transparentBackground'})
    result = {'style': checked_style(style), 'export': checked_export(
        {'width': 2000, 'marginMode': 'percent', 'margin': 2, **options}), 'profile': None}
    if 'removeChemistryMetadata' in value:
        _require(type(value['removeChemistryMetadata']) is bool)
        result['removeChemistryMetadata'] = value['removeChemistryMetadata']
    profile = value.get('profile')
    if profile is not None:
        _require(isinstance(profile, dict) and set(profile) == {'id', 'revision', 'name'})
        _require(isinstance(profile['id'], str) and re.fullmatch('[0-9a-f]{32}', profile['id']) is not None)
        _integer(profile['revision'], 1, 999999)
        name = profile['name']
        _require(isinstance(name, str) and 1 <= len(name.strip()) <= 80 and not any(ord(c) < 32 for c in name))
        result['profile'] = {**profile, 'name': name.strip()}
    return copy.deepcopy(result)


def _graph(graph):
    _require(isinstance(graph, dict) and isinstance(graph.get('atoms'), list) and isinstance(graph.get('bonds'), list))
    atoms, bonds = graph['atoms'], graph['bonds']
    _require(0 < len(atoms) <= 512 and len(bonds) <= 1024)
    ids, bids, edges = set(), set(), set()
    for atom in atoms:
        _require(isinstance(atom, dict))
        key = atom.get('id')
        _text(key, 80, empty=False)
        _require(key not in ids)
        ids.add(key)
        _require(isinstance(atom.get('symbol'), str) and atom['symbol'] in ELEMENTS)
        for coordinate in ('x', 'y'):
            _number(atom.get(coordinate), -10000, 10000)
        for name, low, high in (('charge', -8, 8), ('isotope', 0, 400), ('explicitHs', 0, 8),
                               ('radicals', 0, 8), ('mapNum', 0, 999999), ('chiralTag', 0, 2)):
            _integer(atom.get(name, 0), low, high)
        for flag in ('noImplicit', 'aromatic'):
            _require(type(atom.get(flag, False)) is bool)
    for bond in bonds:
        _require(isinstance(bond, dict))
        key = bond.get('id')
        _text(key, 80, empty=False)
        begin, end = bond.get('begin'), bond.get('end')
        _require(isinstance(begin, str) and isinstance(end, str) and begin in ids and end in ids and begin != end)
        edge = frozenset((begin, end))
        _require(key not in bids and edge not in edges)
        bids.add(key)
        edges.add(edge)
        order = bond.get('order', 1)
        _require(type(order) in (int, float) and order in (1, 1.5, 2, 3))
        _integer(bond.get('direction', 0), 0, 6)
        _integer(bond.get('stereo', 0), 0, 5)
        refs = bond.get('stereoAtoms', [])
        _require(isinstance(refs, list) and len(refs) in (0, 2)
                 and all(isinstance(ref, str) and ref in ids for ref in refs))
        _require(bond.get('stereo', 0) <= 1 or len(refs) == 2)
    return ids


def _properties(properties, graph, scene):
    from .property_contract import validate_properties
    validate_properties(properties, graph)
    _require(properties['status'] == 'calculated')
    _require(properties['formula'] == scene['formula'] and properties['smiles'] == scene['smiles'])
    mass = properties['molecularWeight']['value']
    _require(scene['mass'] is None if mass is None else scene['mass'] == round(mass, 3))


def validate_prepared_result(value, *, mode):
    _require(mode in ('import', 'batch', 'population-state'))
    keys = {'graph', 'scene'} | ({'project'} if mode == 'batch' else {'populationState'} if mode == 'population-state' else set())
    _require(isinstance(value, dict) and set(value) == keys)
    _tree(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    _require(len(encoded) <= MAX_RESULT_BYTES)
    graph, scene = value['graph'], value['scene']
    ids = _graph(graph)
    scene_keys = {'svg', 'positions', 'transform', 'formula', 'smiles', 'mass', 'properties', 'style', 'width', 'height'}
    _require(isinstance(scene, dict) and set(scene) == scene_keys)
    _text(scene['svg'], 4_000_000, empty=False)
    _require('<svg' in scene['svg'] and '</svg>' in scene['svg'])
    _require(re.search(r'<\s*(?:script|iframe|foreignObject)\b|<!\s*(?:DOCTYPE|ENTITY)\b|\bon[a-z]+\s*=',
                       scene['svg'], re.I) is None)
    for key in ('width', 'height'):
        _integer(scene[key], 1, 6000)
    _require(scene['style'] == checked_style(scene['style']) and set(scene['style']) == set(DEFAULT_STYLE))
    positions = scene['positions']
    _require(isinstance(positions, list) and len(positions) == len(ids))
    found = set()
    for position in positions:
        _require(isinstance(position, dict) and set(position) == {'id', 'x', 'y'})
        key = position['id']
        _require(isinstance(key, str) and key in ids and key not in found)
        found.add(key)
        for coordinate in ('x', 'y'):
            _number(position[coordinate], -1e9, 1e9)
    transform = scene['transform']
    _require(isinstance(transform, dict) and set(transform) == {'x0', 'y0', 'xScale', 'yScale'})
    for number in transform.values():
        _number(number, -1e9, 1e9)
    if scene['mass'] is not None:
        _number(scene['mass'], 0, 1_000_000)
    _properties(scene['properties'], graph, scene)
    if mode == 'population-state':
        from .population_state import validate_receipt
        validate_receipt(value['populationState'], graph)
    if mode == 'batch':
        project = value['project']
        _require(isinstance(project, dict) and project.get('schema') == 'nivram/1'
                 and not set(project) - {'schema', 'version', 'graph', 'style', 'export', 'analysis'})
        _require(project['graph'] == graph and checked_style(project.get('style')) == scene['style'])
        checked_export(project.get('export', {'width': 2000, 'marginMode': 'percent', 'margin': 2}))
        _require(len(json.dumps(project, ensure_ascii=False, allow_nan=False, indent=2).encode()) <= MAX_SOURCE_BYTES)
    return value
