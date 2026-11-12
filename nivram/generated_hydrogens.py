"""Bounded display-only H attachment. Never embeds or removes source atoms."""
import hashlib
import math

SCHEMA = 'nivram-generated-hydrogens/1'


def validate_attachment(value, mol_text, source_count):
    def need(ok):
        if not ok:
            raise ValueError('Invalid generated hydrogen display attachment.')
    need(isinstance(value, dict) and set(value) == {'schema', 'canonicalMolSha256', 'hydrogens'})
    need(value['schema'] == SCHEMA and value['canonicalMolSha256'] == hashlib.sha256(mol_text.encode()).hexdigest())
    hs = value['hydrogens']
    need(isinstance(hs, list) and len(hs) + source_count <= 256)
    for i, h in enumerate(hs):
        need(isinstance(h, dict) and set(h) == {'id', 'parentId', 'x', 'y', 'z'})
        need(h['id'] == f'generated-h{i+1}' and type(h['parentId']) in (int, float) and math.isfinite(h['parentId']) and h['parentId'] == int(h['parentId']) and 1 <= h['parentId'] <= source_count)
        need(all(type(h[k]) in (int, float) and math.isfinite(h[k]) and abs(h[k]) <= 10000 for k in ('x', 'y', 'z')))
    return value


def validate_topology(value, mol, mol_text):
    """Compare H topology only. Saved coordinates are not authenticated geometry."""
    from rdkit import Chem
    validate_attachment(value, mol_text, mol.GetNumAtoms())
    expanded = Chem.AddHs(mol, addCoords=False)
    parents = []
    for atom in list(expanded.GetAtoms())[mol.GetNumAtoms():]:
        neighbors = list(atom.GetNeighbors())
        if atom.GetSymbol() != 'H' or len(neighbors) != 1 or neighbors[0].GetIdx() >= mol.GetNumAtoms() or neighbors[0].GetSymbol() == 'H':
            raise ValueError('Unsupported generated hydrogen topology.')
        parents.append(neighbors[0].GetIdx()+1)
    if parents != [h['parentId'] for h in value['hydrogens']] or expanded.GetNumBonds() > 512:
        raise ValueError('Generated hydrogen attachment does not match canonical hydrogen counts.')
    return value


def from_embedding(explicit, source_count, mol, mol_text):
    hs = []
    for atom in list(explicit.GetAtoms())[source_count:]:
        p = explicit.GetConformer().GetAtomPosition(atom.GetIdx())
        hs.append(dict(id=f'generated-h{len(hs)+1}', parentId=atom.GetNeighbors()[0].GetIdx()+1,
                       x=round(p.x, 6), y=round(p.y, 6), z=round(p.z, 6)))
    value = dict(schema=SCHEMA, canonicalMolSha256=hashlib.sha256(mol_text.encode()).hexdigest(), hydrogens=hs)
    return validate_topology(value, mol, mol_text)
