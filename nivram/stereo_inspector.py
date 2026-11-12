"""Read-only, engine-qualified stereo perception of a copied 2D graph.

Never enumerates stereoisomers, changes coordinates or adopts an assignment into
an editor document. A CIP descriptor labels an already specified configuration.
"""
from __future__ import annotations
import copy
from rdkit import Chem, rdBase
from rdkit.Chem import rdCIPLabeler
from .chemistry import ChemistryError, graph_to_mol
from .properties import input_identity

SCHEMA = 'nivram-stereo-inspector/1'
MAX_CIP_ITERATIONS = 100000


def inspect_stereochemistry(graph: dict, document_revision=None) -> dict:
    snapshot = copy.deepcopy(graph)
    mol = graph_to_mol(snapshot, allow_empty=True)
    identity = input_identity(snapshot, mol, document_revision)
    warnings = [
        'RDKit potential-stereo perception is engine-dependent, not an exhaustive stereochemistry or experimental identity check.',
        'Only tetrahedral R/S and double-bond E/Z labels are displayed. Unspecified sites are possibilities, not assigned isomers or a claim of a racemate.',
    ]
    # Normal validation can remove redundant or inapplicable declarations. Make
    # that visible instead of presenting a cleaned-away flag as full validation.
    if (any(a.get('chiralTag', 0) and not int(m.GetChiralTag()) for a, m in zip(snapshot['atoms'], mol.GetAtoms()))
            or any(b.get('stereo', 0) and not int(m.GetStereo()) for b, m in zip(snapshot['bonds'], mol.GetBonds()))):
        warnings.append('Some supplied stereo declarations were not retained by normal chemistry validation. Review the source; the drawing was not changed.')
    # Atom maps are identifiers, not isotopic or chemical distinctions. Retain
    # original IDs and identity in the response, but do not rank by map labels.
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    for item in (*mol.GetAtoms(), *mol.GetBonds()):
        if item.HasProp('_CIPCode'):
            item.ClearProp('_CIPCode')
    try:
        records = list(Chem.FindPotentialStereo(mol, cleanIt=False, flagPossible=True))
        rdCIPLabeler.AssignCIPLabels(mol, maxRecursiveIterations=MAX_CIP_ITERATIONS)
    except Exception as exc:
        raise ChemistryError('The local stereochemistry inspector could not complete within its supported engine limits. No result was adopted; the drawing is unchanged.') from exc
    sites = []
    unsupported = 0
    for record in records:
        stereo_type, specified = str(record.type), str(record.specified)
        index = int(record.centeredOn)
        if stereo_type == 'Atom_Tetrahedral':
            kind, source, item, allowed = 'atom', snapshot['atoms'], mol.GetAtomWithIdx(index), {'R', 'S'}
        elif stereo_type == 'Bond_Double':
            kind, source, item, allowed = 'bond', snapshot['bonds'], mol.GetBondWithIdx(index), {'E', 'Z'}
        else:
            unsupported += 1
            continue
        if not 0 <= index < len(source):
            raise ChemistryError('The stereo result could not be mapped to the source drawing.')
        label = item.GetProp('_CIPCode') if item.HasProp('_CIPCode') else None
        cumulated = kind == 'bond' and any(
            other.GetIdx() != index and other.GetBondType() == Chem.BondType.DOUBLE
            for atom in (item.GetBeginAtom(), item.GetEndAtom()) for other in atom.GetBonds())
        if cumulated:
            status, reason, label = 'unsupported', 'unsupported_type', None
        elif specified in ('Unspecified', 'Unknown'):
            status, reason, label = 'unresolved', specified.lower(), None
        elif specified == 'Specified' and label in allowed:
            status, reason = 'assigned', 'specified'
        else:
            status, reason, label = 'unsupported', 'unsupported_label', None
        sites.append({'kind': kind, 'id': source[index]['id'], 'index': index,
                      'status': status, 'label': label, 'reason': reason})
    if unsupported:
        warnings.append('Additional potential stereo elements are outside the supported tetrahedral/double-bond inspector and are not localized.')
    return {'schema': SCHEMA, 'status': 'empty' if not mol.GetNumAtoms() else 'inspected',
            'inputIdentity': identity,
            'engine': {'name': 'RDKit', 'version': rdBase.rdkitVersion,
                       'method': 'FindPotentialStereo + rdCIPLabeler.AssignCIPLabels'},
            'sites': sites, 'unsupportedCount': unsupported,
            'transformations': [], 'warnings': warnings}


def inspect_request(data):
    """Match one explicitly requested inspection without granting any mutation."""
    import re
    if (not isinstance(data, dict) or set(data) != {'graph', 'documentRevision', 'requestId'}
            or type(data['documentRevision']) is not int or not 0 <= data['documentRevision'] <= 9007199254740991
            or not isinstance(data['requestId'], str) or re.fullmatch(r'[0-9a-f]{32}', data['requestId']) is None):
        raise ChemistryError('Invalid stereochemistry inspection request.')
    return {**inspect_stereochemistry(data['graph'], data['documentRevision']), 'requestId': data['requestId']}
