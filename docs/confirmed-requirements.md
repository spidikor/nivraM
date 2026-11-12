# nivraM — Confirmed export requirements and reference-archive audit

Recorded: September 18, 2026.

## Confirmed scope

nivraM is an independently implemented local Windows chemical drawing application. The visual references are Marvin-family drawings, not ChemDraw. A button labeled `2D Clean` must target Marvin JS behavior; a differently named or visually similar layout engine does not establish equivalence. This document records requirements and initial archive inspection, not completed software.

## PNG export contract

- Output: PNG with a fully opaque white (#FFFFFF) background, independent of the editor theme.
- User-selected total output width: integer pixels, up to and including 9999 px. This is the final image width, including margins.
- Height: automatic, calculated from the full visible structure bounds while preserving aspect ratio. The 9999-pixel width limit is not an implicit height limit.
- Default margin: 2% of the requested output width on EACH of the four edges. Round upward to a whole pixel. No additional 16-pixel or 20-pixel minimum is selected.
- Advanced settings: allow a percentage-of-width or fixed-pixel margin override, with a reset to the 2% default.
- Measure bounds including visible bonds, strokes, atom labels, hydrogens, charges, isotopes, stereobonds, brackets, and included annotations. Account for antialiasing and avoid clipping.
- Export the current layout. Do not silently run `2D Clean` as part of PNG export.
- Rasterize the molecular drawing at the requested resolution rather than enlarging a screen capture or a reference PNG.
- Reject empty drawings, invalid widths, margins leaving no drawable width, and unsupported raster allocations with explicit errors. Do not silently distort, clip, or lower the requested resolution.

For a requested total width W, use p = ceil(0.02 * W) pixels on each edge. If the measured visible drawing bounds have width b_w > 0 and height b_h > 0:

    content_width = W - 2*p
    scale = content_width / b_w
    H = ceil(scale * b_h) + 2*p

The bounds must include all rendered marks. Raster rounding can introduce at most the intended pixel-rounding adjustment, not a new fitting or cleanup step.

| Total output width | Default margin on each edge |
| ---: | ---: |
| 1000 px | 20 px |
| 4000 px | 80 px |
| 9999 px | 200 px |

## Open acceptance gates

This file records the approved output and cleanup requirements.
The alpha uses a local RDKit backend, not Marvin's cleanup algorithm.
Windows execution, whole-corpus renderer agreement and Marvin JS behavioral
equivalence remain unverified. The original reference archive was not modified
and is not bundled here. Private reference-derived measurements are omitted
from this public source tree; see PUBLIC-SOURCE.md.
