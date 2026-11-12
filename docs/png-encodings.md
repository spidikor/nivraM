# PNG formats for structure review

The bounded overview and detail-crop decoder supports these non-interlaced PNG
sources. This applies to supplied-structure review, embedded-structure review,
and the crop input to separately configured optical recognition.

| PNG source | Supported sample depth |
| --- | --- |
| Grayscale | 1, 2, 4, 8 or 16 bits |
| Indexed palette | 1, 2, 4 or 8 bits |
| Grayscale with alpha | 8 or 16 bits |
| RGB or RGBA | 8 or 16 bits |

Adam7-interlaced backend overview and exact crop support the legal core
color-type/sample-depth combinations: **grayscale at 1, 2, 4, 8 or 16 bits**,
**indexed palette at 1, 2, 4 or 8 bits**, and **RGB, grayscale-alpha and RGBA at
8 or 16 bits**, under the strict chunk policy below. Grayscale/RGB `tRNS` and palette
alpha follow the existing transparency rules. Alpha and hidden gray/RGB values
remain unassociated and exact; they are never premultiplied or flattened.
Adam7 16-bit is a new source-level increment; existing non-interlaced 16-bit support is unchanged. See the scoped November 10 verification below.

Palette alpha and grayscale/RGB transparent-color keys (`tRNS`) are supported.
Partially transparent palette entries retain their alpha values. A grayscale or
RGB key makes only matching pixels transparent. Conversion retains the color
values of transparent pixels; it does not flatten them against a background.
Packed samples and transparency follow the
[W3C PNG specification](https://www.w3.org/TR/png-3/#11tRNS).

For untagged or sRGB-declared sources, 16-bit color and alpha samples are reduced
to 8-bit review pixels using rounded linear scaling: `output = (sample + 128) // 257`. This is the integer equivalent
of rounding `sample * 255 / 65535` as described by
[PNG sample-depth rescaling](https://www.w3.org/TR/png-3/#13Sample-depth-rescaling).
Transparent-color keys are compared against every original 16-bit sample before
reduction. Two colors that become the same 8-bit color can therefore retain
different transparency. Alpha stays unassociated, including the RGB values of
fully transparent pixels. Precision reduction applies only to the derivative.

The review crop preserves pixel positions without spatial scaling. The original PNG bytes remain
the source of provenance and are preserved unchanged when the review is accepted.
This format support does not recognize a chemical structure or establish that
a supplied structure matches the image. Continue through the
[ordinary PNG review workflow](png-manual-review.md).

## Boundaries

The source is checked and decompressed one row at a time, including rows outside
the chosen crop. Invalid palette indices anywhere in the image reject the result.
For non-interlaced sources, Pillow receives only generated two-row filter
carriers, never the full source image. Packed source rows remain packed for filtering; output expansion is
separate. For 16-bit rows, the high- and low-byte lanes are filtered separately
using 8-bit carriers of the original width, then interleaved again. All five
PNG filters operate on bytes, so these separate lanes retain the source
predictor distances of 2, 6, 4 or 8 bytes per pixel and all low-byte information
needed by following rows. No full-source Pillow decode or 16-bit multichannel
Pillow conversion is used. The worker result is an 8-bit RGB/RGBA PNG; it
retains only a validated sRGB declaration when present.

All supported Adam7 paths use direct byte filters without Pillow decoding. Each
computes all seven pass dimensions and cumulative filtered lengths before
inflation, omits empty passes, and resets the previous row at each pass boundary.
Each decompression call has a positive bound of at most one pass scanline; an
extra-byte probe rejects excess output. All pass rows are reconstructed and
validated, including pixels outside a crop or overview sample. Only pass-row
buffers, output-sized selectors and the permitted derivative pixels are retained.
Crop coordinates remain exact; overviews use nearest-center source samples.

Indexed pass rows use `ceil(pass_width * bit_depth / 8)` packed bytes and a
one-byte filter predictor distance. Padding remains in the previous packed row
for filtering but is never treated as an additional palette index. Every actual
index is checked against the palette before crop or overview selection. Only
selected pass rows expand to RGB/RGBA, retaining palette colors and exact alpha.
Packed grayscale uses the same one-byte predictor and actual-sample unpacking;
its transparency key is compared to the original sample, with the existing
unused-high-bit mask, before grayscale scaling. Grayscale-alpha8 uses a two-byte
filter predictor and expands each gray/alpha pair to unassociated RGBA. Hidden
gray values survive even when alpha is zero. Previous-row state resets for each pass.

Existing limits remain: 64 MiB source, 128 million source pixels, 32,768 pixels
per source edge, 512 MiB decompressed scanline data, 4,096 chunks, a 960-pixel
overview edge, and a crop of at most one million pixels and 2,048 pixels per edge.
Derived PNGs are limited to 4 MiB. Explicit pixel-buffer accounting includes
expanded rows within the existing 16 MiB budget. That accounting excludes the
interpreter, source, encoded results and transport copies; it is not a measurement
of total process memory. Existing worker cancellation and deadlines still apply.

Arbitrary ICC profiles, noncanonical chromaticities, gamma without explicit
canonical chromaticities, orientation, animation and unknown extension chunks
remain unsupported. The bounded standalone gamma conversion below is supported. Such
sources receive an explanation instead of a potentially misleading crop.

Legal `sBIT` metadata is admitted for stored-sample display as described in the
November 12 section below. It does not enable original-precision recovery or
change any of these decoding and output boundaries.

## Verification scope

`tests/test_png_palette_preview.py` uses handcrafted source encodings and
independently enumerated expected pixels. It checks supported depths, all five
filters, transparency, packed padding, invalid source data and resource limits.
`tests/test_png_preview.py` retains RGB/RGBA and worker-result validation coverage.
`tests/test_png_16bit_preview.py` adds handcrafted 16-bit grayscale, RGB,
grayscale-alpha and RGBA pixels, rounded-scaling boundaries, full-precision
transparent-key near-collisions, low-byte filter dependencies, carrier bounds,
malformed streams, crop/overview geometry and unchanged resource limits.
The separate source-bound workflow receipt exercises actual image and protected
structure-preparation workers using grayscale, indexed-alpha and grayscale-alpha
fixtures. Consult the matching delivery report for exact outcomes and build
identity. These checks do not establish arbitrary-image recognition accuracy,
all PNG encodings or a complete installed Windows clipboard/chooser workflow.

## Fresh RGB8 Adam7 verification (2026-10-21)

This is a new implementation in the recovered derivative, not historical cloud
restoration. On the existing Windows Python 3.12.14 environment, all 15 authored
Adam7 tests and 51 selected existing preview regressions passed with no errors
or skips. Independent pixel fixtures cover small/odd dimensions, empty passes,
all five filters, per-pass resets, exact crops, nearest-center sampling, `tRNS`,
and unchanged source/provenance. Negative checks cover CRCs, invalid filters
outside selected regions, truncation, excess/concatenated streams, unsupported
encodings, cumulative expansion, output/buffer limits and cooperative deadlines.
The new branch's tests forbid production Pillow decoding. Independent code and
untrusted-input review found no blocking issue in the scoped decoder diff.

One authored 13x9 image also passed the existing backend API/session/job/result-
validation route: overview, exact 3x2 crop, and rejection of a cancelled result
after decoding. This component check injected metadata and preview worker
transport, an inert preparation dependency, unavailable optical configuration,
and a token-only server object. Actual parsing, decoding, result validation,
source identity checks and snapshot cleanup ran. It did not launch real image
workers, recognition providers, a browser or the native application.

Buffer accounting is a conservative explicit allocation budget, not a measured
whole-process memory bound. No large-image performance guarantee, native worker
termination qualification, GUI/installed acceptance or Linux result is claimed.
At that RGB8 checkpoint, RGBA8 Adam7 was still pending. The later increment
below adds that encoding; requirement 3.8 remains partial. Matching receipts
bind these results to their respective source hashes.

## Fresh RGBA8 Adam7 verification (2026-10-21)

The next bounded increment extends the same pass decoder to four-byte RGBA8
pixels. Every pass length, filter predictor distance and output copy uses the
source channel count. RGBA alpha and hidden RGB are copied exactly; RGB `tRNS`
behavior is retained. All existing stream, resource and deadline guards remain.
No full-source pixel allocation or production Pillow decode was added.

The independently Pillow-validated 2x2 RGBA fixture first produced the expected
missing-capability failure. After implementation, all 8 new RGBA tests, the
15 existing Adam7 tests and 51 affected preview regressions passed on the same
Windows Python 3.12.14/Pillow 12.3.0 environment, with zero errors or skips.
Literal expected pixels cover alpha zero with nonzero hidden RGB, partial alpha,
exact crop and nearest-center overview. Other checks cover all five filters,
odd/small dimensions, empty passes, CRC and full-stream faults outside the crop,
the exact 487-byte filtered budget for a 13x9 RGBA fixture, bounded decompression,
unsupported encodings, source/provenance nonmutation and cooperative deadlines.
Independent review found no blocking code/security issue in the actual diff.

One authored 13x9 RGBA fixture passed the same injected backend component route:
exact overview pixels, a 3x2 crop retaining hidden `(4,4,68,0)`, source hash and
coordinates, owned snapshot cleanup, and rejection of a cancelled late result.
Alpha values 0, 64, 128 and 255 survived in both outputs. Metadata/preview worker
transport, preparation, optical configuration and the token-only server were
injected as above; this is not real-worker, GUI/native or end-to-end qualification.

At that RGBA8 checkpoint, other Adam7 encodings and color-managed PNGs remained
unsupported. Requirement 3.8 remained partial. No new large-image performance, measured whole-process memory,
native termination or Linux validation claim is made. This is fresh derivative
implementation, not historical cloud restoration.

## Fresh indexed Adam7 verification (2026-10-21)

The next bounded increment adds indexed Adam7 at depths 1, 2, 4 and 8. Packed
pass rows retain their own filter state, use a one-byte predictor, and are
unpacked only for their actual pixel counts. Full-stream index validation
precedes selection. Existing palette/transparency rules, RGB/RGBA behavior,
non-interlaced decoding and all resource limits remain unchanged.

A literal 2x2 depth-2 fixture was independently decoded with Pillow before the
expected missing-capability failure. After implementation, 12 new indexed tests,
23 existing RGB/RGBA Adam7 tests and 51 affected preview regressions passed on
Windows Python 3.12.14/Pillow 12.3.0: 86 tests, zero failures/errors/skips.
All four depths cover all five filters, per-pass resets, odd/small dimensions,
empty passes, exact crop pixels, palette alpha and hidden RGB. Additional checks
cover literal nearest-center samples, ignored padding, invalid actual indices
outside crops and sparse overviews, malformed palettes/transparency/order,
CRC/filter faults, truncation, excess/concatenated streams, bounded decompression,
output/buffer budgets, cooperative deadlines and source/provenance nonmutation.
The existing selected cancellation/result-publication regressions also passed;
no new cancellation or timeout experiment was run.

One authored 13x9 depth-2 indexed fixture passed the actual ImagePreviewJobs,
WorkerSupervisor, worker_main, decoder and bounded-result route. Its exact 3x2
crop retained partial alpha and hidden `(10,20,30,0)`, with unchanged source hash,
coordinates and provenance, and metadata-free output. Child exit, completed I/O,
jobs shutdown and the empty owned cache were observed separately. Independent
actual-diff code/security review found no blocking issue.

That single normal worker completion uses the accepted adapted module bootstrap
with existing pinned package paths. It does not qualify unchanged default
dependency discovery, GUI/native interaction, arbitrary descendants, OS memory
enforcement, native-handle closure, timeout/cancellation behavior or Linux.
The decoder's buffer budget is not a measurement of whole-process memory.
At that indexed checkpoint, Adam7 grayscale/16-bit and color-managed formats
remained unsupported, and requirement 3.8 remained partial. This is fresh implementation in the derivative,
not historical cloud restoration or evidence about the owner's image collection.

## Final core Adam7 tranche: grayscale (2026-10-21)

Fresh implementation adds grayscale color type0 at depths1/2/4/8 and
grayscale-alpha color type4 at depth8. Together with accepted RGB8, RGBA8 and
indexed1/2/4/8 support, this completes the legal core color-type/depth combinations
at eight bits or below for this bounded decoder under its strict chunk policy.
It does not mean every PNG is supported or close requirement3.8 in full.

One literal2x2 grayscale2 fixture was independently decoded with Pillow before
the expected missing-capability failure. After implementation,13 new grayscale
tests,35 existing RGB/RGBA/indexed Adam7 tests and51 affected regressions passed
on Windows Python3.12.14/Pillow12.3.0:99 tests with zero failures/errors/skips.
All five new combinations cover five filters, per-pass resets, odd/small sizes,
empty passes, exact crop and nearest-center overview. Tests preserve original
sample tRNS comparison and masking, ignored padding, hidden gray/alpha, source
identity and provenance. Full-stream faults outside crop/overview selections,
transparency errors, CRC/truncation/excess/concatenated streams, bounded inflation,
buffer/output limits and cooperative deadlines are checked. Existing applicable
job cancellation regressions passed; no new real cancellation experiment ran.

One authored13x9 grayscale-alpha8 fixture passed the actual ImagePreviewJobs,
WorkerSupervisor, worker_main, decoder and bounded-result route. The exact3x2
crop retained alpha0/64/128/255 and hidden `(68,68,68,0)`, unchanged source bytes,
hash, coordinates and provenance, and metadata-free output. Child exit0,
completed supervised I/O, jobs shutdown and empty owned cache were verified
separately. Independent code/security review found no blocking issue.

The run reused the accepted adapted module bootstrap and existing pinned
dependencies. It does not qualify unchanged default startup, GUI/native/browser
workflow, Linux, arbitrary descendants, OS memory enforcement or new timeout/
cancellation behavior. Buffer accounting is not measured whole-process memory.
No16-bit expansion, color-management fidelity, animation, editable import or
recognition is added by this tranche. Other strict-policy exclusions remain;
requirement3.8 stays partial. This closes the bounded core-format tranche only.

## Adam7 16-bit source increment (2026-11-10)

New source support extends bounded pass decoding to grayscale, RGB, grayscale-alpha
and RGBA at 16 bits. Byte filter predictor distance is twice the channel count;
pass-row state remains full precision and resets at each pass. Selected rows reuse
the existing rounded sample reduction, with full-precision transparent-key comparison
before reduction and unassociated alpha/hidden colors preserved. Grayscale16 bypasses
the packed-sample unpacker. Output is still metadata-free 8-bit RGB/RGBA; no source
precision preservation is claimed for the derivative. Originals remain unchanged.

The direct Adam7 decoder does not use Pillow, full-source decoding or a full-source
pixel buffer. Existing positive scanline inflate limits, exact total length, all-row
validation, geometry, memory accounting and cooperative deadlines remain. The derivative
engine identifies interlaced16-bit decoding as version5 and records its actual color
family/sample depth; <=8-bit Adam7 and non-interlaced identity labels remain unchanged.

The dedicated handcrafted16-bit suite uses an independently enumerated pass map and
expected pixel arithmetic. It covers four legal types, all five filters, low-byte
predictors, small/odd/empty passes, pass resets, transparency near-collisions, hidden
colors, crop/overview geometry, outside-crop invalid filters, malformed streams, strict
metadata exclusions and resource limits. Combined with the inspected existing PNG
suites,101 tests passed in the source author's Linux/Python3.12.14 run with process
and network tripwires. An initial unsupported-format red case and an intermediate
syntax-edit failure are retained in the work evidence. Independent review is separate.

This is not a Windows native/worker/browser/clipboard/installed test or chemical
recognition validation. No child, provider or reference-corpus execution occurred.
Color management, animation and broader format fidelity remain excluded; requirement
3.8 is still partial. Historical tests below/above remain bound to their old source.

## Bounded sRGB declaration support (2026-11-11)

An otherwise supported source may contain one CRC-valid `sRGB` chunk, before
the palette and image data, with one rendering-intent byte in the range 0–3.
The derived 8-bit RGB/RGBA image retains that intent; the original bytes and
source hash remain unchanged. An untagged source still yields an untagged,
metadata-free derivative. No other source metadata is copied. Duplicate,
misplaced, malformed or out-of-range declarations reject. At this checkpoint,
`gAMA`, `cHRM`, `iCCP`, HDR, orientation and other unsupported chunks still
rejected, even when accompanying `sRGB`. No ICC conversion or general color
management was claimed.

Source-level and Linux worker checks do not establish browser rendering, color
accuracy, Windows, installed or screen-reader acceptance. Requirement 3.8
remains partial.

## Canonical sRGB fallback metadata (2026-11-11)

An sRGB-declared input may also contain one `gAMA` value of 45455 and/or one
`cHRM` set of 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000. These
chunks can appear in any order with `sRGB`, but each must be before the palette
and image data. The source validator verifies CRC, exact values, placement and
uniqueness; a fallback without a valid sRGB declaration rejects. These are the
[W3C PNG specification's sRGB fallback values](https://www.w3.org/TR/png-3/).
The derived image retains sRGB intent but omits the redundant fallback chunks;
it does not promise color support in older decoders that ignore sRGB. Source
bytes remain unchanged. Standalone or nonstandard gamma/chromaticity and ICC,
HDR, orientation and unrecognized extensions remain unsupported. This does not
implement arbitrary color transforms or establish browser/display accuracy.


## Explicit gamma with canonical sRGB primaries (2026-11-11)

An otherwise supported PNG without an sRGB chunk may supply both one `gAMA`
and one `cHRM`. The chromaticities must be exactly the canonical values listed
above. The gamma integer must be 1 through 2147483647, the positive range of
PNG's four-byte unsigned integer. Both declarations must precede PLTE and IDAT;
CRC, duplication and placement rules remain enforced. Missing either declaration,
zero/high-bit gamma, other chromaticities, ICC and HDR still reject. No primary
colors or gamma are guessed. Existing sRGB-declared handling stays unchanged:
its optional gAMA must still equal 45455, and its canonical fallbacks are not
used to transform its samples.

This new branch converts original color samples into an sRGB review derivative.
For normalized original color x, linear light is x raised to 100000/gAMA, then
encoded using sRGB's piecewise transfer function. The result is rounded once
to an 8-bit review color. A standalone gamma 45455 transfer is a power law, not
sRGB's piecewise transfer; merely retagging its bytes would be wrong.
16-bit original colors are looked up at their full original precision, never
converted from prematurely reduced 8-bit colors. Transparent keys are compared
against original samples first. Explicit alpha is never gamma-corrected and
retains existing rounded 16-to 8 scaling. Hidden colors are converted but never
zeroed, flattened or premultiplied. Indexed samples look up 8-bit palette colors;
index depth is not palette color depth.

The branch covers all legal core types/depths and both interlace modes supported
above. All source rows still undergo byte-filter and stream validation. Only
rows selected for the derivative are expanded/converted; a selected row can
include columns outside the crop. No source-sized raster or Pillow source decode
is added. A fixed per-request byte lookup table has at most 65536 entries, filled
without a Python-integer list. Table bytes are explicitly added to the unchanged
16 MiB pixel-pipeline accounting; row copies stay within the existing conservative
32-row allowance. Generation and conversion check cooperative deadlines, while
existing isolated supervision still controls wall time and cancellation.

Converted output contains only the existing 8-bit RGB/RGBA encoding and an sRGB
chunk with intent 1. This relative-colorimetric intent is the derivative's declared
policy, not metadata allegedly supplied by gAMA. Existing source intent is retained
for the unchanged sRGB input branch. Engine provenance adds a versioned
`colorConversion` record with the original gamma integer, original color depth,
and output intent policy; decoder identity is unchanged. No original profile,
chromaticity or gamma chunks are forwarded to the strict derivative validator.
Source bytes/hash, crop coordinates, nearest-center overview sampling, review
acceptance and separately opted-in recognition remain unchanged.

Fresh Linux source evidence:141 explicitly selected PNG tests passed, including
9 new gamma tests. A Decimal 60 forward reference checked every 16-bit sample for
linear gamma and all 256 samples for five gamma values; other tests cover all
core formats/depths, five filters, both interlace modes, palette/hidden alpha,
full-precision keys, crop geometry, malformed metadata/streams and limits. One
actual normal ImagePreviewJobs/WorkerSupervisor/worker_main completion validated
an Adam 7 RGBA 16 crop; cache cleanup, zero remaining active workers and shutdown
were observed. Existing selected cancellation tests passed; no new native timeout
or cancellation qualification is claimed.

These runs used Python 3.12 with Pillow 13.0.0 (LittleCMS 2.19 was present but unused).
Target Pillow 12.3.0 remains pinned and was not available for this run. RDKit and
webview were absent; no replacement engines or installations were used. This is
not Windows/browser/display calibration, installed acceptance, arbitrary ICC,
recognition-accuracy or complete gap 5 evidence. Originals were not modified.

## Significant-bit metadata compatibility (2026-11-12)

An otherwise supported PNG may contain one CRC-valid `sBIT` chunk before PLTE
and IDAT. Its exact length is one byte for grayscale, three for RGB or indexed,
two for grayscale-alpha, and four for RGBA. Every value must be positive and no
greater than the source sample depth. For indexed images the bound is always
eight, even when indices use one, two or four bits. A `tRNS`-implied alpha channel
does not add an sBIT byte. Duplicate, misplaced, zero, excessive, wrong-length,
truncated or checksum-invalid declarations reject. An optional truecolor PLTE
also requires sBIT to precede it; no extra ordering rule is imposed relative to
tRNS when there is no palette. See [PNG sBIT](https://www.w3.org/TR/png-3/#11sBIT)
and the [PNG chunk-order table](https://www.w3.org/TR/png-3/).

This is compatibility for display of the **stored IHDR-depth samples**, not
original-precision recovery. PNG explicitly permits a decoder to ignore sBIT
when displaying the stored image, while noting that recovering original samples
can yield a more accurate display. See [sample-depth rescaling](https://www.w3.org/TR/png-3/#13Sample-depth-rescaling).
No bit shifts or new color/alpha transforms are applied. For example, encoded
RGBA8 `(224,132,17,170)` with sBIT `(5,6,5,2)` remains exactly those bytes on the
untagged display path; its red is not reconstructed as 230. Existing 16-bit
rounding, original-precision tRNS comparisons, gamma conversion, hidden colors
and unassociated alpha stay unchanged. sBIT is validated and discarded from
the derivative, whose strict allowlist still admits only the existing core
RGB/RGBA encoding and optional sRGB declaration. No ICC or HDR support is added.

Decoder and color-conversion version labels remain unchanged because their
algorithms, output bytes and provenance schema did not change. The new behavior
is source-metadata admission only. Source hashes continue to cover all original
bytes, including sBIT, and the original source/review workflow is unchanged.
Chemical-metadata retention defaults, Add and Continue to 3D are untouched.

Fresh final Linux checks on Python 3.12.14 and the pinned Pillow 12.3.0 passed
149 explicitly selected PNG preview/job tests, including 13 new sBIT tests,
with zero failures, errors or skips. Another 17 existing metadata-option,
metadata-recovery and transparency tests passed separately. The new matrix
covers all 15 legal color/depth combinations, both interlace modes and all five
filters: 150 9x11 format/filter cases, including all seven Adam7 passes, with
literal expected display pixels.
Independent Pillow loading checks each fixture; multichannel16 expectations
come from the literal rounded-value table, not Pillow's truncating conversion.
Additional cases cover both legal depth endpoints, unequal color significance,
one-bit indices with eight-bit palette significance, distinct alpha significance,
tRNS near-collisions, 16-bit rounding, crop/overview, gamma/sRGB coexistence,
malformed/unsupported metadata, complete-stream errors outside the crop and
existing resource/deadline guards. General arbitrary-alpha fixtures declare full
alpha precision; lower-significance alpha fixtures use exactly scaled endpoints
or partial values. A pre-edit failure run and a final-test-against-baseline run
are retained separately; all 150 matrix cases failed old-source admission.

Four new normal completions exercised actual ImagePreviewJobs, WorkerSupervisor,
the default module worker command, worker_main, source snapshots, decoder and
strict result validation: crop and overview for non-interlaced and Adam7 RGBA16
with sBIT plus gamma. No worker transport, decoder, process result or bootstrap
was injected. Exact pixels/source hash, zero active workers, empty owned caches
and shutdown were checked. Existing selected Linux cancellation, output-limit
and timeout regressions also passed; this adds no native/Windows guarantee.

The earlier gamma run with Pillow 13.0.0 remains historical evidence. The new
Pillow 12.3.0 run refreshes the selected Linux PNG suite, not the historical
Windows tests, browser/display calibration, native GUI, installer qualification,
optical accuracy or held-out image-corpus validation. No dependency installation,
network provider, scientific predictor or public release was involved.
Requirement 3.8, broader PNG fidelity and the full Goal remain partial.


## Explicit-gamma D65/P3-primary PNG conversion (2026-11-12)

In addition to the canonical-primary path above, the decoder admits exactly
`cHRM=(31270,32900,68000,32000,26500,69000,15000,6000)` together with an explicit
`gAMA` integer from 1 through 2147483647. Both chunks must be unique, CRC-valid,
and before PLTE/IDAT. Any sRGB declaration with this tuple is contradictory and
rejected regardless of order. Missing gamma, changed coordinates, ICC, HDR and
other unsupported extensions still reject. This is not broad Display-P3 support:
gAMA is a power transfer, not an inferred piecewise Display-P3 transfer.

Original-depth samples are normalized and linearized with `x^(100000/gAMA)`,
then transformed with one fixed signed D65/P3-to-linear-sRGB matrix. Each linear
output channel is clipped to [0,1], sRGB-encoded and rounded once to 8 bits.
Clipping is an explicit bounded policy, not perceptual gamut mapping. No inverse
or white-point adaptation is computed from input values. The matrix follows
[W3C CSS Color 4 conversion constants](https://www.w3.org/TR/css-color-4/),
independently checked against exact rational xy-derived matrices. Its exact row
sums equal one; neutral samples use that identity. This proof concerns this exact
valid tuple, not arbitrary grayscale chromaticities.

All existing legal color/depth families and both interlace modes are supported.
Indexed palette colors always use eight-bit color precision, independently of
index depth. tRNS comparison still uses original sample precision; alpha keeps
existing scaling, and hidden RGB is converted without premultiplication or
flattening. sBIT continues to describe stored samples, without precision recovery.
Source bytes/hash and retention, selected crop coordinates, nearest-center
sampling, metadata-removal defaults and all worker controls are unchanged.

The linear lookup table is an exactly allocated packed `array('d')`, not a
Python-float list and not the existing eight-bit gamma table. Payload and measured
array header, plus the temporary one-element seed array during allocation, are
explicitly added to the existing 16 MiB accounting. At 16 bits on the checked
runtime this is 524368 table bytes plus 88 temporary bytes. The table and selected
row conversion check deadlines. All rows are still sequentially validated; only
selected rows undergo matrix conversion. No full-source raster is decoded.

Only the new branch emits `colorConversion.name = explicit-gamma D65/P3-primary
to sRGB`, version 1, with the original tuple/gamma/color depth, output intent 1,
`clip-linear-sRGB-channels-to-[0,1]` gamut policy and declared transfer policy.
Old-path conversion provenance and derivative bytes are unchanged. The strict
derivative allowlist stays eight-bit RGB/RGBA with sRGB intent 1 for conversions;
source gAMA/cHRM/ICC/text chunks are not forwarded. The bridge's bounded metadata
validator accepts this provenance without expanding its PNG allowlist.

Verification is synthetic and Linux-runtime scoped; it does not establish
Windows/browser/display calibration, installed behavior, arbitrary ICC support,
perceptual equivalence, private-image prevalence, or completion of all PNG gaps.

## Exact cICP Display-P3 conversion (2026-11-12)

The bounded decoder now admits one `cICP` chunk with exactly the four bytes
`0C 0D 00 01` (12,13,0,1): D65/P3 primaries, sRGB piecewise transfer, identity
matrix coefficients and full range. See [PNG3 cICP](https://www.w3.org/TR/png-3/#11cICP)
and the [Apple-supplied Display-P3 definition](https://www.color.org/chardata/rgb/DisplayP3.xalter).
The chunk must be CRC-valid, unique, exactly four bytes, and before PLTE/IDAT.
Other code points, transfer functions, narrow range and HDR remain unsupported.

Under this tuple, cICP takes precedence over admitted gAMA, cHRM and sRGB
chunks regardless of their relative order. Their values do not affect conversion,
including conflicting positive gamma, either of the two supported chromaticity
sets, or another sRGB intent. A gAMA or cHRM companion is not required. Structural
checks, uniqueness, lengths, ranges, CRC and placement still apply to each chunk.
The existing cHRM allowlist (canonical sRGB or exact D65/P3), positive 31-bit gamma
range and blanket ICC/unknown-extension exclusions are retained as **admission
policy**, even for lower-priority metadata. Thus some otherwise legal PNG mixtures,
including arbitrary ICC companions, are still unsupported. This is not a claim
that lower-priority color disagreement invalidates a PNG. Earlier no-cICP policies
and successful output bytes remain unchanged.

Normalize stored color samples at full IHDR precision (eight-bit palette colors
for indexed PNG), then decode with `x/12.92` for `x <= 0.04045`, otherwise
`((x+0.055)/1.055)^2.4`. Apply the fixed signed D65/P3-to-linear-sRGB matrix,
clip linear channels to [0,1], sRGB-encode and round once to eight-bit output.
This is not gAMA's power transfer and not perceptual gamut mapping. The same
neutral-axis identity and matrix used by the explicit-gamma P3 path apply.
Alpha, original-precision tRNS comparison, hidden color, sBIT stored-sample policy,
all legal core color/depth combinations, both interlace modes, crop coordinates
and nearest-center overview sampling retain their existing behavior.

The original-depth packed-double table has the same explicit table/temporary
accounting and deadline checks as the explicit-gamma P3 path. No profile parsing,
source-sized raster, native color engine or dependency is added. Complete stream
validation still includes pixels outside the selected region. Source bytes and
hash remain unchanged. The strict derivative contains only RGB/RGBA8 core chunks
and sRGB intent 1, never copied cICP/gAMA/cHRM/ICC. Provenance adds
`colorConversion.name = cICP Display-P3 to sRGB`, version 1, `sourceCICP`, original
color depth, output intent policy and explicit transfer/gamut policies. Existing
bounded result validation accepts it without changing its PNG allowlist.

Verification is synthetic Linux source/runtime evidence, not native Windows,
browser rendering, display calibration, held-out private-image accuracy, arbitrary
ICC support, installed acceptance, or completion of requirement 3.8 or the Goal.

## Exact cICP sRGB interpretation (2026-11-12)

The decoder additionally admits the exact four-byte cICP tuple `(1,13,0,1)`.
This is a narrow addition, not general CICP/profile/HDR support. [PNG3 §11.3.2.6](https://www.w3.org/TR/png-3/#11cICP)
defines the four fields by reference to ITU-T H.273, requires matrix coefficients
0 for PNG RGB, defines full-range flag 1, and gives understood cICP highest color
precedence. [ITU-T H.273 V4 (July 2024), Tables 2 and 3](https://www.itu.int/epublications/publication/itu-t-h-273-v4-2024-07-coding-independent-code-points-for-video-signal-type-identification)
identify primaries 1 as the D65/sRGB primaries and transfer 13 with matrix 0 as
IEC 61966-2-1 sRGB. The tuple is derived from those field definitions, not quoted
as a worked example from PNG3.

Already-sRGB samples need no transfer or gamut conversion. The existing stored
sample policy remains: eight-bit values stay exact, lower-depth grayscale expands
by its full-range integer scale, and sixteen-bit color/alpha uses the existing
rounded full-range eight-bit scale. Palette colors remain eight-bit independently
of index depth. Alpha remains unassociated, hidden RGB is retained, and tRNS keys
are compared at full source depth before quantization. Both noninterlaced and
Adam7 paths use the same interpretation, without allocating a color LUT.

Under either admitted cICP tuple, supported lower-priority sRGB/gAMA/cHRM chunks
are ignored for color interpretation regardless of their order. In particular,
cICP sRGB with lower-priority P3 cHRM must not select P3 conversion. Structural
validation and existing strict value exclusions remain: malformed, duplicate or
misplaced chunks, unsupported cHRM and ICC/HDR extensions still fail closed.
This strict subset does not declare all excluded inputs invalid under PNG3.

The derivative retains only its existing IHDR/sRGB/IDAT/IEND structure, with sRGB
intent 1 (relative colorimetric) as **local output policy**, not source-declared
intent. [PNG3 sRGB](https://www.w3.org/TR/png-3/#11sRGB) defines this encoding and
intent. No cICP or fallback profiles are copied. Provenance adds
`engine.colorInterpretation` with name `cICP sRGB`, version 1, `sourceCICP`,
`sourceColorDepth`, `outputIntentPolicy`, and sample policy
`stored-samples; scale-to-8bit; no-transfer-or-gamut-conversion`.
It does not falsely report a `colorConversion`. Original source identity, complete
stream validation, crop/overview geometry and resource limits remain unchanged.
Existing P3 conversion output and provenance remain unchanged. Tests are local
source/worker checks, not calibrated-display, Windows-installation or scientific
recognition acceptance.

## Bounded embedded ICC v4 display RGB intake (2026-11-12)

This addition supersedes the blanket ICC exclusion in the historical sections
above **only for the following deliberately bounded subset**. It is not arbitrary
ICC support or a complete ICC conformance validator; excluded profiles are not
necessarily invalid under ICC/PNG. No ImageCms/LittleCMS input-profile parser,
new dependency, profile-driven import, URL access or native CMS is introduced.

Supported inputs are RGB/RGBA8/16 and indexed1/2/4/8, in either noninterlaced or
Adam7 form. Indexed color uses eight-bit palette components, never index values.
RGB profiles paired with grayscale/gray-alpha reject, including under cICP.
Non-ICC grayscale behavior is unchanged. Profiles must be ICC v4.0–4.4 display
class (`mntr`), RGB input and XYZ PCS. Header intent 0–3 is retained as provenance,
not treated as a command. ICC v2, GRAY, CMYK, Lab, device-link, input/output and
other profile classes, LUT/CLUT and hybrid matrix/LUT transforms remain excluded.

One iCCP is allowed before PLTE/IDAT. Its 1–79-byte Latin-1 keyword must use PNG's
printable ranges, no leading/trailing/doubled spaces, a NUL separator, and method
zero. Names and descriptions are inert and never choose a transform or reach the
output. The entire zlib stream must end with valid checksum, no trailing bytes or
concatenated stream. Compressed bytes are capped at 256 KiB, inflated bytes at
1 MiB, with at-most-64-KiB input/output pieces, an excess-byte probe and cooperative
deadlines; no unbounded decompress or flush is used.

The header length must match the four-byte-aligned inflated length, `acsp`, exact
ICC D50 illuminant, valid date, and zero reserved bytes. Tag count is at most 64.
Signatures are unique. Tag offsets must be aligned and beyond the table, sizes
at least 8 and entirely inside the profile, with zero reserved tag bytes. Exact
shared spans are allowed; partial overlaps and same-offset/different-size spans
reject. Gaps and trailing padding must be zero and at most 3 bytes. Profile IDs
are opaque identifiers, not authenticated or verified ICC-MD5 claims.

Required tags are rXYZ/gXYZ/bXYZ, rTRC/gTRC/bTRC, wtpt, desc and cprt. Optional
chad, bkpt, lumi and chrm are admitted with strict structure checks; all other
tags reject. Text must be mluc, 1–64 records of size12, valid two-letter locale
codes and in-bounds even-length valid UTF-16BE spans; shared strings are allowed.
Each XYZ tag has one signed fixed-point triple. White point must agree with ICC
D50 within 2/65536. Source matrix coefficients have magnitude at most4, row sums
within 0.002 of D50, and absolute determinant at least1e-8; signed coefficients
and negative determinants are accepted. Optional chad is an exactly nine-value
sf32 matrix bounded by 16 and nonsingular. It is validated, **not applied twice**:
source columns are already PCS-adapted. Optional bkpt/lumi triples are bounded
by 16; black point is not applied. Optional chrm has exactly three xy coordinate
pairs, each in [0,1] with x+y<=1, and colorant type 0–4; it does not alter conversion.

TRCs may be curv identity, positive u8Fixed8 gamma up to 10, or 2–4096 monotone
unsigned16 entries with endpoints 0/65535 and piecewise linear interpolation.
Parametric type0 supports positive gamma up to 10. Type3 supports `(a*x+b)^g`
at or above d, otherwise c*x, with 0<a<=16, |b|<=16, 0<=c<=16, 0<=d<=1. The high-branch
base must be nonnegative at d; endpoints must be within 0.002 of 0/1 and the join
within 0.002, allowing downward rounding discrepancy only up to 2/65536. Curve
output clips to [0,1]. Other functions and domains reject. These are local numerical
admission restrictions, not universal ICC validity rules.

Metadata selection is cICP > iCCP > sRGB > gAMA/cHRM, independent of declaration
order. All admitted lower-priority chunks still undergo existing structural and
value checks; malformed/unsupported ICC rejects even under cICP. An active ICC
ignores lower gamma/chromaticity values, without requiring their companion.
Accepted cICP never constructs ICC sample tables or executes ICC conversion.
PNG3 recommends only one embedded profile absent cICP; this subset still admits
structurally valid sRGB+iCCP, with ICC selected, rather than misreporting that
recommendation as a mandatory invalidity rule.

Conversion uses original sample precision and separate channel TRCs, then source
matrix to PCS D50, fixed CSS Color4 Bradford D50-to-D65 and XYZ-to-linear-sRGB
matrices, linear channel clipping, standard sRGB encoding and one final eight-bit
rounding. The fixed local policy is media-relative colorimetric intent 1, full
adaptation, **no black-point compensation**, and no perceptual/saturation mapping.
It does not promise to honor source intent. Float64 numerical results are checked
against independently frozen Decimal analytical cases with tolerance one output
code per color channel, not physically calibrated display measurements.

Alpha is never transformed, premultiplied or flattened. Hidden RGB at alpha 0 is
converted normally. Alpha16 retains rounded scaling; tRNS compares full original
samples before conversion/quantization. sBIT remains stored-sample policy. Crop,
nearest-center overview, complete image-stream validation outside the selected
region, PNG CRC/filter/palette checks and source hashes remain unchanged.

A conservative 9,175,040-byte profile-work allowance accounts for simultaneous
profile/inflation/text copies, input tails, curve/tag objects and parsing scratch.
At most three 65536-entry packed-double tables add 1,573,104 bytes on this runtime,
plus the small seed-array temporary, measured using actual array header/item size.
The implementation checks profile processing before inflation and total pipeline
accounting before output/table allocation against the unchanged 16 MiB budget.
Row/output/source/transport exclusions remain explicit; this is not a process RSS
limit. Conservative accounting can reject otherwise valid large combinations.
All other source, output, wall-time and subprocess cancellation limits remain.

Derivatives contain RGB/RGBA8 core chunks plus sRGB intent 1 only. Provenance records
profile SHA256, source intent, selected metadata, conversion algorithm/version,
original color depth, explicit intent/BPC/gamut/numeric policy and profile/table
accounting. No source profile or untrusted description is forwarded. Raw input is
unchanged; invalid, cancelled or incomplete work cannot publish a derivative.

Evidence is synthetic Linux source/runtime verification: independently authored
linear, gamma2, sampled, parametric sRGB, swapped-primary, P3 and unequal-TRC
fixtures, malformed-boundary tests, all 80 supported format/filter/interlace
combinations, alpha/tRNS, crop/overview, precedence and actual supervised workers.
No vendor-profile ecosystem, browser rendering, Windows, installed-app, trusted
Arial, private corpus, recognition accuracy or physical calibration qualification
is claimed. Requirement 3.8 and the overall Goal are not declared complete.

References: [PNG3](https://www.w3.org/TR/png-3/),
[ICC v4.3](https://archive.color.org/specification/ICC1v43_2010-12.pdf),
[ICC White Paper35](https://archive.color.org/files/whitepapers/ICC_White_Paper35-Use_of_the_parametricCurveType.pdf),
and [CSS Color4](https://www.w3.org/TR/css-color-4/).
