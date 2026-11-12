"""Coordinate reflection with explicit, independently checked stereo semantics.

Horizontal means left/right (x); vertical means up/down (y). Flip preserves
configuration. Mirror inverts defined tetrahedral centers whose atom IDs are
selected. It does not assign configuration to unspecified centers. No layout,
standardization, protonation, atom reordering or bond reordering is performed.
"""
from __future__ import annotations

import copy

from rdkit import Chem
from rdkit.Geometry import Point3D

from .chemistry import ChemistryError, export_mol, graph_to_mol


_TETRAHEDRAL = (Chem.ChiralType.CHI_TETRAHEDRAL_CW, Chem.ChiralType.CHI_TETRAHEDRAL_CCW)
_WEDGES = (int(Chem.BondDir.BEGINWEDGE), int(Chem.BondDir.BEGINDASH))


def reflect_graph(graph: dict, *, mode: str = 'flip', axis: str = 'horizontal',
                  selected: list[str] | None = None) -> dict:
    """Return a reflected copy, or reject without modifying the input graph.

    None and [] select the whole drawing, as with cleanup. Only the selected
    coordinates move, around their bounding-box midpoint. At a selection
    boundary, the requested tetrahedral configurations remain explicit; an E/Z
    conflict or a depiction that cannot survive MOL export is rejected rather
    than silently changing chemistry or moving unselected atoms.
    """
    if mode not in ('flip', 'mirror') or axis not in ('horizontal', 'vertical'):
        raise ChemistryError('Choose Flip or Mirror and a horizontal or vertical direction.')
    original = graph_to_mol(graph)
    atom_ids = [atom['id'] for atom in graph['atoms']]
    if (selected is not None and (not isinstance(selected, list)
            or any(not isinstance(atom_id, str) or atom_id not in atom_ids for atom_id in selected)
            or len(set(selected)) != len(selected))):
        raise ChemistryError('Reflection selection must contain distinct atom identifiers from the drawing.')
    chosen = set(selected or atom_ids)
    coordinate = 'x' if axis == 'horizontal' else 'y'
    values = [atom[coordinate] for atom in graph['atoms'] if atom['id'] in chosen]
    extent_sum = min(values) + max(values)
    out = copy.deepcopy(graph)
    target = Chem.Mol(original)

    for index, atom in enumerate(out['atoms']):
        if atom['id'] in chosen:
            atom[coordinate] = extent_sum - atom[coordinate]
            desired = target.GetAtomWithIdx(index)
            if mode == 'mirror' and desired.GetChiralTag() in _TETRAHEDRAL:
                desired.InvertChirality()
                # Wedge-only input remains wedge-only: its configuration is
                # encoded below, without inventing a stored atom annotation.
                if atom.get('chiralTag', 0) in (1, 2):
                    atom['chiralTag'] = int(desired.GetChiralTag())
        target.GetConformer().SetAtomPosition(index, Point3D(atom['x'], atom['y'], 0.0))

    try:
        wedge_centers = set()
        for index, entry in enumerate(out['bonds']):
            bond = target.GetBondWithIdx(index)
            if entry.get('direction', 0) not in _WEDGES or bond.GetBeginAtom().GetChiralTag() not in _TETRAHEDRAL:
                continue
            begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            # Individual wedging keeps the user's wedge bond and endpoints.
            # Whole-molecule wedging may reverse bonds or select new ones.
            Chem.WedgeBond(bond, begin, target.GetConformer())
            if (bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()) != (begin, end):
                raise ChemistryError('The reflected wedge could not preserve its bond endpoints.')
            entry['direction'] = int(bond.GetBondDir())
            wedge_centers.add(begin)

        expected = Chem.MolToSmiles(target, isomericSmiles=True)
        if Chem.MolToSmiles(graph_to_mol(out), isomericSmiles=True) != expected:
            raise ChemistryError('Reflection could not preserve the requested stereochemistry.')
        if wedge_centers:
            # Stored atom tags take precedence during ordinary graph parsing.
            # Remove them at wedged centers to independently check that the
            # visible wedge geometry also encodes the intended configuration.
            depicted = copy.deepcopy(out)
            for index in wedge_centers:
                depicted['atoms'][index]['chiralTag'] = 0
            if Chem.MolToSmiles(graph_to_mol(depicted), isomericSmiles=True) != expected:
                raise ChemistryError('The reflected wedge geometry is ambiguous; the drawing was retained.')
        # Includes coordinate-precision and E/Z checks through the established
        # exporter. This creates text in memory only and never writes a file.
        export_mol(out)
        return out
    except ChemistryError:
        raise
    except Exception as error:
        raise ChemistryError('Reflection could not preserve the requested stereochemistry; the drawing was retained.') from error
