"""Thirty-degree coordinate edits with independently checked stereochemistry.

Positive degrees are counterclockwise in molecular (y-up) coordinates. The
target is the exact supplied atom subset; None/[] means the complete drawing.
"""
from __future__ import annotations

import copy
import math

from rdkit import Chem
from rdkit.Geometry import Point3D

from .chemistry import ChemistryError, export_mol, graph_to_mol


_TETRAHEDRAL = (Chem.ChiralType.CHI_TETRAHEDRAL_CW, Chem.ChiralType.CHI_TETRAHEDRAL_CCW)
_WEDGES = (int(Chem.BondDir.BEGINWEDGE), int(Chem.BondDir.BEGINDASH))


def rotate_graph(graph: dict, *, degrees: int | float, selected: list[str] | None = None) -> dict:
    """Return an independently validated copy; never mutate or regenerate layout.

    Boundary bonds may change drawn length/angle. Invalid stereo geometry or a
    collapsed boundary bond rejects the complete step. Wedged bonds retain
    endpoints, while their direction may be adjusted to preserve configuration.
    """
    if isinstance(degrees, bool) or not isinstance(degrees, (int, float)) or degrees not in (-30, 30):
        raise ChemistryError('Rotate by exactly 30 degrees clockwise or counterclockwise.')
    original = graph_to_mol(graph)
    atom_ids = [atom['id'] for atom in graph['atoms']]
    if selected is not None and (not isinstance(selected, list)
            or any(not isinstance(atom_id, str) or atom_id not in atom_ids for atom_id in selected)
            or len(set(selected)) != len(selected)):
        raise ChemistryError('Rotation selection must contain distinct atom identifiers from the drawing.')
    chosen = set(selected or atom_ids)
    out = copy.deepcopy(graph)
    if len(chosen) < 2:
        return out
    points = [atom for atom in graph['atoms'] if atom['id'] in chosen]
    cx = math.fsum(atom['x'] for atom in points) / len(points)
    cy = math.fsum(atom['y'] for atom in points) / len(points)
    angle = math.radians(degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    target = Chem.Mol(original)
    for index, atom in enumerate(out['atoms']):
        if atom['id'] in chosen:
            dx, dy = atom['x'] - cx, atom['y'] - cy
            atom['x'], atom['y'] = cx + cosine * dx - sine * dy, cy + sine * dx + cosine * dy
        target.GetConformer().SetAtomPosition(index, Point3D(atom['x'], atom['y'], 0.0))

    try:
        positions = {atom['id']: atom for atom in out['atoms']}
        wedge_centers = set()
        for index, entry in enumerate(out['bonds']):
            a, b = positions[entry['begin']], positions[entry['end']]
            if (entry['begin'] in chosen) != (entry['end'] in chosen):
                if math.hypot(a['x'] - b['x'], a['y'] - b['y']) <= 1e-6:
                    raise ChemistryError('Rotation would collapse a selection-boundary bond; the drawing was retained.')
            bond = target.GetBondWithIdx(index)
            if entry.get('direction', 0) not in _WEDGES or bond.GetBeginAtom().GetChiralTag() not in _TETRAHEDRAL:
                continue
            begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            Chem.WedgeBond(bond, begin, target.GetConformer())
            if (bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()) != (begin, end):
                raise ChemistryError('The rotated wedge could not preserve its bond endpoints.')
            entry['direction'] = int(bond.GetBondDir())
            wedge_centers.add(begin)

        expected = Chem.MolToSmiles(original, isomericSmiles=True)
        if Chem.MolToSmiles(graph_to_mol(out), isomericSmiles=True) != expected:
            raise ChemistryError('Rotation could not preserve molecular identity and stereochemistry.')
        if wedge_centers:
            depicted = copy.deepcopy(out)
            for index in wedge_centers:
                depicted['atoms'][index]['chiralTag'] = 0
            if Chem.MolToSmiles(graph_to_mol(depicted), isomericSmiles=True) != expected:
                raise ChemistryError('The rotated wedge geometry is ambiguous; the drawing was retained.')
        # MOL readers infer E/Z and tetrahedral configuration from the depicted
        # geometry. This independently tests the output at export precision.
        export_mol(out)
        return out
    except ChemistryError:
        raise
    except Exception as error:
        raise ChemistryError('Rotation could not preserve stereochemistry; the drawing was retained.') from error
