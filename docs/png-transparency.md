# Optional transparent PNG backgrounds

The image save controls include an unchecked **Transparent background?** checkbox.
White is the default. Checking it preserves PNG alpha instead of painting the final
white background. Drawing ink, dimensions, margins, colors, coordinates and PNG
metadata do not change. Transparency is not background removal from an imported
image: it changes the background of newly rendered exports.

## Scope and stored settings

- Main drawing PNGs use `export.transparentBackground`, a strict Boolean defaulting
  to false. The option follows document history, projects and rendering profiles.
  Old files without the option retain a white background. Restoring a project or
  profile explicitly saved with true restores that selection.
- Exact profile previews and batch PNG exports use the selected/frozen export
  settings. Their descriptions identify transparent versus white output.
- Supplied/generated/recovered 3D PNGs and both pH chart dialogs have their own
  initially unchecked option. The choice is captured when exporting. Screen theme
  changes do not change the exported light-theme ink or force a dark background.
- Native chart save calls carry an optional Boolean flag, default false. Text
  exports and existing callers retain their prior behavior.

## Validation and metadata

The bounded native PNG decoder keeps its existing CRC, compressed-stream, filter,
size and metadata checks. White mode still requires opaque pixels and white
margins. Transparent mode requires RGBA and zero-alpha margins; hidden RGB values
under fully transparent pixels are permitted. Interior alpha includes antialiasing.
All five PNG scanline filters retain their existing support. No coordinate metadata
schema changes: molecular PNGs retain coordinates; chart PNGs retain Software only.

## Evidence and limits

New non-browser tests execute the production vector export with an SVG/DOM adapter
and installed `@napi-rs/canvas`, plus actual pinned Chart.js for both charts. They
encode PNGs and inspect decoded pixel alpha, default white, unchanged geometry,
foreground, metadata, cleanup, option validation and native-save forwarding.
Python tests independently decode synthetic fixtures with Pillow and exercise the
production native decoder across all five filters, both modes, corrupted data,
cancellation, settings and chart-save validation. A mocked dialog tests saver
routing only; it does not constitute native GUI acceptance.

The preceding checkpoint fails the same raster regression because a requested
transparent margin still has alpha 255 instead of 0. Source inspection confirms
RDKit scene generation already disables background painting (`clearBackground=False`).
The installed canvas package is a test-only environment dependency, not a new app
runtime dependency.

Actual browser interaction, visual layout, Windows/WebView, RDKit-dependent source
suites and installed-app acceptance remain unverified for this change. The existing
browser socket restriction was not retried or bypassed. These are source changes,
not an installer or a claim that a user's installed application was updated.
