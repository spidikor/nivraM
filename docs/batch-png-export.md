# Batch PNG export

Current background option (2026-11-11): white remains the default; the unchecked **Transparent background?** setting enables PNG alpha. Earlier white-only evidence below is historical. See [PNG transparency](png-transparency.md) for the changed validation contract and current limits.

Batch export accepts user-selected `.nivram` projects and single, supported 2D
`.mol` blocks. Reference PNGs, SMILES text disguised as MOL, SDF collections,
3D structures, empty drawings and unsupported chemistry are per-file failures.
The open editor document is never replaced, saved, marked clean or added to its
undo history by this feature.

Choose **Current drawing settings** or a saved profile revision in **Batch PNG
export**. Width and margins come from that selection and are displayed before
the native file dialogs open. Current settings can be adjusted in the existing
appearance/export controls; profile settings can be saved as a new revision.
The batch freezes its chosen style, width, margins and profile reference when it
starts. Each source file is read once into bounded immutable bytes after both
native dialogs return. Later source-file or editor changes do not alter the batch.

## Native boundary and integration

Keep `BatchExportManager()` in a private `DesktopApi._batch` member. These are the
only public bridge wrappers; none accepts a path:

| Bridge method | Private manager call | Result |
| --- | --- | --- |
| `begin_batch_export(settings)` | `begin(self._window, settings)` | Opens multi-file OPEN and destination FOLDER dialogs; returns session receipt or `status: cancelled` |
| `prepare_batch_item(session_id, item_id)` | `prepare(session_id, item_id)` | Validates the next snapshotted input, renders its own graph copy, returns isolated `project`, `graph`, `scene`, and `item` when `status: ready` |
| `write_batch_png(session_id, item_id, png_base64)` | `write_png(session_id, item_id, png_base64)` | Validates and publishes one PNG, then checkpoints the manifest |
| `fail_batch_item(session_id, item_id, error_code)` | `fail(session_id, item_id, error_code)` | Records `render_failed`, `export_failed`, or `encoding_failed` using fixed messages |
| `cancel_batch_export(session_id)` | `cancel(session_id)` | Cancels unfinished items and retains published output |
| `finish_batch_export(session_id)` | `finish(session_id)` | Final receipt; refuses completion if any item remains unfinished |

Settings have the shape `{style, export, profile}`. `export` has `width`,
`marginMode` (`percent` or `pixels`) and `margin`. `profile` is null or an
`{id, revision, name}` reference. The exact style/export values are stored alongside
that reference in the manifest; the reference is descriptive provenance, not a
cryptographic attestation of a profile-store record.

The final application shutdown calls private `_batch.close()` before window
destruction where practical. This permanently prevents new batches, cancels the
current session and rejects file dialogs that return after shutdown. The frontend
module separately listens for `nivram-close-review` to abort active work and
disable new starts. `nivram-close-review-ended` resumes those controls if closing
was declined. Source snapshots are released at terminal states.

Include `public/modules/batch-export.mjs` once. It initializes when
`nivram-native-ready` fires, or immediately if the native bridge/editor already
exist. `initializeBatchExport()` is idempotent. It creates its own controls in
`.appearance-panel`; it does not require modifications to the editor canvas.

## Rendering and cancellation

`renderBatchPng()` validates the returned scene using `prepareRender()`, creates a
connected off-screen SVG stage, and calls the exact existing `exportPng()`.
That function remains responsible for vector rerasterization, width/margin
geometry, opaque white pixels, allocation checks and its shared export lock.
The temporary stage is removed after success, failure or cancellation.

`createBatchExport()` waits for each prepare/render/encode/save step before
starting the next input. It passes its abort signal to the production renderer
and FileReader, checks cancellation after asynchronous steps, and requests native
cancellation. An aborted or late renderer cannot publish a later file.
Native stream validation also observes the cancellation event between bounded
chunks, and publication checks it immediately before attempting the atomic
operation. A PNG already published when cancellation arrives remains a successful
file. Noninterruptible browser PNG encoding and an in-progress filesystem call
must settle before their resources can be released; there is no claim of instant
preemption or OS-level disk-write deadlines.

A rejected native PNG-write acknowledgement stops the batch and requests native
cancellation. Publication may already have completed, so the frontend does not
relabel the item as a rendering/encoding failure, retry the write, or start another
input. A reported manifest checkpoint failure also stops processing immediately.
Already published outputs remain in place. Local rendering and encoding failures
still fail only their individual input and allow later inputs to proceed.

## Limits and memory accounting

Limits are explicit failures, not silent downsampling:

- At most 64 selected files, 4,000,000 source bytes per file and 64 MiB of retained
  source bytes for the batch. Atom/bond/coordinate/chemistry limits are the existing
  project/import limits. Rejected oversized/unreadable files have no claimed
  complete source hash.
- One active native session and one sequential raster job. The shared production
  renderer keeps its existing 512 MiB raster working estimate, width up to 9,999,
  per-side limit 32,767 and other safety checks.
