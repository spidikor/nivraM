"""Local, nonmutating property and drawing comparison of two explicit snapshots."""
from __future__ import annotations
import copy
import re
from .chemistry import ChemistryError
from .properties import calculate_properties, input_identity
from .property_contract import validate_properties
from .scene import checked_style, render_scene

SCHEMA = 'nivram-comparison/2'


def compare_request(data):
    bound = isinstance(data, dict) and 'sourceBinding' in data
    if (not isinstance(data, dict) or set(data) != ({'requestId', 'pinId', 'documentRevision', 'pinned', 'current'} | ({'sourceBinding'} if bound else set()))
            or bound and data['sourceBinding'] != 'nivram-source/1'
            or type(data['documentRevision']) is not int or not 0 <= data['documentRevision'] <= 9007199254740991
            or any(not isinstance(data[k], str) or re.fullmatch('[0-9a-f]{32}', data[k]) is None for k in ('requestId', 'pinId'))):
        raise ChemistryError('Invalid structure comparison request.')
    snapshot = copy.deepcopy(data)
    results = {}
    # Validate/calculates both sides before producing any result. A render failure
    # is distinct from a chemical failure and never substitutes different geometry.
    for side in ('pinned', 'current'):
        item = snapshot[side]
        if not isinstance(item, dict) or set(item) != {'graph', 'style'}:
            raise ChemistryError('Invalid comparison source.')
        item['style'] = checked_style(item['style'])
        identity = input_identity(item['graph'], document_revision=snapshot['documentRevision'])
        properties = calculate_properties(copy.deepcopy(item['graph']), snapshot['documentRevision'])
        validate_properties(properties, item['graph'], revision=snapshot['documentRevision'], expected_identity=identity)
        results[side] = {'status': properties['status'], 'identity': properties['inputIdentity'],
                         'engine': properties['engine'], 'formula': properties['formula'],
                         'molecularWeight': properties['molecularWeight']['value'],
                         'exactMass': properties['exactMass']['value'],
                         'formalCharge': properties['formalCharge'], 'fragmentCount': properties['fragmentCount'],
                         'logP': {k: properties['logP'][k] for k in ('value', 'status', 'warnings')},
                         'warnings': properties['warnings'],
                         **({'massUnavailable': properties['massUnavailable']}
                            if 'massUnavailable' in properties else {})}
        if bound:
            results[side].update(sourceGraph=copy.deepcopy(item['graph']), sourceIdentity=identity)
    for side in ('pinned', 'current'):
        item = snapshot[side]
        try:
            scene = render_scene(item['graph'], item['style'])
            preview = {'status': 'available', 'scene': scene, 'error': None}
        except ValueError as exc:
            # Current rendering gates raise user-facing ValueErrors (e.g. required
            # Arial/FreeType). Do not misreport a missing preview as a good image.
            preview = {'status': 'unavailable', 'scene': None, 'error': str(exc)[:1000]}
        results[side]['preview'] = preview
    return {'schema': SCHEMA, 'requestId': snapshot['requestId'], 'pinId': snapshot['pinId'],
            'documentRevision': snapshot['documentRevision'], 'transformations': [],
            **({'sourceBinding': 'nivram-source/1'} if bound else {}), **results}
