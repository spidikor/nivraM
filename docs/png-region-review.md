# PNG overview and detail review

The overview/detail feature is implemented and verified in the source application. The visible
native source run in `verification/png-preview/source-native-1.json` passed 20
checks and exited with code 0. Its supervised duration was 29.172 seconds. That
run included an actual 11559 x 9999 synthetic PNG, a navigation overview, an
unscaled detail crop, reviewed metadata import, original-source preservation,
and one-step undo. This feature has not yet been verified in a rebuilt packaged
executable or installed candidate. The previously installed candidate does not
constitute acceptance of these newer source changes.

The correction draft and common chooser/drop/paste workflow are newer source
features. Focused browser checks cover draft history, cancellation, corrected
acceptance, and one-step main Undo; a chemistry-backed browser case covers atom
and bond edits, normalization, and invalid-valence rejection. These source
checks do not certify a newly packaged installer or live Windows clipboard
behavior. The previously delivered installer predates these correction changes.

Focused correction verification on 2026-09-23: **37 passed, 0 failed, 0 skipped,
exit 0**, in 22.515 seconds. All seven owned browser processes closed normally.
The command used the pinned Node runtime, the secure build Python environment,
headless Edge, and a workspace temporary directory:

```text
node --test --test-concurrency=1 --test-timeout=100000 tests/image-review.test.mjs tests/image-intake.test.mjs tests/image-import-browser.test.mjs tests/image-region-browser.test.mjs tests/image-correction-chemistry.test.mjs
```

## Using the review

1. Choose **Import PNG** and select a local PNG in the native file chooser,
   drop one PNG onto the drawing, or paste a PNG image with **Ctrl+V**.
   Inspection lists supported embedded molecular components and any warnings.
   Images without supported molecular metadata do not become editable merely
   because their pixels can be displayed.
2. For a large supported image, choose **Load overview**. The overview is a
   reduced navigation aid. It cannot enable acceptance by itself.
3. Select the complete metadata components that you intend to import. Enter
   **Left (x)**, **Top (y)**, **Width**, and **Height** in original source pixels,
   or drag a region in the overview to populate those controls. The numeric
   controls provide a keyboard alternative to dragging. Coordinates begin at
   the top-left corner, with zero-based x and y values.
4. Choose **Load selected detail at 100%**. The detail preserves the selected
   source pixels without resampling. Scroll its white viewport to inspect all
   edges. Choose **Preview selected components** and compare the reconstruction
   with the source, including charges, stereochemistry, warnings, and omissions.
5. Correct the **Structure draft** as needed using its atom, bond, ring, move,
   and erase tools. The atom controls also edit charge, isotope, and explicit
   hydrogens. Draft Undo/Redo and Reset affect only this review. Each correction
   is checked by the local chemistry engine and clears the acknowledgment so
   the updated structure can be reviewed again. Changing the component selection
   starts a new draft.
6. Acknowledge the review and choose **Import reviewed components**. The result
   is an unsaved project; use **Save As** to select a destination. One undo
   restores the preceding drawing. Cancel leaves the drawing unchanged.

Changing crop coordinates or component selection invalidates the prior detail
acknowledgment. Load and review the current detail again. Closing the review,
changing the document, cancellation, and stale replies must not publish a later
result into the document.

The detail's scale is one source pixel per image pixel and, at 100% browser zoom,
one image pixel per CSS pixel. This is not a promise of one source pixel per
physical display pixel: Windows scaling, browser zoom, and the device pixel
ratio can change that mapping. The navigation overview uses nearest-center
sampling and can omit thin bonds or labels; it is not suitable for final
chemical verification.

## Supported scope and limits

| Item | Current limit or behavior |
| --- | --- |
| Original source bytes | At most 64 MiB |
| Original dimensions | At most 32768 pixels per edge and 128,000,000 pixels total |
| Sequential filtered output | At most 512 MiB processed; not retained as a full raster |
| PNG encoding for derived region review | Core grayscale, indexed, RGB, grayscale-alpha and RGBA sample depths, including Adam7; see [PNG encodings](png-encodings.md) |
| Overview | At most 960 pixels on its longest edge; integer dimension mapping, no enlargement |
| Detail crop | Integer coordinates inside the source; at most 2048 pixels per edge and 1,000,000 pixels total |
| Encoded derived PNG | At most 4 MiB |
| Worker response | At most 6 MiB of standard output; bounded separate error output |
| Source chunk count | At most 4096 |
| Worker execution | One active preview worker; 40-second cooperative decoding deadline within a 45-second supervisor budget |
| Temporary preview cache | Application quota of 128 MiB including the incoming snapshot, 128 entries including the new directory/file, and bounded nesting |
| Preserved original-source store | At most 512 MiB under the existing source-preservation policy |

The worker budget excludes native file operations before/after the worker and
the supervisor's separate cleanup allowance; it is not a hard deadline for the
whole user interaction. Cache quotas are application checks, not filesystem
quotas against arbitrary concurrent changes by other programs.

Metadata inspection and region-preview services recheck the original attempt's
cancellation after cleanup, before returning a successful result. Cancellation
acknowledged while finalization waits therefore suppresses that result without
replacing an earlier worker, validation, or cleanup failure. This does not add a
total-operation deadline to either service or replace the session and document
revision checks used when adopting a result.

Animated PNGs and unsupported extensions cannot use this derived-region path.
Supported sample and palette transparency are preserved. A single valid `sRGB`
declaration is retained in the derived image. It may be accompanied by the
standard sRGB `gAMA` and/or `cHRM` fallback values, which are validated but not
copied to the derivative. Standalone or nonstandard `gAMA`/`cHRM`, `iCCP`,
`eXIf` and other unsupported extensions are rejected rather than silently
discarded. This is not general color-management support.
Supported inert text, physical-dimension, and timestamp metadata are not copied
into derived previews. The unchanged original PNG is the preservation record.

