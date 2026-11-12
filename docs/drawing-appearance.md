# Drawing appearance and PNG output

The Drawing appearance panel exposes line width, label size, multiple-bond spacing, hashed-stereobond appearance and element colors. Choose a spacing from `0.06` to `0.28`, then select **Apply appearance**. The starting value remains `0.14`. This controls the separation of strokes in double and triple bonds; it is not a pixel measurement.

Apply uses the existing drawing-style transaction. Undo and Redo restore the setting with the drawing. Opening a project or applying a saved rendering profile also restores its spacing. Invalid or empty values are rejected without committing a document change. Existing projects and profile revisions already store this field, so no file-format migration is needed. The control's increment is `0.01`; loading an existing valid value does not round it to that increment.

Appearance changes use the current layout without invoking 2D Clean. Editor PNG export, exact PNG preview and batch PNG export share the existing vector rasterizer. The requested total width includes the margins, height is automatic, and the output background is opaque white by default in both editor themes. The unchecked **Transparent background?** option enables alpha for drawings, exact previews and batch exports; see [PNG transparency](png-transparency.md).

## Extra label clearance

**Extra label clearance (%)** adds space around atom labels before nearby bonds are drawn. Choose 0% to 20% of label size, then select **Apply appearance**. The starting value remains 4%. The percentage is additional padding around the renderer's label box, not a promise that the visible gap between a particular glyph and a rounded bond cap equals that percentage. Increasing it can shorten the visible bond near a label; it does not move atoms or run cleanup.

The setting follows the same Apply, Undo/Redo, project and rendering-profile workflow as the other appearance controls. Empty, nonnumeric and out-of-range values are rejected without committing a change. Existing projects and profile revisions without `style.labelPadding` load with `0.04`. Loaded fractions are retained when the control is left unchanged. New records include this setting; older builds that do not recognize it may reject them.

The renderer uses RDKit's `additionalAtomLabelPadding`, which its [versioned option definition](https://github.com/rdkit/rdkit/blob/Release_2025_09_4/Code/GraphMol/MolDraw2D/MolDraw2DHelpers.h#L215-L217) defines relative to font size.

## Atom-label alignment

On the supported Windows target, atom labels use the installed regular Arial font. The renderer measures the font's glyphs and emits their vector outlines, using the same metrics for label placement, multi-character labels, isotope and charge annotations, and nearby bond clipping. The browser and PNG exporter receive those outlines; neither needs to substitute a font. Font files are not copied into the project or installer, and rendering needs no online font service.

Regular Arial must be readable from the Windows Fonts directory. Missing, unreadable, invalid or wrong-face font files produce a rendering error. Native-text fallback is rejected if the outline renderer is unavailable. These failures leave the existing document in place; restoring the required local font/runtime is separate from editing the molecule. Non-Windows font selection is not implemented in this Windows development candidate.