- Each PNG is at most 32 MiB; base64 is at most 44,739,244 ASCII characters.
  `estimateBatchTransportBytes()` separately reserves two raw-size allocations
  plus eight bytes per encoded character for intermediate/string/bridge copies.
  Its ceiling at the byte limit is about 405.34 MiB, below a separate 512 MiB
  transport estimate. This is an engineering estimate, not a hard bound on total
  browser/Python RSS or a claim about immediate garbage collection.
- Native validation permits the production canvas's noninterlaced 8-bit RGB/RGBA
  PNGs, at most 32 million pixels. It checks the signature, header, dimensions,
  chunk order, CRCs, complete zlib stream, exact expanded row count, scanline filter
  identifiers and small supported ancillary chunks. It processes at most 64 KiB
  of compressed/expanded data per inner operation, rather than allocating a
  second full RGBA raster. Animation, text metadata, trailing data and malformed
  streams are rejected. All five PNG filters are reconstructed using bounded
  current/previous scanlines. Every RGBA pixel must be opaque; every pixel in
  the four requested margins must be white, using the ceiling of the selected
  percentage/pixel padding. Explicit zero padding imposes no border requirement.
  Invalid pixels fail before publication; accepted PNG bytes are unchanged.

## Output integrity and manifest

PNG names use the source stem and, on collisions, numbered suffixes such as
`structure (2).png`. There is no overwrite mode or hidden confirmation. A flushed
temporary sibling is atomically published using Windows `os.rename`, which fails
if the destination exists. A collision that appears after naming is retried with
the next suffix. The non-Windows fallback uses an exclusive hard link and fails
closed if the filesystem does not support it. Python documents the Windows
no-replacement behavior in its [os.rename reference](https://docs.python.org/3/library/os.html#os.rename).

Each PNG is read back and SHA-256 checked before a success receipt. A failed
readback retains a potentially published output and marks the item failed with
its known output name/hash, rather than deleting possible user data. A failure
before publication leaves no partial target and cleans its temporary file.

The uniquely named JSON manifest uses schema `nivram-batch-export/1`. It records
the frozen settings, opaque identifiers, source basenames/extensions, per-file
status, source SHA-256 where completely read, output basename/SHA-256/dimensions
where published, and bounded error codes/messages. It omits personal absolute
paths by default. Basenames and hashes can still be identifying data; the app
does not upload this manifest or the structures.

The manifest is atomically checkpointed after each item/state change. A changed
manifest detected before replacement is retained. If a checkpoint cannot be
saved, the session stops, successful PNGs remain, and the live receipt explicitly
reports that the on-disk manifest is the last available checkpoint. PNG and JSON
publication cannot form one cross-file atomic transaction: a crash in between can
leave a completed PNG absent from the last manifest. No stronger transaction or
power-loss durability claim is made. After a hard crash, a manifest can contain
`pending`/`rendering`; it must not be interpreted as a completed batch.

## Verification scope

`tests/test_batch_export.py` exercises the real chemistry backend and temporary
filesystem: source immutability, MOL/project handling, rejection boundaries,
sequential opaque IDs, hashes/dimensions, write and manifest failures, cancellation,
bounded PNG stream and pixel checks, all five filters, RGB/RGBA, zero/fractional
padding, split scanlines, and a Windows collision introduced at publication.
Dialog return values are controlled in these tests; they do not prove native file
dialog interaction.

`tests/batch-export.test.mjs` checks orchestration sequencing, settings snapshots,
partial success, cancellation during rendering/encoding and late dialog replies,
bridge failure and separate transport estimates with injected boundary adapters.

`tests/batch-export-browser.test.mjs` uses actual headless Edge, actual RDKit scene
generation, the production PNG renderer and native stream validation. It checks
exact requested width, opaque pixels, white margins, unchanged drawing/input,
stage cleanup, cancellation during real SVG decode and close-review UI events.
Only the bridge in cancellation/UI tests is simulated. These are browser tests,
not evidence of completed real native multi-select/folder dialogs, installer
behavior, offline network protection, or packaged batch acceptance.

The fixed `batch-png` native acceptance scenario observes the original production
multi-file OPEN and FOLDER calls, with exactly two direct synthetic fixture files.
It selects a saved spaced-drawing profile whose width and margins differ from the
active editor, preserves a preexisting output-name collision, and verifies one
complete PNG and one strict malformed-MOL failure against the on-disk manifest.
Every PNG pixel, source hash, collision sentinel, complete editor state and native
worker cleanup is checked. The scenario does not substitute dialog results or
expose a new public automation bridge. Unlike single-PNG saves, batch publication
uses the native filesystem manager and expects zero browser downloads.

This completion scenario does not establish in-flight native cancellation,
freezing under a later intentional editor/source change, native valid 2D-MOL
success, installation or clean-machine behavior. A separate source-only probe of
31,996,800 Paeth-filtered RGBA pixels measured 55.865 seconds for validation;
large-image validation latency remains a measured hardening boundary.
