# Atom hydrogens and radicals candidate

New atoms retain the existing engine-default valence policy: explicitHs=0,
noImplicit=false. The engine computes attached hydrogens; these are label counts,
not extra graph atoms. Carbon-bound hydrogen counts now appear as CH, CH2, CH3 or
CH4 using the existing outlined-label renderer. Heteroatom hydrogen labels remain
engine-generated. Counts change with supported bond, charge and radical edits.
Isotope, charge and atom-map information is retained on custom carbon labels;
radical dots remain engine-drawn. No valence or hydride is invented for elements
without an engine default. Imported hydrogen counts and noImplicit flags remain
unchanged.

Selected atom now has a separate Unpaired electrons (radicals) field, 0–8.
An incompatible requested count is rejected instead of accepting a sanitized,
different radical state. This guard applies to graph conversion, including edits,
properties and export. Fixed-H imported structures can therefore reject some
charge/bond/radical edits that previously silently recomputed radicals. Import a
structure with the intended hydrogen/radical state in those cases. Existing
transactional application keeps the document unchanged when conversion fails.

This is not a claim of stable electronic configurations, measured chemistry,
synthetic accessibility or force-field coverage. Generated 3D retains its existing
nonradical supported-element limit.

The requested full-element-name picker change is not part of this candidate: its
edit was blocked before execution. The prior picker and keyboard entry are
unchanged. Backend/schema support for the 118 element symbols is unchanged.

Validation: 19 focused chemistry/label tests passed on RDKit 2025.09.4; app module
syntax passed. These tests check computed chemistry and label configuration, not
visual output. Actual font outlines, radical-dot placement, isotope/charge/map
placement, clipping, dense labels and PNG export require trusted Arial rendering
acceptance. No substitute font or font-validation bypass was used. Browser and
Windows acceptance were not run.
