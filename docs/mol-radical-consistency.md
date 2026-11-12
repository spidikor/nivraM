# Positive MOL radical-count consistency

The existing singlet, malformed/duplicate RAD, quoting, continuation and physical-line
policies remain in effect. The raw preflight also returns positive radical-electron
constraints: RAD2 means one, RAD3 means two. V2000 legacy atom charge-code 4 means
one unless an actual M CHG or M RAD property record globally supersedes the legacy
atom fields. Zero-entry property records also supersede them; M ISO does not.
Header text, A/G payloads and S SKP skipped records do not establish overrides.
Explicit M RAD constraints survive CHG records in either order. Repeated radical
assignments remain rejected, even if one is zero.

Legacy three-character charge fields accept blank/default or an integer with
optional sign, ASCII digits and outer ASCII spaces only, in the CTfile range0–7.
Padded and leading-plus spellings remain supported. Malformed native-prefix forms
such as `4 0`, `4+0`, `4-0` or an embedded NUL reject before native parsing,
even if an overriding property follows. No numeric prefix is interpreted.

An actual M CHG record must have an integer entry count0–8 and exactly that many
fixed-width atom/charge pairs, followed only by whitespace. Atom ordinals must be
within the declared atom count; charge values must be in the CTfile range−15…+15.
Its numeric fields use the same signed-ASCII-integer/outer-space grammar. Empty
zero-entry records remain valid. Bare records, negative counts, missing pairs,
surplus data and malformed values reject conservatively. This is explicit format
policy: some malformed native-accepted CHG records do clear legacy charges, so
these rejections are not presented as scanner/native override disagreements.
Existing duplicate CHG precedence is not changed.

Constraints use retained input atom order. V2000 ordinals are one-based; V3000
numeric labels may be nonsequential and unsorted, but must be positive and unique.
Neither route removes explicit H atoms. Isotopes and atom maps do not determine
correspondence and remain subject to existing preservation checks.

After each route's existing native sanitization, before graph/candidate creation,
a differing positive count rejects with the specific `inconsistent_mol_radical`
error. Ordinary MOL import retains full `sanitize=True, removeHs=False` parsing;
optical validation retains its existing unsanitized parse, feature checks and
explicit sanitization. The patch never changes chemistry to make a constraint fit.

Zero and absent declarations impose no count constraint. Ordinary under-valence
native radical inference therefore remains possible. This compatibility policy
is deliberately narrower than complete declaration preservation. Counts and
canonical SMILES establish neither physical spin multiplicity nor real-world
chemical validity. The separate [signed-charge preparation policy](mol-signed-charges.md) preserves
validated leading-plus charge spellings without changing chemistry or these constraints.

Ordinary and batch preparation and protected optical validation expose the specific
conservative rejection. PNG MOL metadata records the source as unsupported with no
candidate. Population preparation retains its existing state-structure category.
Rejected or cancelled work must not replace a drawing or add a review candidate.
The narrower supplied/generated 3D policies are unchanged.

Synthetic tests cover 69 assessment fixtures including 24 valid controls, native
atom order with nonsequential V3000 labels and explicit H, effective property
precedence, integer spellings, identity, isotope/map and stereo preservation.
Separate subprocess checks exercise real optical validators without model inference.
These are local source tests, not recognition accuracy, Windows containment,
installed-app, licensed-reference, private-corpus or full CTfile verification.

Sources: [BIOVIA CTfile Formats 2020](https://discover.3ds.com/sites/default/files/2020-08/biovia_ctfileformats_2020.pdf),
Atom Block and Charge/Radical/Isotope property definitions; pinned
[RDKit MOL parser](https://github.com/rdkit/rdkit/blob/Release_2025_09_4/Code/GraphMol/FileParsers/MolFileParser.cpp)
and [MolOps](https://github.com/rdkit/rdkit/blob/Release_2025_09_4/Code/GraphMol/MolOps.cpp).
These sources support format/native behavior, not a physical multiplicity claim.