This replaces the earlier native-text mode, whose estimated character widths did not match the Arial text subsequently drawn by the browser. For example, the [pinned native-text implementation](https://github.com/rdkit/rdkit/blob/Release_2025_09_4/Code/GraphMol/MolDraw2D/DrawTextSVG.cpp#L66-L123) gives a standalone character an estimated width of 0.6 times the font size. The [font-aware implementation](https://github.com/rdkit/rdkit/blob/Release_2025_09_4/Code/GraphMol/MolDraw2D/DrawTextFT.cpp#L134-L208) instead uses actual glyph bounds. Shifting a single oxygen label or squeezing its width would leave the other estimated spacing and clipping unchanged.

Molecular coordinates and chemistry remain in the document. Accurate glyph bounds can change the scene's overall centering, label baselines, and the visible ends of adjacent bonds; old SVG and PNG bytes are therefore intentionally not preserved. Atom selection uses the renderer's corresponding scene positions. Existing style values, projects, profiles, and history remain applicable. The installed Arial revision is an environmental input, so a source hash alone does not identify font bytes on another computer. Reference-font identity and whole-corpus typography equivalence remain unverified.

## Optional hashed-stereobond appearance

This is a source development candidate. Consult its matching focused checks and package receipts for actual outcomes; previous checks do not verify a later candidate. The complete nivraM Goal remains active, and 3D remains deferred until the user explicitly resumes it.

**Hashed stereobonds** offers **Engine spacing** and **Thicker, spaced bars**. Engine spacing remains the default and preserves the existing engine SVG. Older projects and rendering profiles without `style.hashBondStyle` receive `engine`; their original files are not rewritten on load. New saved records include this optional field. Older builds that do not recognize it may reject them. The application and document version remain unchanged.

Thicker, spaced bars uses the configured full ordinary-bond width for each hash stroke. The bar count targets a provisional center spacing of approximately 3.4 stroke widths, using the nearest spacing achievable between the existing first and last bar centers. At least three distinct bars and a positive gap between full-width round-cap strokes are required. Short bonds may have a different achievable ratio; an unsupported or too-short depiction rejects the requested appearance with an instruction to choose Engine spacing. There is no silent rendering fallback.

Only hashes identified by the prepared molecule's actual stereobond direction are adjusted. Their first and last bar endpoints, widening orientation, bond/atom identity, molecular coordinates, scene atom positions and chemical stereochemistry are preserved. Intermediate marks are resampled uniformly along the existing straight taper. Other SVG paths, labels and filled wedges retain their original bytes. This appearance does not run 2D Clean.

Two-color hashes retain the engine's contiguous color ordering, selecting the existing color at the nearest original bar center. No gradient is added to a hash. Colored-hash transition appearance remains uncalibrated; the reference target below concerns black marks only. Supplied paint servers, URL paints and external references remain rejected by the existing SVG boundary.

The setting follows Apply appearance, Undo/Redo, project save/readback, rendering-profile application, exact PNG preview and batch export. Only `engine` and `spaced` are valid values; invalid types, values and unsupported style fields reject before document commit. Ambiguous classes, noncontiguous groups, unsupported paths or styles, inherited transforms/opacity, bent or reversed tapers and insufficient spacing reject the alternate render while retaining the previous drawing, style and history.

## Bond color transitions

Ordinary straight bonds joining different element colors use a short, continuous color transition around the existing midpoint. Its length is one stroke width, shortened if a very short stroke cannot contain that span. Both solid endpoint colors and the emitted path coordinates are retained. Double and triple bonds receive a separate transition for each eligible stroke. Same-color strokes, hashed or dashed strokes and unrecognized geometry retain their existing rendering.

Eligible two-color filled wedges now use a short linear transition at their existing shared color edge. Only the renderer's opaque adjacent triangle and widening quadrilateral with the same bond/atom classes, matching non-color styles, and an unambiguous straight taper qualify. The configured full bond width determines the transition span, shortened when necessary to fit the wedge. Both fill and outline use the same paint; the original vertices, atom coordinates, wedge orientation and chemical stereochemistry remain intact. Missing or invalid contextual width and ambiguous shapes retain their original paint. This extension does not cover hashed wedges.

The shared drawing-preparation step constructs local vector gradients only after validating the engine's SVG. Supplied gradient definitions, URL paints and external references remain rejected. Each prepared drawing and connected export clone receives independent paint identifiers, so another open review or preview cannot supply its colors.

Dark mode lightens the black end of these transitions only on the main drawing canvas. Review artwork and PNG exports retain the canonical element colors, on white by default or a transparent background when selected. Gradient painting does not add a cleanup operation or alter the chemical document.

## Reference comparison and limits

Private reference image identifiers, hashes, dimensions and measured pixel/color
statistics are not included in this public source tree. The implemented appearance
controls above preserve their existing constants and behavior, but their historical
calibration does not establish font identity, exact layout or gradient equivalence,
whole-corpus visual parity or Marvin JS cleanup compatibility. Colored-hash
transition appearance and independent held-out validation remain unverified.
Synthetic invariants and shared PNG consistency tests are separate from those
private comparisons; test definitions alone are not passing execution evidence.

## Numeric input integrity

Clearing a PNG margin field leaves it invalid until a number is entered. It no longer saves a zero margin or publishes a PNG/project from that incomplete entry. Explicit zero remains valid, and loading a valid fractional pixel margin retains its exact value rather than rounding it to the input control's increment.

The selected-atom charge and isotope fields use the same distinction between missing input and explicit zero. Both values are validated together on a detached graph before a render request. An incomplete, nonfinite, noninteger or out-of-range entry retains the current molecule and history; intentionally entering zero can clear the corresponding annotation when chemically valid. Ordinary valence validation still applies. Focused browser tests use directly imported carbon-13 ethanol and an explicitly constructed implicit-hydrogen ammonium fixture, and retain Undo/Redo checks for intentional edits.
