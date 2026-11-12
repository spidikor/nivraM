# SVG artwork export

**Export SVG artwork** exports the current main editor's 2D drawing as standalone
vector artwork. It does not clean, rotate, reflect, rearrange or chemically
reconstruct the drawing. No network or chemistry request is made by the exporter.
The document, selection, history, saved state and project destination are unchanged.

## Appearance and file contract

- Uses the existing width, margin units/value, transparency and filename controls.
  Width (1–9999) and automatic height (at most 32,767) are nominal display pixels,
  not a raster resolution. Large vector images do not allocate a raster buffer.
- Preserves current path geometry, outlined atom labels, wedges, hashed bonds,
  ordinary bond paint and generated vector color transitions. Element colors and
  black light-theme ink are used in either editor theme. The canvas view/zoom,
  selection handles, warnings and transient tools are not exported.
- Opaque white is the default. **Transparent background?** omits the background
  rectangle. Current source scene SVG is sanitized again, then local gradient
  definitions are regenerated. No external paint, scripts, linked images, fonts,
  animation or embedded raster is admitted.
- Bounds come from vector geometry with a conservative stroke/miter/square-cap guard. Margins
  surround those guarded bounds. Unlike PNG's sampled exact-ink crop, SVG does not
  promise an identical painted-pixel crop or exact apparent whitespace. Nominal
  height is rounded up and content is vertically centered.
- This bounded version supports the renderer's outlined, untransformed artwork.
  Native text, nested SVG viewports and transformed child elements fail explicitly
  instead of risking missing fonts or clipped strokes. There is a 4-million-character
  source limit, 100,000-element limit and 8 MB serialized UTF-8 output limit.
- File type is `image/svg+xml`, extension `.svg`. The desktop adapter retains its
  current-origin blob check, exclusive active download, native-path validation,
  overwrite consent, cancellation and shutdown rules. A prepared download is not a
  completed save. Finish the save dialog and verify the chosen file.

## Chemistry and privacy

SVG is **visual artwork**, not a nivraM project or chemical interchange record.
No coordinate snapshot, SMILES, graph, source document, runtime record or other
recoverable-chemistry metadata is embedded. Engine atom/bond classes and source
IDs are removed; only local gradient IDs needed for paint remain. Visible molecular
features still disclose the depicted structure. Save a nivraM project or 2D MOL
separately for chemistry editing. This feature does not add SVG import, 3D SVG,
batch SVG export or the separate planned PNG metadata-removal option.

## Failure and qualification

Invalid artwork, options, dimensions, serialization, cancellation or a stale
revision produces no download. Temporary measurement elements are removed even
when measurement fails. The job and busy-state guards follow the existing export
workflow. SVG generation is synchronous within a bounded source size; it cannot
interrupt the browser in the middle of a synchronous DOM/layout call. Close review
prevents starting another export, and the native download lifecycle handles a save
already dispatched.

Tests exercise production sanitizer/paint/export logic using controlled DOM geometry
and a real XML parser, pure margin/aspect-ratio math, production entrypoint/store/job
contracts and controlled native download states. They do not constitute browser
rendering, visual pixel comparison, real Windows dialog, packaging or installed
acceptance. Subsequent user-authorized installation supplies the pinned Linux
RDKit 2025.09.4, NumPy 2.3.5 and Pillow 12.3.0 in an isolated environment. Chemistry,
chemical integrity, properties, MOL stereo, conformer/worker, stereo-guidance and
PNG gamma checks pass there. Full fragment/render tests now run but stop at the
missing trusted-system Arial Regular font. No substitute is used. Actual SVG
rendering, browser and Windows acceptance remain unqualified; existing broader
chemistry, native shutdown, image-recognition and scientific holds remain.
