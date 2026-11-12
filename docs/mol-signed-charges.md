# Same-integer MOL charge preparation

Ordinary MOL import and optical MOL validation share `prepare_mol`. After validating
raw declarations, it returns an internal copy using the same integer values without
optional leading plus signs in the following fields only:

- V2000 `M  CHG` entry count, atom ordinal and charge, retaining field widths.
- V2000 legacy atom charge code (columns 37–39). These are encoded values, not
  formal charges: code3 means +1, code5 means −1, and code4 retains radical semantics.
- V3000 `CHG` values in actual ATOM logical records, including continued tokens and
  fully quoted assignment tokens such as `"CHG=+1"`.

This works around RDKit2025.09.4 native leading-plus integer loss before sanitization.
It is lexical preparation, not a chemical correction or MOL serialization roundtrip.
The caller's text, LF/CRLF endings, unrelated fields, payloads and properties remain
unchanged. A/G alias payloads and S SKP skipped records are not charge records.
Normalization stops at end markers. Existing atom-annotation rejection still applies.

Complete ASCII decimal spelling and bounds are checked before the prepared copy
reaches RDKit. Malformed signs, Unicode digits, embedded whitespace, invalid
references, out-of-range values, truncated pairs and trailing garbage reject.
V3000 charge values use at most nine digits and the range −15…+15. Quoting only the
numeric value (`CHG="+1"`) is unsupported and rejects, rather than silently losing
charge. Unrelated quoted text is never globally stripped of plus signs. Repeated
CHG behavior and effective CHG/RAD precedence retain native compatibility. Zero-entry
V2000 CHG records remain supported compatibility behavior, not a spec-conformance claim.

Atom record order is unchanged, including unsorted/nonsequential V3000 labels,
explicit H, isotopes and maps. Both routes retain `removeHs=False`. Ordinary import
keeps full `sanitize=True`; optical keeps its existing unsanitized parse, feature
checks and explicit sanitization. Supported sanitizer cleanup may change formal
charges, as with neutral pentavalent nitro input. There is deliberately no blanket
raw-charge-versus-final-charge equality assertion and no charge mutation to force
optical agreement. Correct charged SMILES must match the resulting structure;
a corresponding incorrect neutral SMILES is rejected.

The existing unsupported-singlet and positive-radical consistency guards remain.
Other integer fields, atom/bond counts, coordinates, isotopes, labels and currently
unsupported leading-plus RAD spellings are outside this repair. No general CTfile
conformance, physical chemical validity, private-corpus accuracy, model inference,
Windows containment or installed-app qualification is implied by portable tests.

## Source basis

[BIOVIA CTfile Formats 2026](https://www.3ds.com/assets/invest/2026-01/biovia_chemistry_2026_ctfile_formats.pdf)
defines strict Fortran-style V2000 fields and integer charge semantics.
[ANSI X3.9-1978 FORTRAN77, reproduced in NIST FIPS69-1](https://nvlpubs.nist.gov/nistpubs/Legacy/FIPS/fipspub69-1.pdf),
sections4.3.1,13.5.9 and13.5.9.1, defines an optional sign followed by decimal digits,
says input plus signs may be omitted, and requires an optionally signed integer
constant in an integer input field. Together these support V2000 same-integer
normalization. V3000 is free-field: accepting `CHG=+1` is
an explicit same-integer compatibility contract, not a claim that the CTfile document
quotes a complete signed-integer regex. The pinned
[RDKit parser](https://github.com/rdkit/rdkit/blob/Release_2025_09_4/Code/GraphMol/FileParsers/MolFileParser.cpp)
uses integer conversion whose leading-plus loss is reproduced by regression fixtures.
