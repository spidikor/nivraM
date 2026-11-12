"""Validated, coordinate-preserving molecule exchange and experimental cleanup.

No structure is standardized, protonated, neutralized, or sent to a remote API.
This module is not an implementation of Chemaxon's layout algorithm.
"""
from __future__ import annotations
import copy
import json
import math
import re
from typing import Any
from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Geometry import Point2D, Point3D
from .import_limits import MolRadicalConsistencyError, RADICAL_CONSISTENCY_MESSAGE, check_radical_consistency, UnsupportedMolRadicalError, SINGLET_MESSAGE, ImportLimitError, prepare_mol, check_smiles

MAX_ATOMS = 512
MAX_BONDS = 1024
MAX_INPUT = 4_000_000
BOND_TYPES = {1.0: Chem.BondType.SINGLE, 2.0: Chem.BondType.DOUBLE,
              3.0: Chem.BondType.TRIPLE, 1.5: Chem.BondType.AROMATIC}

class ChemistryError(ValueError):
    """User-facing validation failure; callers must retain the old document."""

class UnsupportedAtomAnnotationError(ChemistryError):
    """Supplied MOL atom meaning that the editable graph cannot retain."""
    def __init__(self):
        super().__init__('MOL atom aliases, atom values and attachment-point annotations are not supported. '
                         'Import cancelled to avoid losing them. Use a copy with these annotations explicitly '
                         'resolved or removed in the source editor; the original source is unchanged.')

class UnsupportedSingletRadicalError(ChemistryError):
    """Raw MOL singlet declaration cannot be retained by the editable graph."""
    def __init__(self):
        super().__init__(SINGLET_MESSAGE)

class InconsistentMolRadicalError(ChemistryError):
    """Native sanitization changed a positive raw radical declaration."""
    def __init__(self):
        super().__init__(RADICAL_CONSISTENCY_MESSAGE)

class AtomValenceError(ChemistryError):
    """Typed RDKit rejection, indexed back to submitted atom identities."""
    def __init__(self, atom_ids):
        super().__init__('The proposed edit exceeds the allowed valence at an atom. The preceding drawing is retained.')
        self.diagnostic = {'code': 'atom-valence', 'atomIds': atom_ids}

def _number(value: Any, name: str, limit: float = 10000) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChemistryError(f'{name} must be a finite number.')
    if not math.isfinite(value) or abs(value) > limit:
        raise ChemistryError(f'{name} is outside the supported coordinate range.')
    return float(value)

def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ChemistryError(f'{name} must be an integer from {low} to {high}.')
    return value

