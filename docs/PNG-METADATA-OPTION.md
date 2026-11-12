# Optional removal of PNG chemistry metadata

This source increment adds the exact unchecked checkbox **Remove chemistry metadata?**
for the main drawing PNG, exact profile PNG preview, native batch PNG export and
3D PNG export. The choice is session/export-only. It does not enter a project,
saved profile, Undo history, recovery document or SVG option. Existing projects and
profiles keep their prior format. Batch receipts record the frozen per-job choice.

Unchecked (the default) keeps the existing recoverable record: the exact validated
2D graph or the exact 3D MOL with its bounded provenance, source binding and optional
generated-hydrogen record. Default and explicit-false exports retain prior bytes
for the same canvas encoding and runtime version. Checked omits the entire native
`nivraM Coordinates` record, rather than removing only coordinates and leaving its
chemical graph, MOL, identifiers, provenance or hashes behind. It keeps the current
`Software: nivraM <version>` label. Calculated-population and reference-chart PNGs
already carry only that software label, so they have no redundant chemistry checkbox.

The PNG artwork, dimensions, margins, alpha/white-background choice and compressed
image chunks are unchanged. The source graph, saved project, original MOL, profile,
3D view and input files are not modified. Visible atom labels and bonds still reveal
chemical information. This is not anonymization, redaction of the artwork, a general
PNG metadata scrubber, or a guarantee that the structure cannot be recognized.
A stripped PNG cannot use nivraM's native coordinate recovery. Keep the project/MOL
or an unchecked PNG if exact recovery will be needed.

## Boundaries

The flag is a strict boolean. It is captured before export awaits. Main and batch
controls are disabled while their operation is active. Changing the 3D metadata
checkbox invalidates any pending export, requiring a new export with the new choice.
Changing the profile-preview choice cancels/clears pending or completed preview
bytes; Save cannot reuse stale bytes. A preview's saved PNG remains its exact
previewed byte sequence. Existing document/model/close/cancel fences remain in place.

2D export still validates its source graph even when metadata is omitted. 3D export
uses the already accepted view and existing source-currentness gate; it need not
construct an unneeded recovery record in removal mode. Neither path cleans up,
reassigns stereochemistry or regenerates molecular coordinates.

The shared stamper checks bounded PNG framing and chunk CRCs, not IDAT decoding.
Removal mode can discard the native coordinate key in tEXt/zTXt/iTXt framing. It
rejects other preexisting text metadata and unexpected chunk types rather than
claiming to scrub arbitrary uploaded images. It accepts the established canvas
image/color chunks and bounded valid sBIT significant-bit-depth values. The producer
label is still resolved from the running application, never invented when status
lookup fails. Native batch independently decodes/validates the PNG and rejects any
coordinate record if the frozen session requested removal, before file publication.
Default batch validation retains its existing contract; it is not an authenticity
proof that an arbitrary input PNG's pixels match its metadata.

The removal choice affects PNG bytes only. Original batch inputs and the batch
manifest are not scrubbed: manifests retain input/output filenames, source/output
hashes and settings. Do not share those files assuming this checkbox removed them.

## Qualification

Focused tests cover exact default/false byte identity, absent native records when
selected, independent Pillow pixel decoding, strict flags, native batch rejection
before publication, frozen settings and preservation of input bytes. Independent
review exercises real nonbrowser @napi-rs/canvas rasterization against the untouched
baseline, generated/supplied/recovered 3D records, and preview/3D late-result fences.
Pinned RDKit tests cover real generated stereochemical identity and exact MOL
recovery with metadata retained. Test counts and exact source hashes belong in the
matching checkpoint receipts, not to every later source revision.

Browser UI/layout/accessibility, Windows installed-app acceptance and the trusted
Arial 2D chemistry-scene rendering prerequisite remain unverified/deferred. The
two deep-JSON server-admission assertions failed at the PNG option checkpoint
(expected 400, observed 404). The later [JSON admission repair](JSON-ADMISSION.md)
addresses those failures separately. No installer is updated, no new dependency is
installed and no complete Goal or release acceptance is claimed.
