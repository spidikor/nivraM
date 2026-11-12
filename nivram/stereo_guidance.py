"""Bounded presentation diagnostics, never a stereochemistry assignment engine."""
from __future__ import annotations

SCHEMA = 'nivram-stereo-denial/1'
MESSAGE = 'Invalid stereochemistry! Please specify'


class UnresolvedStereoError(ValueError):
    def __init__(self, denial):
        super().__init__('Specify potential atom/bond stereochemistry before generation; no stereoisomer is silently selected.')
        self.denial = denial


def map_unresolved(graph, records):
    """Map engine-derived (type, index) records using graph_to_mol insertion order.

    The caller has already validated chemistry and verified that the molecule's
    atom/bond ordering matches the source. This pure helper does not perceive
    stereo or establish whether the supplied records are chemically correct.
    """
    targets, seen, unsupported = [], set(), 0
    for kind, index in records:
        if kind == 'Atom_Tetrahedral':
            source, target_kind, notation = graph['atoms'], 'atom', 'R/S?'
        elif kind == 'Bond_Double':
            source, target_kind, notation = graph['bonds'], 'bond', 'E/Z?'
        else:
            unsupported += 1
            continue
        if type(index) is not int or not 0 <= index < len(source):
            unsupported += 1
            continue
        item = source[index]
        identifier = item.get('id')
        if (not isinstance(identifier, str) or not identifier or
                target_kind == 'bond' and item.get('order') != 2):
            unsupported += 1
            continue
        key = (target_kind, identifier)
        if key in seen:
            continue
        seen.add(key)
        targets.append({'kind': target_kind, 'id': identifier, 'notation': notation})
    return {'schema': SCHEMA, 'targets': targets, 'unsupportedCount': unsupported}


def unresolved_diagnostic(mol, graph, stereo_records):
    """Consume actual RDKit StereoInfo records; never infer stereo from text."""
    unresolved = [s for s in stereo_records if str(s.specified) == 'Unspecified']
    if not unresolved:
        return None
    # graph_to_mol adds atoms and bonds in source order. Check that invariant
    # here so a future upstream reordering cannot silently mislabel targets.
    ordered = mol.GetNumAtoms() == len(graph['atoms']) and mol.GetNumBonds() == len(graph['bonds'])
    if ordered:
        ordered = all(a.GetSymbol() == item['symbol'] and a.GetAtomMapNum() == item.get('mapNum', 0)
                      for a, item in zip(mol.GetAtoms(), graph['atoms']))
    if ordered:
        ordered = all({graph['atoms'][b.GetBeginAtomIdx()]['id'], graph['atoms'][b.GetEndAtomIdx()]['id']}
                      == {item['begin'], item['end']} for b, item in zip(mol.GetBonds(), graph['bonds']))
    if not ordered:
        return {'schema': SCHEMA, 'targets': [], 'unsupportedCount': len(unresolved)}
    return map_unresolved(graph, [(str(s.type), int(s.centeredOn)) for s in unresolved])


def validate_denial(denial, graph):
    """Reject malformed worker presentation payloads at the transport boundary."""
    bad = ValueError('Invalid stereochemistry diagnostic.')
    if not isinstance(denial, dict) or set(denial) != {'schema', 'targets', 'unsupportedCount'}:
        raise bad
    targets, unsupported = denial['targets'], denial['unsupportedCount']
    if (denial['schema'] != SCHEMA or not isinstance(targets, list) or len(targets) > 384
            or type(unsupported) is not int or not 0 <= unsupported <= 384
            or not 1 <= len(targets) + unsupported <= 384):
        raise bad
    atoms = {a['id']: a for a in graph['atoms']}
    bonds = {b['id']: b for b in graph['bonds']}
    seen = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {'kind', 'id', 'notation'}:
            raise bad
        kind, identifier = target['kind'], target['id']
        if not isinstance(kind, str) or not isinstance(identifier, str):
            raise bad
        key = kind, identifier
        if key in seen:
            raise bad
        seen.add(key)
        if kind == 'atom' and identifier in atoms and target['notation'] == 'R/S?':
            continue
        if kind == 'bond' and identifier in bonds and bonds[identifier]['order'] == 2 and target['notation'] == 'E/Z?':
            continue
        raise bad
    return denial
