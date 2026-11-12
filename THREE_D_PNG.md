# Explicit 3D PNG coordinates

The optional 3D viewer offers Export 3D PNG and Open 3D PNG. Export takes one immutable camera/scene/model snapshot, rasterizes local SVG primitives to 1280 × 800 with a white background, and writes Software = nivraM plus the actual runtime version. Camera gestures change the depiction, never the accepted MOL bytes.

After download dispatch, the 3D status says **PNG prepared**, retains the selected
metadata notice, and asks the user to complete the save/download dialog and check
the file. It explicitly says saving is not confirmed here. This controller receives
no browser/native save-completion receipt; a normal anchor click is not evidence
that a file was saved. Its existing boolean success means preparation and download
dispatch completed, not verified disk persistence. Download mechanism, filenames,
bytes, privacy choice, cancellation and stale-result guards are unchanged.

One uncompressed `nivraM Coordinates` iTXt record carries schema `nivram-png-coordinates/2` (generated or V2000 supplied), `/3` (plain V3000 supplied), `/4` (native-validated V3000 supplied), or `/5` (V2000/V3000 supplied RAD declarations): exact MOL text, its byte count and SHA-256, bounded scientific provenance and its checksum. Generated V3000 records also carry ordered historical source atom IDs and bounded source context. Supplied V2000 remains an explicit-3D ASCII declaration-only subset: inline charge codes 0/1/2/3/5/6/7, fixed-width M CHG values −8…+8 and M ISO absolute masses 1…400 are supported. A present M CHG or M RAD channel jointly replaces all inline charges/radicals; multiple lines accumulate, with duplicate atom assignments in the same channel rejected. Absent isotope is the internal sentinel 0 (“not specified”), not isotope mass zero; explicit M ISO 0 rejects. Nonzero inline mass differences, radical code 4 (even if overridden), query/stereo/maps and all other V2000 properties reject. Isotope values are bounded declarations, not a validated nuclide inventory, CTfile reference-table conformance or chemical identity. Source bytes remain unchanged. No native chemistry validation is invoked for this subset. Records are limited to 1 MiB, MOL to 256 KiB, provenance to 128 KiB, context to 48 KiB and PNG to 32 MiB. Recipients can recover this metadata.

Opening does not change the 2D drawing or regenerate coordinates. Schema4 supplied records are freshly parse-validated from exact embedded MOL; their saved receipt is never trusted as current validation. A current engine-derived interpretation is shown separately from historical provenance. Generated records are freshly parsed in a disposable, authenticated, cancellable RDKit worker. The worker checks allowed V3000 features and identity/geometry consistency. It does not embed or minimize. Imported producer, generation method and verified flags remain historical claims, not live authenticated generation. Hashes prove internal consistency, not authenticity, chemical accuracy or correspondence to PNG pixels. Re-export preserves historical provenance while identifying today's exporter.

2D drawing coordinates retain schema v1 unchanged. Chart outputs remain software-only. The 2D image importer directs native 3D PNGs to the 3D viewer instead of flattening them. This is not arbitrary foreign V3000 import, optical recognition, experimental geometry validation or all-format interoperability.

## Qualification

Linux source checks verify supplied LF/CRLF/signed-zero/trailing-whitespace exact-byte round trips and 11 real generated molecular fixtures, including chirality, E/Z, charge, isotope, aromaticity, explicit hydrogen, atom maps and zero-bond molecules. Fake-DOM integration uses actual generation and recovery workers, checks stale/close/cancel fencing and rejects forged validation flags. Injected raster tests cover resource cleanup; they do not establish real browser rendering.

Real browser rasterization, visual/accessibility inspection, file picker/download/reopen, and installed Windows worker lifecycle remain unqualified. Do not treat this candidate as a Windows/browser release. See the sibling validation receipt for exact passing checks and retained baseline failures.

## Supplied radical declarations

Schema5 and provenance `nivram-supplied-3d-view/3` are reserved for explicit V2000 M RAD or V3000 RAD. Codes 0, 1, 2, 3 preserve literal none, singlet, doublet and triplet declarations. They are not unpaired-electron counts, physical spin verification or total molecular multiplicities. RAD1 and RAD3 must remain distinguishable even though RDKit 2025.09.4 can map both to two radical electrons. No normalization or chemistry inference is applied. Values, pair counts, atom indices and duplicate assignments are bounded; RAD mixed with any atom CFG/VAL or bond CFG rejects, including RAD0. Inline V2000 code4 remains unsupported. Old metadata schemas reject RAD-bearing source.

Labels use RAD codes, while the table and tooltips explicitly name declaration semantics. Explicit RAD-bearing H atoms remain visible in implicit-H display mode. Supplied source bytes are exported exactly and reparsed on PNG reopening. Native generated/recovery and supplied CFG/VAL validation keep their radical rejection. No input is rerouted to avoid a required native check.

## Retained embedding hydrogens (schema 6)

New generated views retain the H coordinates already produced by bounded ETKDG
in a separate `nivram-generated-hydrogens/1` display attachment. The canonical
MOL, source atom IDs, canonical readback coordinates, and generation provenance
remain unchanged. The attachment binds to the canonical MOL SHA-256 and contains
only ordered `generated-hN` identities, canonical parent indices, and finite
coordinates rounded to six decimals. Generated identities are not source IDs.
There are at most 256 derived display atoms and 512 display bonds.

The unchecked Explicit hydrogen checkbox condenses eligible ordinary source H
and attached H into parent labels, counting each once. Isotopic, mapped and other
special source H retain the prior explicit display behavior. Checking it shows
the existing coordinate-bearing H in either ball-and-stick or space-filling mode.
No supplied geometry gains inferred H, and no 2D drawing is changed. Canonical
atom tables and MOL downloads remain source-atom views; attached H is preserved
through the PNG, not added to the canonical MOL download.

`nivram-png-coordinates/6` adds `generatedHydrogensJson` and its SHA-256 solely
for recorded generated geometry. Schemas 2–5 remain supported without gaining
missing H coordinates. On reopening schema 6, the disposable native worker first
revalidates the canonical MOL, then checks attachment structure and exact expected
per-parent appended-H topology with RDKit AddHs. It does not embed, optimize,
remove source H, or generate positions. The fresh recovery validation `/2` binds
the attachment digest and explicitly marks hydrogen geometry unauthenticated.
Hashes and topology consistency do not prove historical generation, the original
2D source, experimental geometry, or correspondence to visible PNG pixels.
Re-export preserves the attachment; display choices still are not restored.

Verification is local Python/Node and controlled-DOM only. Browser rendering,
Windows packaging and live socket qualification remain outside this increment.