These restrictions describe the new region decoder. The existing bounded
whole-image preview remains a separate path for images that meet its limits.
Do not interpret a header-level capability result as proof that a compressed
PNG is valid: complete decoding and validation are required before a region is
published.

## What a crop does and does not establish

A crop changes the area of source artwork displayed for review. It does not
recognize a molecule, locate metadata components in the image, select components
automatically, or clip atoms from a selected component. Selected metadata
components remain complete disconnected graph fragments. A salt or mixture may
require several fragments to be selected together. Annotations remain in the
original image rather than becoming editable graph objects.

Acceptance uses an opaque native review token bound to the current source
snapshot, region request, and explicit component selection. The application
also checks the source hash, derived-image hash, geometry, browser decoding,
document revision, and cancellation state. An overview has no acceptance token.
Changing the source/region/selection invalidates stale review authority. These
checks establish consistency of the application transaction; they cannot prove
that a person inspected every feature or that metadata corresponds to the
chosen image area.

On acceptance the original inspected PNG bytes are preserved and hash-checked
in the local profile's `import-sources` directory. The resulting project records
the source identity and reviewed region, including its rectangle and derived
PNG hash. Provenance explicitly states that image-to-metadata correspondence is
not established and that whole components were retained. The external source
file is not overwritten. Manual corrections are recorded separately from the
original metadata graph in the project's image-import provenance. This does
not establish correspondence between the metadata, corrections, and source
pixels. Optical recognition remains unavailable. Chooser, drop, and paste all
use this same review transaction; pasting while typing in a text field leaves
the field's ordinary paste behavior intact.

## Processing, privacy, and memory interpretation

The native host creates an immutable local source snapshot for one owned worker;
browser content does not supply filesystem paths. The worker validates source
chunk structure and CRCs, scans the complete compressed stream, and reconstructs
rows sequentially, including rows outside the crop. It does not create a full
source raster. Dependent PNG filters use generated two-row Pillow carriers;
the source dimensions are never passed to Pillow as a full image to load.
Overview or crop pixels are the only retained output raster. The native result
boundary separately checks the derivative's complete PNG/zlib stream in bounded
pieces before it crosses the bridge.

The temporary snapshot is removed after confirmed direct-worker exit. Unknown
files, redirected cache entries, and unconfirmed cleanup are retained locally
and reported rather than recursively deleted. Cancellation and timeout stop
only processes owned by the supervisor. An OS crash or forced termination of
the parent is outside normal-cleanup guarantees. No image upload, external
recognition service, OCR model, or chemical inference is introduced by this
feature.

`pixelPipelineBudgetBytes = 16777216` is explicit buffer accounting, not a
16 MiB limit on process RAM. Its stated exclusions include interpreter/native
baseline, immutable source bytes, encoded PNGs, and JSON/base64/native-bridge/
browser copies. Neither sampled process memory nor this accounting proves a
hard operating-system memory ceiling.

## Evidence and remaining validation

Paths below are relative to the source working tree. Release assembly may copy
selected receipts into its separate `evidence` directory; test profiles and
private reference images are not release evidence artifacts.

- `verification/png-preview/source-native-1.json`: 20 checks passed, exit 0,
  no forced termination. The 11559 x 9999 source produced a 960 x 830 overview.
  The reviewed rectangle was x=1000, y=1000, width=640, height=230. Native readback
  confirmed a 640 x 230 image and CSS box, a white scrollable detail viewport,
  recorded region provenance, preservation of the full original source, and
  one-step undo. Normal WebView2 exit, managed-session removal, preview-worker
  shutdown, and other owned-worker exit were confirmed.
- `verification/png-preview/sequential-core-final-tests.json`: 16 core tests
  passed. Every supported filter for RGB/RGBA was compared with known synthetic
  pixels and a whole-source Pillow crop oracle. The small whole-source oracle
  belongs to tests, not the production large-image decoding path.
- `tests/test_png_preview_jobs.py` and `tests/test_worker_processes.py`: result
  geometry/resampling, malformed derivatives, retained-cache limits, cleanup
  failures, real worker pixel/accounting readback, cancellation, timeout, output
  limits, and direct-child ownership have focused regression coverage.
- `verification/png-preview/sequential-core-final-benchmark.json`: synthetic
  11559 x 9999 core benchmarks with Python 3.12.14, Pillow 12.3.0, and zlib 1.3.2.
  These are decoder measurements, not GUI/supervisor/packaged timings.

| Synthetic benchmark | Output | Elapsed seconds | Sampled private-memory baseline / peak, bytes |
| --- | --- | ---: | ---: |
| Mixed filters, overview | 960 x 830 | 3.213 | 26,243,072 / 32,915,456 |
| Average filter, overview | 960 x 830 | 2.931 | 28,594,176 / 39,755,776 |
| Paeth filter, crop at x=3000, y=8999 | 1000 x 1000 | 5.240 | 28,938,240 / 42,172,416 |

The benchmark sampled Windows private bytes every 5 ms. Sampling can miss a
short-lived peak, and performance varies with data and the machine. All three
cases processed 462,323,763 filtered bytes sequentially; the largest accounted
pixel-pipeline allocation was 5,741,728 bytes. This is not whole-private-corpus
calibration or evidence of recognition accuracy. Packaged/native installation
acceptance for this feature, clean-Windows testing, broad format coverage, and
complete accessibility and chemical correspondence validation remain separate
gates.
