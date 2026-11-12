"""Offline, nonmutating descriptors of the exact submitted molecular graph.

Calculated descriptors are not experimental measurements or pH-dependent LogD.
No neutralization, fragment selection, tautomerization or cleanup is performed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone

from rdkit import Chem, rdBase
from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors

from .chemistry import graph_to_mol
from .mass_contract import MASS_REASON, MASS_MESSAGE


LOGP_LABEL = 'Calculated LogP - RDKit Wildman-Crippen'
# A conservative application domain, not a claim that all these compounds have
# experimentally accurate LogP predictions. Broader elements need evaluation.
LOGP_ELEMENTS = frozenset((1, 6, 7, 8, 9, 15, 16, 17, 35, 53))


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def input_identity(graph: dict, mol: Chem.Mol | None = None,
                   document_revision: int | str | None = None) -> dict:
    """Separate an exact graph snapshot from its supported chemical identity."""
    if mol is None:
        mol = graph_to_mol(graph, allow_empty=True)
    if document_revision is not None and (
            isinstance(document_revision, bool)
            or not isinstance(document_revision, (str, int))
            or (isinstance(document_revision, str) and len(document_revision) > 120)):
        raise ValueError('The document revision must be an integer or a short string.')
    smiles = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    encoded = json.dumps(graph, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('utf-8')
    return {'graphHash': hashlib.sha256(encoded).hexdigest(),
            'structureHash': hashlib.sha256(smiles.encode('utf-8')).hexdigest(),
            'smiles': smiles, 'documentRevision': document_revision}


def calculate_properties(graph: dict, document_revision: int | str | None = None) -> dict:
    """Return JSON-safe properties and applicability without editing ``graph``."""
    snapshot = copy.deepcopy(graph)
    mol = graph_to_mol(snapshot, allow_empty=True)
    identity = input_identity(snapshot, mol, document_revision)
    empty = mol.GetNumAtoms() == 0
    # RDKit falls back to the integer isotope label when its explicit mass
    # table has no entry. That is not a supported mass calculation. Isotope 0
    # is intentionally excluded: it retains the engine's unlabeled behavior.
    table = Chem.GetPeriodicTable()
    missing = set()
    for atom in mol.GetAtoms():
        isotope = atom.GetIsotope()
        if isotope:
            mass = table.GetMassForIsotope(atom.GetAtomicNum(), isotope)
            if not math.isfinite(mass) or mass <= 0:
                missing.add((atom.GetSymbol(), isotope))
    mass_unavailable = ({'reason': MASS_REASON, 'message': MASS_MESSAGE,
                         'isotopes': [{'symbol': symbol, 'isotope': isotope}
                                      for symbol, isotope in sorted(missing)]}
                        if missing else None)
    fragments = len(Chem.GetMolFrags(mol)) if not empty else 0
    charged = any(atom.GetFormalCharge() for atom in mol.GetAtoms())
    radicals = any(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms())
    outside_domain = sorted({atom.GetSymbol() for atom in mol.GetAtoms()
                             if atom.GetAtomicNum() not in LOGP_ELEMENTS})
    warnings = []
    if fragments > 1:
        warnings.append('Multiple disconnected fragments are present. Select an analyte explicitly; a whole-mixture LogP is not calculated.')
    if charged:
        warnings.append('The as-drawn structure contains charged atoms. Its fragment descriptor is not a validated neutral-species partition coefficient or pH-dependent LogD.')
    if radicals:
        warnings.append('Radical LogP is outside the evaluated application domain.')
    if outside_domain:
        warnings.append('LogP is outside the evaluated element domain: ' + ', '.join(outside_domain) + '.')
    applicable = not empty and fragments == 1 and not radicals and not outside_domain
    logp = None
    if applicable:
        # The tested RDKit binding uses includeHs, not the legacy docstring addHs.
        logp = float(rdMolDescriptors.CalcCrippenDescriptors(mol, includeHs=True, force=True)[0])
        if not math.isfinite(logp):
            logp = None
            applicable = False
            warnings.append('The descriptor engine returned a nonfinite result.')
    return {
        'status': 'empty' if empty else 'calculated',
        'inputIdentity': identity,
        'engine': {'name': 'RDKit', 'version': rdBase.rdkitVersion},
        'calculatedAt': utc_timestamp(),
        'transformations': [],
        'formula': '' if empty else rdMolDescriptors.CalcMolFormula(
            mol, separateIsotopes=True, abbreviateHIsotopes=False),
        'smiles': identity['smiles'],
        'formalCharge': Chem.GetFormalCharge(mol),
        'fragmentCount': fragments,
        'molecularWeight': {
            'value': None if empty or missing else float(Descriptors.MolWt(mol)),
            'unit': 'g/mol', 'label': 'Molecular weight / molar mass',
            'method': 'RDKit average atomic weights, with explicit isotope labels'},
        'exactMass': {
            'value': None if empty or missing else float(rdMolDescriptors.CalcExactMolWt(mol)),
            'unit': 'Da', 'label': 'Exact mass',
            'method': 'RDKit CalcExactMolWt; explicit isotopes and formal charge retained'},
        'logP': {
            'value': logp, 'unit': 'dimensionless', 'label': LOGP_LABEL,
            'method': 'Wildman-Crippen atom-fragment estimate',
            'methodVersion': Crippen.MolLogP.version,
            'status': ('not_applicable' if not applicable else
                       'calculated_with_warning' if charged else 'calculated'),
            'applicability': 'As-drawn structure only; not experimental LogP or LogD',
            'warnings': list(warnings)},
        'warnings': warnings + ([MASS_MESSAGE] if missing else []),
        **({'massUnavailable': mass_unavailable} if missing else {}),
    }


def property_response(data: dict) -> dict:
    """Bind transport to an independent original snapshot; keep legacy reports."""
    from .property_contract import SCHEMA, validate_properties
    import re
    if not isinstance(data, dict):
        raise ValueError('Invalid molecular property request.')
    bound = 'requestId' in data
    if bound and (not isinstance(data['requestId'], str) or re.fullmatch('[0-9a-f]{32}', data['requestId']) is None):
        raise ValueError('Invalid molecular property request identity.')
    snapshot = copy.deepcopy(data.get('graph', {}))
    revision = data.get('documentRevision')
    identity = input_identity(snapshot, document_revision=revision)
    # Do not pass the authoritative snapshot to a potentially mutating producer.
    report = calculate_properties(copy.deepcopy(snapshot), revision)
    validate_properties(report, snapshot, revision=revision, expected_identity=identity)
    if not bound:
        return report
    return {'schema': SCHEMA, 'requestId': data['requestId'], 'sourceGraph': snapshot,
            'sourceIdentity': identity, 'properties': report}
