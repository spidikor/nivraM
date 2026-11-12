# Optional supplied-coordinate viewer

This candidate adds an opt-in **3D input viewer** toolbar button. Opening it creates a separate modal; its state is independent of the 2D document, history, cleanup and save operations. This is an input/view/output increment, not completion of the PNG-to-3D goal.

## Supported subset

One ASCII MOL record with an explicit 3D header and finite decimal coordinates bounded to ±10,000 MOL units. V2000 accepts 1–256 elemental atoms and at most 512 plain single/double/triple/aromatic bonds. V3000 accepts 1–128 atoms from H/B/C/N/O/F/Si/P/S/Cl/Br/I and at most 256 single/double/triple bonds; see the detailed subset below.

V2000 supports inline charge codes 0/1/2/3/5/6/7, fixed-width M CHG (−8 to +8) and absolute M ISO (isotope mass number 1–400). A present M CHG or M RAD channel jointly replaces the inline charge/radical channel; multiple property lines accumulate, but duplicate atom assignments within the same channel reject. Nonzero inline isotope mass differences remain unsupported, including when M ISO is present. Inline radical code 4 (even if overridden), parity, mapping, query/stereo/valence and other V2000 properties reject. Absent isotope is the internal zero sentinel displayed as “not specified”; explicit M ISO 0 rejects. Accepted values are bounded declarations, not a validated nuclide inventory, reference-table conformance or chemical identity, and do not invoke native validation. Exact source bytes remain unchanged. V3000 preserves CHG (−8 to +8), MASS (isotope mass number 1–400), and canonical integer atom maps (0–999999); absent CHG defaults to zero formal charge; absent MASS uses an internal zero sentinel and displays as “not specified”, not a physical mass number or a natural-abundance claim. Duplicate properties, unsupported properties, continuations and extra sections are rejected. Supported CFG/VAL declarations require the separate fresh native validation path described below. Sequential atom/bond indices are required. Query/topology fields, SDF, PDB and XYZ remain unsupported. A rejected file is not necessarily an invalid molecule. The last accepted view remains intact.

Coordinates and chemical declarations are supplied. A fresh limited native parse-consistency check is required for supported CFG/VAL; it does not establish scientific identity or experimental origin. Coordinates are never minimized, regenerated or changed. The local renderer is a schematic orthographic projection with depth ordering, rotation and zoom, not a chemistry engine or photorealistic surface. Fixed atom-circle sizes are not atomic radii. Atom and bond tables expose all accepted declarations; isotope mass number and charge appear in atom labels and atom maps have their own column, separate from drawing source IDs; original coordinates remain readable. Pointer drag, arrow keys, zoom buttons/+/− and Home/Fit control the view. Closing invalidates pending loads.

Downloads are explicit: the accepted original MOL bytes, preserving LF/CRLF, and a separate provenance JSON containing SHA-256, source length and limitations. No remote requests, CDN dependency, CSP relaxation, automatic output, 2D mutation, conformer generation or PNG recognition are added by this module.

## Verification boundaries

The dependency-free parser/projection and a bounded fake-DOM controller test pass (`node tests/supplied-3d.test.mjs`). Independent parser probes also pass. A real Chromium test harness was prepared, but launch failed before loading any page: `socket() failed: Operation not permitted`. No bypass was attempted. Therefore actual rendering, browser accessibility, responsive layout, native Windows/WebView, full app interaction and real download behavior remain unqualified. The test double does not prove those behaviors. Do not describe this candidate as visually accepted or production-qualified.

Current guidance source-read for this work: CODE 1.10.0, PRISM 7.6.2, TOKEN 2.3.0 and frontend UI engineering; version evidence reused from the parent verified release receipt. This does not assert installation.

## Bounded V3000 reopening

The optional supplied viewer also accepts a deliberately bounded V3000 declaration subset: explicit 3D header, 1–128 atoms from H/B/C/N/O/F/Si/P/S/Cl/Br/I, up to 256 single/double/triple bonds, sequential atom/bond indices, atom maps 0–999999, CHG/MASS properties in the declaration-only path and supported CFG/VAL in the native-validated path, and coordinates within ±10,000. Continuations, query features, extra sections, SDF and trailing records are rejected. LF/CRLF bytes are preserved exactly.

