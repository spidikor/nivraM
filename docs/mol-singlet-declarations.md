# Explicit MOL singlet declarations

The editable graph stores radical-electron counts, not spin multiplicity. RDKit
2025.09.4 maps both CTfile code 1 (singlet) and code 3 (triplet) to two radical
electrons. Export then emits code 3. Raw 2D MOL inputs with explicit singlet
declarations now fail before native MOL parsing rather than silently changing
that declaration. This is unsupported-input rejection, not spin-state support
or a prediction of physical multiplicity. SMILES does not resolve this distinction.

## Bounded policy

MOL physical records must use LF or CRLF. Bare CR, VT, FF and other Python
`splitlines` separators reject so preflight cannot inspect different records from
the native parser. Normal CRLF files remain accepted under the same policy.

The shared `check_mol` preflight still bounds structural counts. It additionally
checks V2000 `M  RAD` property records after the counted atom/bond records and
V3000 RAD attributes in logical ATOM records. V3000 continuation lines are joined
before checking, and native quoted-token boundaries are respected, including
fully quoted assignments and a new assignment immediately after a closing quote.
Names/comments, V2000 A/G payload lines and S SKP skipped lines, unrelated quoted
attribute values, and unrelated parenthesized values are not radical declarations.

Code 1 rejects even when a later declaration would overwrite it. Duplicate
radical declarations for a V2000 atom (within or across records), or repeated
V3000 RAD attributes on one atom, reject rather than choosing a winner. Malformed
radical integers, codes outside 0–3, invalid V2000 atom indices, counts outside
0–8, missing pairs, and surplus non-whitespace V2000 RAD data also reject.
Accepted radical integer spelling is an optional minus and 1–9 ASCII digits;
code range still excludes negative values. Zero padding is accepted. Signed-plus
and quoted values reject conservatively, even where the native reader tolerates
them. Unbalanced V3000 atom quoting/parentheses reject. Other MOL syntax remains
subject to the existing parser, size, annotation and graph checks. This guard is
not a complete CTfile validator or a promise to preserve unknown metadata.

Valid ordinary, doublet and triplet controls remain supported within existing
chemistry restrictions. The later [positive radical-count consistency policy](mol-radical-consistency.md)
now rejects inconsistent positive declarations such as RAD2 plus VAL2 changing to
two electrons. Zero and absent declarations still permit native inference. Neither
SMILES nor an electron count establishes physical spin multiplicity.

## Consumers and error behavior

Ordinary paste/file import, batch MOL preparation, PNG MOL metadata, manual PNG
structure correction and population-state MOL preparation use `import_structure`
and therefore this boundary. Optical results use the same preflight directly,
before their unsanitized native parse. Specific singlet rejection is propagated
through the ordinary/batch preparation worker and protected optical validator;
PNG metadata reports the source as unsupported rather than offering a candidate.
Population-state preparation retains its existing state-structure error category.
Errors do not commit a new editor document or image review candidate.

Supplied 3D validation and recorded-generated coordinate recovery already use a
narrower grammar that rejects every RAD property; that policy is unchanged.
Conformer generation and export reparse only internally generated MOL, not new
raw-MOL ingress. Direct calls accepting an already-parsed RDKit molecule cannot
recover a declaration discarded before that call.

## Source and verification limits

BIOVIA CTfile Formats 2020 distinguishes codes 1/2/3 on PDF pages 14 and 55:
https://discover.3ds.com/sites/default/files/2020-08/biovia_ctfileformats_2020.pdf

Pinned native implementation:
https://github.com/rdkit/rdkit/blob/Release_2025_09_4/Code/GraphMol/FileParsers/MolFileParser.cpp

Tests use synthetic frozen one-carbon declarations, valid doublet [CH3]/VAL3 and
triplet [CH2]/VAL2 controls, real local validator subprocesses and session failure
checks. They do not validate recognition accuracy, Windows containment, the
installed GUI, physical spin chemistry, or all possible CTfile representations.