def _id(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise ChemistryError('Every atom and bond needs a nonempty string identifier.')
    return value

def _check_supported(mol: Chem.Mol) -> None:
    if mol.GetNumAtoms() > MAX_ATOMS or mol.GetNumBonds() > MAX_BONDS:
        raise ChemistryError(f'This alpha supports at most {MAX_ATOMS} atoms and {MAX_BONDS} bonds.')
    if Chem.GetMolSubstanceGroups(mol):
        raise ChemistryError('Polymer/S-group annotations are not supported in this alpha; import cancelled to avoid losing them.')
    if mol.GetStereoGroups():
        raise ChemistryError('Enhanced AND/OR stereogroups are not supported in this alpha; import cancelled.')
    # Only these proven, unrepresentable MOL properties are checked. Empty
    # text and the attachment-point zero default carry no annotation; retain
    # whitespace text as supplied rather than guessing that it is meaningless.
    for atom in mol.GetAtoms():
        if (any(atom.HasProp(key) and atom.GetProp(key) != ''
                for key in ('molFileAlias', 'molFileValue'))
                or atom.HasProp('molAttchpt') and atom.GetProp('molAttchpt') != '0'):
            raise UnsupportedAtomAnnotationError()
    if any(a.HasQuery() or a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
        raise ChemistryError('Query atoms, R groups and wildcard atoms are not supported in this alpha.')
    if any(b.HasQuery() or b.GetBondType() not in BOND_TYPES.values() for b in mol.GetBonds()):
        raise ChemistryError('Only single, double, triple and aromatic covalent bonds are supported in this alpha.')

def _check_double_bond_geometry(mol: Chem.Mol) -> None:
    """Reject a 2D depiction that contradicts declared cis/trans configuration.

    MOL readers derive double-bond configuration from coordinates. Checking only
    stored stereo flags could therefore export the opposite isomer after a drag.
    """
    conf = mol.GetConformer()
    same_side = {Chem.BondStereo.STEREOZ, Chem.BondStereo.STEREOCIS}
    opposite_side = {Chem.BondStereo.STEREOE, Chem.BondStereo.STEREOTRANS}
    for bond in mol.GetBonds():
        stereo = bond.GetStereo()
        if stereo not in same_side | opposite_side:
            continue
        refs = list(bond.GetStereoAtoms())
        if bond.GetBondType() != Chem.BondType.DOUBLE or len(refs) != 2:
            raise ChemistryError('Defined double-bond stereochemistry has invalid reference atoms.')
        begin = conf.GetAtomPosition(bond.GetBeginAtomIdx())
        end = conf.GetAtomPosition(bond.GetEndAtomIdx())
        dx, dy = end.x - begin.x, end.y - begin.y
        length = math.hypot(dx, dy)
        sides = []
        for origin, reference in zip((begin, end), refs):
            point = conf.GetAtomPosition(reference)
            rx, ry = point.x - origin.x, point.y - origin.y
            scale = length * math.hypot(rx, ry)
            cross = dx * ry - dy * rx
            if scale == 0 or abs(cross) <= 1e-6 * scale:
                raise ChemistryError('The coordinates make specified double-bond stereochemistry ambiguous. '
                                     'Move its reference atoms away from the bond axis; the previous drawing is retained.')
            sides.append(cross > 0)
        if (sides[0] == sides[1]) != (stereo in same_side):
            raise ChemistryError('The coordinates conflict with specified double-bond stereochemistry. '
                                 'This edit was cancelled; preserve the configuration when moving its atoms.')

def _double_stereo_signature(mol: Chem.Mol, atom_ids: list[int]) -> dict:
    """Reference-relative double stereo, independent of atom/bond traversal.

    E/Z and cis/trans use the bond's selected reference ligands. Comparing only
    their enum values would miss a changed ligand and falsely reject an honest
    serializer that selects the other ligand. Normalize each endpoint to its
    lowest original atom index. NONE and ANY are both unspecified in plain
    SMILES; the graph and MOL paths still retain their separate annotations.
    """
    signature = {}
    for bond in mol.GetBonds():
        if bond.GetBondType() != Chem.BondType.DOUBLE:
            continue
        endpoints = [bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()]
        key = tuple(sorted(atom_ids[i] for i in endpoints))
        stereo = bond.GetStereo()
        if stereo in (Chem.BondStereo.STEREONONE, Chem.BondStereo.STEREOANY):
            signature[key] = None
            continue
        references = list(bond.GetStereoAtoms())
        if len(references) != 2:
            raise ChemistryError('Defined double-bond stereochemistry has invalid reference atoms.')
        opposite = int(stereo in (Chem.BondStereo.STEREOE, Chem.BondStereo.STEREOTRANS))
        for endpoint, other, reference in zip(endpoints, reversed(endpoints), references):
            neighbors = [atom_ids[a.GetIdx()] for a in mol.GetAtomWithIdx(endpoint).GetNeighbors()
                         if a.GetIdx() != other]
            if atom_ids[reference] not in neighbors:
                raise ChemistryError('Defined double-bond stereochemistry has disconnected reference atoms.')
            opposite ^= atom_ids[reference] != min(neighbors)
        signature[key] = opposite
    return signature

def _check_smiles_double_stereo(mol: Chem.Mol) -> None:
    """Reject an actual SMILES output that invents or loses double stereo.

    A partially specified polyene can require shared directional edges that
    accidentally specify an intended-unspecified middle double. Comparing two
    canonical SMILES masks that error because both use the same lossy writer.
    The writer's atom-output order lets us compare native annotations directly,
    without changing user atom maps or assuming a canonical atom ordering.
    """
    expected = _double_stereo_signature(mol, list(range(mol.GetNumAtoms())))
    if not expected:
        return
    text = Chem.MolToSmiles(mol, isomericSmiles=True)
    order = json.loads(mol.GetProp('_smilesAtomOutputOrder'))
    parser = Chem.SmilesParserParams()
    parser.removeHs = False
    restored = Chem.MolFromSmiles(text, parser)
    if (restored is None or restored.GetNumAtoms() != mol.GetNumAtoms()
            or sorted(order) != list(range(mol.GetNumAtoms()))
            or _double_stereo_signature(restored, order) != expected):
        raise ChemistryError('The current SMILES serializer cannot preserve this combination of specified and unspecified '
                             'double-bond stereochemistry. The edit was cancelled; the previous drawing is retained.')

def mol_to_graph(mol: Chem.Mol, ids: list[str] | None = None,
                 bond_ids: list[str] | None = None) -> dict:
    _check_supported(mol)
    if not mol.GetNumConformers():
        raise ChemistryError('The molecule has no coordinates.')
    conf = mol.GetConformer()
    ids = ids or [f'a{i}' for i in range(mol.GetNumAtoms())]
    bond_ids = bond_ids or [f'b{i}' for i in range(mol.GetNumBonds())]
    atoms = []
    for i, a in enumerate(mol.GetAtoms()):
        p = conf.GetAtomPosition(i)
        atoms.append({'id': ids[i], 'symbol': a.GetSymbol(), 'x': p.x, 'y': p.y,
                      'charge': a.GetFormalCharge(), 'isotope': a.GetIsotope(),
                      'explicitHs': a.GetNumExplicitHs(), 'noImplicit': a.GetNoImplicit(),
                      'aromatic': a.GetIsAromatic(), 'chiralTag': int(a.GetChiralTag()),
                      'radicals': a.GetNumRadicalElectrons(), 'mapNum': a.GetAtomMapNum()})
    bonds = []
    for i, b in enumerate(mol.GetBonds()):
        bonds.append({'id': bond_ids[i], 'begin': ids[b.GetBeginAtomIdx()],
                      'end': ids[b.GetEndAtomIdx()], 'order': b.GetBondTypeAsDouble(),
                      'direction': int(b.GetBondDir()), 'stereo': int(b.GetStereo()),
                      'stereoAtoms': [ids[j] for j in b.GetStereoAtoms()]})
    return {'atoms': atoms, 'bonds': bonds}

def graph_to_mol(graph: dict, allow_empty: bool = False) -> Chem.Mol:
    if not isinstance(graph, dict) or not isinstance(graph.get('atoms'), list) or not isinstance(graph.get('bonds'), list):
        raise ChemistryError('A molecule must contain atoms and bonds arrays.')
    atoms, bonds = graph['atoms'], graph['bonds']
    if not atoms and not allow_empty:
        raise ChemistryError('The drawing is empty. Draw or import a molecule first.')
    if len(atoms) > MAX_ATOMS or len(bonds) > MAX_BONDS:
        raise ChemistryError('The drawing exceeds this alpha\'s atom/bond limit.')
    rw = Chem.RWMol()
    indices: dict[str, int] = {}
    try:
        for item in atoms:
            aid = _id(item.get('id'))
            if aid in indices:
                raise ChemistryError('Duplicate atom identifiers are not allowed.')
            symbol = item.get('symbol')
            if not isinstance(symbol, str) or symbol not in _ELEMENTS:
                raise ChemistryError('Select a valid chemical element.')
            atom = Chem.Atom(symbol)
            atom.SetFormalCharge(_integer(item.get('charge', 0), 'Charge', -8, 8))
            atom.SetIsotope(_integer(item.get('isotope', 0), 'Isotope', 0, 400))
            atom.SetNumExplicitHs(_integer(item.get('explicitHs', 0), 'Explicit hydrogens', 0, 8))
            for flag in ('noImplicit', 'aromatic'):
                if not isinstance(item.get(flag, False), bool):
                    raise ChemistryError(f'{flag} must be true or false, not a string or number.')
            atom.SetNoImplicit(item.get('noImplicit', False))
            atom.SetIsAromatic(item.get('aromatic', False))
            atom.SetNumRadicalElectrons(_integer(item.get('radicals', 0), 'Radicals', 0, 8))
            atom.SetAtomMapNum(_integer(item.get('mapNum', 0), 'Atom map', 0, 999999))
            tag = _integer(item.get('chiralTag', 0), 'Chirality', 0, 2)
            atom.SetChiralTag(Chem.ChiralType.values[tag])
            indices[aid] = rw.AddAtom(atom)
        seen_ids, seen_edges = set(), set()
        for item in bonds:
            bid = _id(item.get('id'))
            if bid in seen_ids:
                raise ChemistryError('Duplicate bond identifiers are not allowed.')
            seen_ids.add(bid)
            begin, end = item.get('begin'), item.get('end')
            if begin not in indices or end not in indices or begin == end:
                raise ChemistryError('A bond must join two different atoms present in the drawing.')
            edge = frozenset([begin, end])
            if edge in seen_edges:
                raise ChemistryError('Two atoms can only have one bond entry; change its order instead.')
            seen_edges.add(edge)
            order = _number(item.get('order', 1), 'Bond order')
            if order not in BOND_TYPES:
                raise ChemistryError('Unsupported bond order.')
            rw.AddBond(indices[begin], indices[end], BOND_TYPES[order])
            b = rw.GetBondBetweenAtoms(indices[begin], indices[end])
            direction = _integer(item.get('direction', 0), 'Bond direction', 0, 6)
            b.SetBondDir(Chem.BondDir.values[direction])
        mol = rw.GetMol()
        conf = Chem.Conformer(len(atoms)); conf.Set3D(False)
        for i, item in enumerate(atoms):
            conf.SetAtomPosition(i, Point3D(_number(item.get('x'), 'x'), _number(item.get('y'), 'y'), 0.0))
        mol.AddConformer(conf, assignId=True)
        for i, item in enumerate(bonds):
            refs = item.get('stereoAtoms', [])
            if not isinstance(refs, list) or len(refs) not in (0, 2) or any(a not in indices for a in refs):
                raise ChemistryError('Invalid double-bond stereochemistry references.')
            b = mol.GetBondWithIdx(i)
            if refs:
                b.SetStereoAtoms(indices[refs[0]], indices[refs[1]])
            stereo = _integer(item.get('stereo', 0), 'Bond stereochemistry', 0, 5)
            if stereo > 1 and not refs:
                raise ChemistryError('Defined double-bond stereochemistry needs two reference atoms.')
            b.SetStereo(Chem.BondStereo.values[stereo])
        try:
            Chem.SanitizeMol(mol)
        except Chem.AtomValenceException as exc:
            # The Python exception has no atom index. RDKit's typed problem
            # objects do; never derive identities from human-readable messages.
            problems = Chem.DetectChemistryProblems(mol)
            atom_ids = [atoms[problem.GetAtomIdx()]['id'] for problem in problems
                        if problem.GetType() == 'AtomValenceException'
                        and 0 <= problem.GetAtomIdx() < len(atoms)]
            raise AtomValenceError(list(dict.fromkeys(atom_ids))) from exc
        # Sanitization may recompute radicals on atoms with fixed hydrogen counts.
        # Never report a successful edit with a different unpaired-electron count.
        for item, atom in zip(atoms, mol.GetAtoms()):
            if atom.GetNumRadicalElectrons() != item.get('radicals', 0):
                raise ChemistryError(
                    'The requested unpaired-electron count is incompatible with the '
                    'atom’s bonds, charge or fixed hydrogen count. Nothing was changed; '
                    'import a structure with the intended hydrogen and radical state.')
        Chem.AssignChiralTypesFromBondDirs(mol, confId=0, replaceExistingTags=False)
        # Imported SMILES slash flags are a serialization of the explicit graph
        # stereo, not a second authority. A removed reference can leave these
        # flags on an alternative ligand, including edges shared by other E/Z
        # bonds. Let native cleanup validate atom stereo, then restore only the
        # graph's defined configurations on currently stereogenic double bonds.
        for bond in mol.GetBonds():
            if bond.GetBondDir() in (Chem.BondDir.ENDUPRIGHT, Chem.BondDir.ENDDOWNRIGHT):
                bond.SetBondDir(Chem.BondDir.NONE)
        Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
        possible = {info.centeredOn for info in Chem.FindPotentialStereo(mol)
                    if info.type == Chem.StereoType.Bond_Double}
        for i, item in enumerate(bonds):
            if item.get('stereo', 0) > 1 and i in possible:
                bond = mol.GetBondWithIdx(i)
                bond.SetStereoAtoms(*(indices[ref] for ref in item['stereoAtoms']))
                bond.SetStereo(Chem.BondStereo.values[item['stereo']])
        Chem.AssignStereochemistry(mol, cleanIt=False, force=True)
        # Disconnected-component SMILES writing needs regenerated neighbor
        # directions. Do not run cleanIt=True again: shared directions can
        # recreate a graph-undefined center. The output gate below checks that.
        Chem.SetDoubleBondNeighborDirections(mol)
        _check_supported(mol)
        _check_double_bond_geometry(mol)
        _check_smiles_double_stereo(mol)
        return mol
    except ChemistryError:
        raise
    except Exception as exc:
        raise ChemistryError('The edit produced an invalid or unsupported molecular structure: ' + str(exc).split('\n')[0]) from exc

def import_structure(text: str) -> dict:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_INPUT:
        raise ChemistryError('Provide a nonempty SMILES or 2D MOL file smaller than 4 MB.')
    try:
        if '$RXN' in text or text.lstrip().startswith('<') or '>>' in text:
            raise ChemistryError('This alpha imports SMILES and 2D MOL files, not reaction/XML documents.')
        is_mol = 'V2000' in text or 'V3000' in text
        if is_mol:
            endings = list(re.finditer(r'(?m)^M  END[ \t]*\r?$', text))
            if len(endings) != 1 or text[endings[0].end():].strip():
                raise ChemistryError('Import one complete MOL record without SDF records or trailing metadata. '
                                     'Nothing was imported because later records or annotations would be lost.')
            prepared, declarations = prepare_mol(text, MAX_ATOMS, MAX_BONDS)
            mol = Chem.MolFromMolBlock(prepared, sanitize=True, removeHs=False, strictParsing=True)
        else:
            if '|' in text:
                raise ChemistryError('CXSMILES annotations are not supported; import cancelled to avoid losing them.')
            source = text.strip()
            if len(source.splitlines()) != 1 or re.search(r'\s', source):
                raise ChemistryError('Import one SMILES record without a record name or additional records. '
                                     'Dot-separated molecular components within that record are supported.')
            check_smiles(source, MAX_ATOMS, MAX_BONDS)
            parser = Chem.SmilesParserParams()
            parser.allowCXSMILES = False
            parser.parseName = False
            # Keep supplied atoms (and their maps/isotopes), as for MOL input.
            # Bracket H counts remain counts; implicit Hs are not expanded.
            parser.removeHs = False
            mol = Chem.MolFromSmiles(source, parser)
        if mol is None or mol.GetNumAtoms() == 0:
            raise ChemistryError('The structure could not be parsed. Check SMILES syntax or the MOL file.')
        if is_mol:
            check_radical_consistency(declarations, mol)
        _check_supported(mol)
        if mol.GetNumConformers() and mol.GetConformer().Is3D():
            raise ChemistryError('3D files are not imported by this 2D alpha. Supply a 2D MOL file.')
        if not mol.GetNumConformers():
            rdDepictor.Compute2DCoords(mol, canonOrient=True, useRingTemplates=True)
        Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
        if is_mol:
            # Sanitization can retain unknown/wedged MOL annotations only as
            # private parser properties. Restore them before graph conversion.
            Chem.ReapplyMolBlockWedging(mol)
        Chem.Kekulize(mol, clearAromaticFlags=True)
        return mol_to_graph(mol)
    except MolRadicalConsistencyError as exc:
        raise InconsistentMolRadicalError() from exc
    except UnsupportedMolRadicalError as exc:
        raise UnsupportedSingletRadicalError() from exc
    except ChemistryError:
        raise
    except Exception as exc:
        raise ChemistryError('Unable to import this structure: ' + str(exc).split('\n')[0]) from exc

def export_mol(graph: dict) -> str:
    """No Compute2DCoords here: MOL export retains the supplied conformation."""
    mol = graph_to_mol(graph)
    # MOL carries undefined double stereo with CFG=2, not SMILES slash flags.
    # A shared slash edge can otherwise defeat that unknown marker on export.
    mol_for_export = Chem.Mol(mol)
    for bond in mol_for_export.GetBonds():
        if bond.GetBondDir() in (Chem.BondDir.ENDUPRIGHT, Chem.BondDir.ENDDOWNRIGHT):
            bond.SetBondDir(Chem.BondDir.NONE)
    block = Chem.MolToMolBlock(mol_for_export, includeStereo=True, forceV3000=True)
    restored = Chem.MolFromMolBlock(block, sanitize=True, removeHs=False, strictParsing=True)
    if restored is None or Chem.MolToSmiles(restored, isomericSmiles=True) != Chem.MolToSmiles(mol, isomericSmiles=True):
        raise ChemistryError('MOL export could not preserve chemical identity or stereochemistry at the stored coordinate precision. '
                             'The drawing was not changed and no MOL file was prepared.')
    return block

def clean_graph(graph: dict, selected: list[str] | None = None) -> dict:
    """Experimental RDKit layout, not Marvin algorithmic equivalence."""
    mol = graph_to_mol(graph)
    original = Chem.MolToSmiles(mol, isomericSmiles=True)
    all_ids = [a['id'] for a in graph['atoms']]
    if not isinstance(selected, (list, type(None))) or any(a not in all_ids for a in (selected or [])):
        raise ChemistryError('Cleanup selection contains an unknown atom.')
    chosen = set(selected or all_ids)
    fixed = {i: Point2D(a['x'], a['y']) for i, a in enumerate(graph['atoms']) if a['id'] not in chosen}
    try:
        rdDepictor.Compute2DCoords(mol, canonOrient=not bool(fixed), clearConfs=True,
                                  coordMap=fixed, useRingTemplates=True)
        conf = mol.GetConformer()
        # Hard constraint: the selected operation never moves unselected atoms.
        for i, p in fixed.items():
            conf.SetAtomPosition(i, Point3D(p.x, p.y, 0.0))
        out = copy.deepcopy(graph)
        for i, atom in enumerate(out['atoms']):
            if atom['id'] in chosen:
                p = conf.GetAtomPosition(i); atom['x'], atom['y'] = p.x, p.y
        if Chem.MolToSmiles(graph_to_mol(out), isomericSmiles=True) != original:
            raise ChemistryError('Cleanup changed chemical identity and was cancelled.')
        return out
    except ChemistryError:
        raise
    except Exception as exc:
        raise ChemistryError('The selected cleanup could not be completed; the old layout is retained.') from exc

_ELEMENTS = {Chem.GetPeriodicTable().GetElementSymbol(i) for i in range(1, 119)}