Supported generated exports can be opened as **supplied**, never newly generated
or authenticated historical output. CHG/MASS/map-only files retain the prior
declaration-only route. Files with bounded atom CFG (1/2), VAL (-1 through 8), or
single-bond CFG (1/3) require a fresh authenticated local native validation request.
RDKit 2025.09.4 must preserve exact declarations through parse/reserialization and
leave all coordinates unchanged. Contradictory CFG, unspecified potential stereo,
normalization of VAL (including redundant defaults), and normalized-away bond CFG
are rejected. The accepted subset is deliberately narrower than CTfile.

Displayed CFG and VAL values are literal input declarations. The separately
labeled engine-derived isomeric identity may include stereo perceived from 3D
geometry, not explicitly declared stereo. Neither is an experimental-origin claim.
The original MOL and 2D drawing are not modified. No embedding or minimization is
performed. Unsupported input retains the last accepted scene.

Plain supplied V3000 PNGs retain `nivram-png-coordinates/3`; V2000 supplied and
generated V3000 retain schema2. Native-validated supplied inputs use schema4 and
supplied provenance2. JS and Python metadata validators check the structural
contract and exact bytes; a saved receipt is historical only. Every schema4 PNG
reopen freshly invokes the native worker before adoption. Re-export preserves
exact MOL bytes and historical provenance without claiming authentication. See
[supplied native validation](supplied-native-validation.md) for exact boundaries.

## Evidence for the declaration extension

BIOVIA CTfile Formats 2026 describes atom CHG, MASS (absolute isotope mass), atom mapping, CFG and VAL: https://www.3ds.com/assets/invest/2026-01/biovia_chemistry_2026_ctfile_formats.pdf . This implementation deliberately accepts a narrower subset, not the full specification. RDKit's interpretation of 2D/3D flags documents that 3D geometry takes precedence over wedging: https://www.rdkit.org/docs/RDKit_Book.html . Installed RDKit 2025.09.4 real exporter fixtures, rather than documentation version alone, establish the tested interoperability. Charge/isotope/maps are preserved without claiming native chemistry validation; the generated recovery path and its stricter parse/re-serialization checks are unchanged.

## Supplied RAD declarations

V2000 M RAD and V3000 RAD preserve literal codes 0 (none), 1 (singlet), 2 (doublet), and 3 (triplet). These are unverified declarations, not electron counts or verified physical spin. Duplicate atom/property assignments reject; RAD combined with atom CFG/VAL or bond CFG rejects, even RAD0. RAD-bearing hydrogen atoms stay visible in implicit-H mode. PNG schema5 preserves and reopens exact source text; earlier schemas reject RAD-bearing source. Native/generated radical rejection remains unchanged.

## Coordinate measurements

In the optional 3D view, choose Distance, Angle, or Signed torsion and select two,
three, or four distinct atoms in order. Selectors are keyboard accessible and do
not change drag rotation or arrow-key controls. Distances use accepted Cartesian
coordinates, never screen projection or image pixels, and assume MOL units are Å.
Angles use degrees, with atom 2 as the vertex. Atoms need not be bonded.

For torsion 1–2–3–4, project vectors 2→1 and 3→4 perpendicular to unit axis 2→3.
The signed angle is atan2(axis · (first projection × second projection),
first projection · second projection), in −180° to +180°. The sign is tied to
this ordered convention, not a stereochemical assignment. Reflections reverse it.
Distinct coincident atoms have distance zero; angular arms of length ≤10⁻⁸ Å
and torsions with normalized plane-projection length ≤10⁻⁸ are rejected as
undefined or nearly degenerate. These are numerical safeguards, not chemical
thresholds. Four displayed decimal places do not establish measurement accuracy.

Only currently visible atoms can be selected. Explicit H exposes existing H
coordinates; generated display-attachment H is labeled with its attachment ID
and parent, never substituted into the canonical source. Model replacement,
generated-source invalidation, H-display changes, and closing the view clear the
selection. Failed loads retain the previous valid model and its measurement.
Rotation, zoom and representation changes do not change the coordinate values.

Results label supplied, generated, or recovered coordinates. Recovered historical
origin/generation claims remain unverified. Measurements establish no bond,
energy, experimental accuracy or physical validation. MOL/PNG source records,
canonical graph, and 2D document/history are unchanged by measurements.
