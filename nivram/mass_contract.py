"""Data-only boundary contract, not an isotope table or chemical validator."""
MASS_REASON = 'missing_isotope_mass'
MASS_MESSAGE = ('Molar mass and exact mass are unavailable: the RDKit isotope mass table '
                'has no usable entry for one or more explicit isotope labels. '
                'The graph annotations are retained; this is not a claim about isotope existence or stability.')


def valid_mass_unavailable(value, graph):
    """Bind a bounded missing-table report to explicit labels in the source graph.

    Only the chemistry worker checks the actual engine table. This parent-side
    check validates transport semantics without importing RDKit.
    """
    if (not isinstance(value, dict) or set(value) != {'reason', 'message', 'isotopes'}
            or value['reason'] != MASS_REASON or value['message'] != MASS_MESSAGE):
        return False
    entries = value['isotopes']
    if not isinstance(entries, list) or not 1 <= len(entries) <= len(graph['atoms']):
        return False
    labels = {(atom['symbol'], atom.get('isotope', 0)) for atom in graph['atoms']}
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'symbol', 'isotope'}
                or not isinstance(entry['symbol'], str) or type(entry['isotope']) is not int
                or not 1 <= entry['isotope'] <= 400):
            return False
        key = (entry['symbol'], entry['isotope'])
        if key not in labels or key in seen:
            return False
        seen.add(key)
    return True
