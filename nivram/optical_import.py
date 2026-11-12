"""Validate a local optical worker result before offering an editable candidate.

This module does not load a model, decode images or make network requests. The
caller binds inputSha256 to the exact retained PNG/crop and requires review.
"""
from __future__ import annotations

import math
import re

from rdkit import Chem, rdBase

from .chemistry import (BOND_TYPES, MAX_ATOMS, MAX_BONDS, ChemistryError,
                        graph_to_mol, mol_to_graph)
from .import_limits import MolRadicalConsistencyError, check_radical_consistency, UnsupportedMolRadicalError, prepare_mol, check_smiles


MODEL_SHA256 = '32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501'
MAX_OPTICAL_SMILES = 65_536
MAX_OPTICAL_MOLFILE = 1_048_576
_FIELDS = {'schemaVersion', 'method', 'modelSha256', 'inputSha256',
           'smiles', 'molfile', 'confidence'}


class OpticalImportError(ValueError):
    """No accepted document should be changed when optical validation fails."""

    def __init__(self, message: str, *, code: str = 'invalid_prediction'):
        super().__init__(message)
        self.code = code


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise OpticalImportError(f'The recognized {label} is empty or exceeds its input limit.')
    try:
        if len(value.encode('utf-8')) > limit:
            raise OpticalImportError(f'The recognized {label} exceeds its input limit.')
    except UnicodeError as exc:
        raise OpticalImportError(f'The recognized {label} is not valid UTF-8 text.') from exc
    if '\x00' in value:
        raise OpticalImportError(f'The recognized {label} contains a forbidden null character.')
    return value


def _supported(mol: Chem.Mol) -> None:
    if mol.GetNumAtoms() == 0 or mol.GetNumAtoms() > MAX_ATOMS or mol.GetNumBonds() > MAX_BONDS:
        raise OpticalImportError('The recognized structure is empty or exceeds the editor atom/bond limits.')
    if Chem.GetMolSubstanceGroups(mol) or mol.GetStereoGroups():
        raise OpticalImportError('Recognized S-groups or enhanced stereogroups cannot be imported without losing annotations.')
    for atom in mol.GetAtoms():
        # No expansion dictionary is verified here. An alias cannot establish
        # that the depicted atom/group agrees with the separate SMILES.
        if Chem.GetAtomAlias(atom):
            raise OpticalImportError('An unresolved atom abbreviation remains in the recognized structure.',
                                     code='unsupported_abbreviation')
        if atom.HasQuery() or atom.GetAtomicNum() == 0:
            raise OpticalImportError('Unresolved wildcard, R-group or query atoms remain in the recognized structure.')
        if int(atom.GetChiralTag()) not in (0, 1, 2):
            raise OpticalImportError('The recognized atom stereochemistry is unsupported.')
    for bond in mol.GetBonds():
        if bond.HasQuery() or bond.GetBondType() not in BOND_TYPES.values():
            raise OpticalImportError('The recognized structure contains an unsupported or query bond.')
        if (bond.GetStereo() == Chem.BondStereo.STEREOANY
                or bond.GetBondDir() in (Chem.BondDir.UNKNOWN, Chem.BondDir.EITHERDOUBLE)):
            raise OpticalImportError('The recognized structure contains explicitly ambiguous stereochemistry.')


def _assign_stereo(mol: Chem.Mol) -> None:
    specified = {atom.GetIdx() for atom in mol.GetAtoms() if int(atom.GetChiralTag()) in (1, 2)}
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    if any(int(mol.GetAtomWithIdx(index).GetChiralTag()) == 0 for index in specified):
        raise OpticalImportError('A recognized stereochemical annotation cannot be represented consistently.')


def _smiles(source: str) -> Chem.Mol:
    source = source.strip()
    if re.search(r'\s', source) or '|' in source or '>' in source:
        raise OpticalImportError('Recognition must return one plain SMILES record without names, reactions or annotations.')
    check_smiles(source, MAX_ATOMS, MAX_BONDS)
    parser = Chem.SmilesParserParams()
    parser.allowCXSMILES = False
    parser.parseName = False
    parser.removeHs = False
    parser.sanitize = False
    mol = Chem.MolFromSmiles(source, parser)
    if mol is None:
        raise OpticalImportError('The recognized SMILES could not be parsed.')
    _supported(mol)
    Chem.SanitizeMol(mol)
    _assign_stereo(mol)
    return mol


def _molfile(source: str) -> Chem.Mol:
    endings = list(re.finditer(r'(?m)^M  END[ \t]*\r?$', source))
    if len(endings) != 1 or source[endings[0].end():].strip():
        raise OpticalImportError('Recognition must return one complete MOL record without trailing records or metadata.')
    prepared, declarations = prepare_mol(source, MAX_ATOMS, MAX_BONDS)
    mol = Chem.MolFromMolBlock(prepared, sanitize=False, removeHs=False, strictParsing=True)
    if mol is None:
        raise OpticalImportError('The recognized MOL file could not be parsed.')
    Chem.ReapplyMolBlockWedging(mol)
    _supported(mol)
    Chem.SanitizeMol(mol)
    check_radical_consistency(declarations, mol)
    _assign_stereo(mol)
    if not mol.GetNumConformers() or mol.GetConformer().Is3D():
        raise OpticalImportError('The recognized MOL file must contain 2D coordinates.')
    for point in mol.GetConformer().GetPositions():
        if any(not math.isfinite(float(value)) or abs(value) > 10000 for value in point) or abs(point[2]) > 1e-6:
            raise OpticalImportError('Recognized MOL coordinates are nonfinite, out of range or not 2D.')
    return mol


