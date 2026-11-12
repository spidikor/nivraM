# SMILES hydrogen fidelity

Ordinary SMILES import retains explicitly supplied hydrogen atoms using RDKit's
`SmilesParserParams.removeHs = False`, as 2D MOL import and the existing population
state SMILES path already do. A supplied `[H:7][O:8][H:9]` therefore retains three
atoms, maps 7/8/9, and its two O–H edges. Previously the ordinary parser removed
the two H vertices and lost their map annotations, leaving `[OH2:8]`.

This is a deliberate fidelity policy change, including unmapped hydrogens:
`[H]O` now has two graph atoms; `O` still has one. The previous README explicitly
allowed parser hydrogen removal. The correction addresses annotation loss and
retains the supplied representation; it does not imply that the old water formula,
mass or stereochemistry was necessarily wrong.

- Standalone H vertices keep supported maps, isotopes, charges and radical counts.
- Bracket H counts such as `[NH4+]` are not four separate H vertices. Existing
  kekulization may represent aromatic `[nH]` as an implicit count on N; chemical
  H counts and identity are preserved, not the original SMILES spelling.
- Implicit hydrogens are not expanded with `AddHs`. Existing inputs without
  standalone H vertices retain their previous parser behavior.
- Layout, editable atom/bond counts, labels and identity hashes can differ for
  inputs whose H vertices were previously removed. Carbon H-count labels exclude
  separate neighboring H vertices to avoid double-counting them.
- New ordinary text imports and supported PNG SMILES metadata recovery use this
  behavior. Saved graphs are not migrated, and naming normalization is unchanged.
- Formula, charge and mass calculations use the retained graph. Descriptor values
  remain local calculations, not measured properties or prediction validation.

Both the preflight and resulting graph limits still count every H vertex within
512 atoms and 1024 bonds. Bracket H counts occupy one atom token. Unsupported
annotations, query atoms, extra records and reaction syntax remain rejected.
MOL re-export/reimport, full cleanup and supported selected-region cleanup retain
chemical identity. Unselected coordinates remain fixed during selected cleanup.

The focused regression tests compare map-keyed topology, annotations and chemical
stereochemistry against an independently configured retaining RDKit parser. They
also exercise synthetic PNG metadata, scalar properties and H-label configuration.
They do not establish Windows worker containment, Arial rendering, installed GUI
acceptance, arbitrary SMILES support or equivalence to Marvin.