def _identity(mol: Chem.Mol) -> str:
    # Normalize only ordinary explicit hydrogens for comparison. Preserve
    # isotopic and mapped H; neither comparison operand is modified.
    parameters = Chem.RemoveHsParameters()
    parameters.removeIsotopes = False
    parameters.removeMapped = False
    normalized = Chem.RemoveHs(Chem.Mol(mol), parameters)
    return Chem.MolToSmiles(normalized, canonical=True, isomericSmiles=True)


def optical_candidate(result: dict) -> dict:
    """Return one review-only candidate from the pinned worker JSON contract.

    A matching MOL depiction retains its coordinates. Contradictory chemistry
    or an unresolved abbreviation rejects the entire proposal, with no fallback.
    Model confidence is retained as an uncalibrated score, never a probability.
    """
    if not isinstance(result, dict) or set(result) != _FIELDS:
        raise OpticalImportError('The optical worker returned an invalid result contract.')
    if type(result['schemaVersion']) is not int or result['schemaVersion'] != 1 or result['method'] != 'MolScribe':
        raise OpticalImportError('The optical worker method or schema is unsupported.')
    if result['modelSha256'] != MODEL_SHA256:
        raise OpticalImportError('The optical result does not identify the pinned model.')
    if not isinstance(result['inputSha256'], str) or not re.fullmatch('[0-9a-f]{64}', result['inputSha256']):
        raise OpticalImportError('The optical result has an invalid input image hash.')
    confidence = result['confidence']
    if confidence is not None and (type(confidence) not in (int, float) or not 0 <= confidence <= 1 or not math.isfinite(confidence)):
        raise OpticalImportError('The optical model score must be null or a finite number between zero and one.')
    smiles = _text(result['smiles'], 'SMILES', MAX_OPTICAL_SMILES)
    molfile = _text(result['molfile'], 'MOL file', MAX_OPTICAL_MOLFILE)
    warnings = ['Optical recognition can misread atoms, bonds, charges, isotopes and stereochemistry. Compare the candidate with the original image and correct it before accepting.',
                'The model score is uncalibrated; it is not a probability of chemical correctness.']
    try:
        with rdBase.BlockLogs():
            recognized = _smiles(smiles)
            depicted = _molfile(molfile)
            if _identity(depicted) != _identity(recognized):
                raise OpticalImportError('The recognized MOL depiction and SMILES describe different structures.',
                                         code='inconsistent_prediction')
            graph = mol_to_graph(depicted)
            coordinates = 'preserved-from-recognized-molfile'
            # Prefix identifiers so this candidate can coexist with metadata
            # candidates without creating duplicate atom/bond identifiers.
            mapping = {atom['id']: 'optical-1-' + atom['id'] for atom in graph['atoms']}
            for atom in graph['atoms']:
                atom['id'] = mapping[atom['id']]
            for bond in graph['bonds']:
                bond['id'] = 'optical-1-' + bond['id']
                bond['begin'], bond['end'] = mapping[bond['begin']], mapping[bond['end']]
                bond['stereoAtoms'] = [mapping[atom_id] for atom_id in bond.get('stereoAtoms', [])]
            rebuilt = graph_to_mol(graph)
            _supported(rebuilt)
            if _identity(rebuilt) != _identity(recognized):
                raise OpticalImportError('The optical candidate cannot preserve recognized chemical identity or stereochemistry.')
            if len(Chem.GetMolFrags(rebuilt)) > 1:
                warnings.append('The recognition contains disconnected fragments. They are offered together and are not automatically identified as a single compound.')
    except MolRadicalConsistencyError as exc:
        raise OpticalImportError(str(exc), code='inconsistent_mol_radical') from exc
    except UnsupportedMolRadicalError as exc:
        raise OpticalImportError(str(exc), code='unsupported_singlet_radical') from exc
    except OpticalImportError:
        raise
    except (ChemistryError, ValueError, RuntimeError, OverflowError) as exc:
        raise OpticalImportError('The optical result contains malformed or unsupported chemistry: ' + str(exc).split('\n')[0]) from exc
    return {
        'id': 'optical-1', 'status': 'supported', 'kind': 'optical_candidate',
        'graph': graph, 'label': f'Optically recognized structure ({len(graph["atoms"])} atoms)',
        'source': 'optical-recognition', 'requiresReview': True,
        'atomCount': len(graph['atoms']), 'formalCharge': Chem.GetFormalCharge(rebuilt),
        'warnings': warnings, 'unsupportedFeatures': [],
        'recognition': {'method': 'MolScribe', 'modelSha256': MODEL_SHA256,
                        'inputSha256': result['inputSha256'], 'confidence': confidence,
                        'confidenceMeaning': 'uncalibrated model score',
                        'coordinates': coordinates},
    }
