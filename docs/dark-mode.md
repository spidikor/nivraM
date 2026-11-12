# Display-only dark mode

Status: implemented in source; the remembered desktop preference requires its
own restart verification before an installed-release claim. Historical
production-route browser checks verified
keyboard activation, exact paint preservation, unchanged SVG/document/history,
and decoded PNG equality for the public fixture containing a methyl radical.
The exact original geometry regression also passes on Edge 153.0.4234.48, with
all six main/preview/batch outputs at 640x647 and identical decoded RGBA. Native
acceptance after the separate atomic-save repair remains a distinct gate.

## Behavior and boundary

The header's **Dark mode** button toggles the current window. It is a native HTML
button with a stable accessible name and aria-pressed state. The desktop app
remembers the last selected light or dark mode in its application settings and
restores it when reopened. The first session defaults to light. The preference
is independent of the disposable browser session, projects, and drawing/export
profiles. Closing immediately after a change also saves the final choice.
If settings cannot be read or saved, the app reports that limitation instead of
claiming the choice was preserved. A browser-only development page without the
native settings bridge retains per-window behavior. Actual Tab reachability, Enter and
Space activation, focus retention, and pressed-state changes passed in Edge at
1366×768 with device pixel ratio 1. This is not a native screen-reader or Windows
scaling assessment.

The controller changes data-theme on the HTML root and aria-pressed on the
button, and uses narrowly scoped native appearance operations for persistence.
It does not modify the molecular document or SVG. The native store accepts only
light/dark choices, writes atomically, and rejects stale page/sequence updates.
During ordinary close review the theme control is frozen and the latest choice
is committed before the window is destroyed; cancelling close re-enables it.
The browser check confirmed unchanged serialized molecular SVG, complete project
snapshot, and undo/redo availability after toggling and exporting.

Dark-mode CSS changes application chrome and the main editor background. Within
#canvas #drawing, canonical RDKit black bond stroke/fill (#000000) becomes light
grey (#d6d6d6). Separate stroke and fill rules cover bond-class paths, lines,
polygons, and polylines without recoloring colored bond halves. A narrow
additional rule covers RDKit's unclassed, unfilled open bond-join paths. It
excludes classed atom glyphs, circles, and filled radical marks. Stroke widths,
SVG attributes, and geometry are not changed. There is no global inversion or
image filter.

All atom-label ink retains its original color, including explicit carbon,
isotope, and hydrogen labels. A display-only neutral halo supports the visibility
of dark element colors and black labels. The white PNG preview remains available
for output review. Comprehensive chemical-artwork accessibility has not been
certified.

The exact-PNG preview, PNG import source/candidate panels, and cleanup comparison
canvases retain white artwork backgrounds. Their SVGs and the hidden SVGs used
for profile preview/batch export remain outside the main-editor color selectors.

## PNG isolation and bounds repair

Export serializes original SVG attributes into a standalone image without the
page stylesheet, then fills the output raster white. The theme does not change
canonical black or colored export ink.

The browser tests uncovered a separate bounds inconsistency: for the original
public fixture, main export was 640×646 while preview and batch were 640×647.
Those failures remain in verification/dark-mode-browser/contract-corrected.
The current exporter measures an untransformed clone on a temporary SVG stage
at a fixed scale so editor zoom does not select a different text measurement
context. The stage and clone are removed even when bounds measurement fails.
This is an export implementation change; earlier statements that export.mjs was
unchanged no longer apply. Profiles and batch rendering retain their existing
export calls.

Current identities recorded after the original-regression attempt:

| Module | SHA-256 |
| --- | --- |
| public/modules/export.mjs | eaca6915477df005adeccf25cc2b967f05b22bdcfc33ac447c5b829041638252 |
| public/modules/profiles.mjs | 2467ec0531c1fd58c1b7e6aa1662a9c80e4308f9d73b9fe078c3f7427d4853c6 |
| public/modules/batch-export.mjs | 303b2ecdc968a5509109f20cd6dc45093872398af1f5bde8859da8e035d689e1 |

## Measured browser results

The receipt at verification/dark-mode-browser/final-integrated/receipt.json
records 11 passed checks and no failed checks on 2026-09-20. It used actual
nivram.server routes, Edge 153.0.4234.32, one owned headless browser/server,
1366×768 viewport, and device pixel ratio 1.

- Exact paint comparison found 54 canonical black bond strokes, 2 black fills,
  and 14 neutral joins recolored as expected; 53 colored paint entries and 37
  text nodes retained their colors. Hashed and filled wedges were represented.
- The molecular graph contained one radical atom. The measured count of black
  circle/ellipse radical dots was **0**. This does not establish preservation of
  a separately rendered radical-dot primitive; do not infer that coverage from
  the presence of a radical atom.
- Main export, actual preview-image readback, and production batch renderer all
  produced 640×552. Every decoded RGBA value matched across all six light/dark
  outputs. Each image had 13-pixel opaque white margins, zero transparent pixels,
  and preserved black plus element-colored ink.
- All six decoded images had RGBA SHA-256
  8058c6d108208789a97449d5bf6dad35f1431ccc05857cd031b1645f6d00329f.
- Injected bounds failure left no temporary SVG stage and allowed the next export
  to succeed without modifying the molecular document.
- The real physiology comparison form preserved 7.35–7.45 inputs, single-value
  markers, prior valid values after four invalid proposals, context isolation,
  the source evidence gap, and the document.
- Page errors were empty. The harness blocked two requests to
  http://injections.adguard.org/. It does not prove that there were zero attempted
  non-loopback requests.
- Edge PID 70020 exited 0 through CDP Browser.close; server PID 49788 exited 0
  through shutdown/server_close. No forced fallback was recorded.

The profile-preview test uses an empty profile-list adapter while exercising
production rendering and the visible preview image. Native profile persistence,
native file dialogs, and batch file writing are outside this harness.

## Exact original regression and retained failures

The harness now supports NIVRAM_THEME_FIXTURE=original while keeping the radical
fixture as its default. Original mode uses the exact original public SMILES and
requires its imported #drawing serialization to equal the preserved failing SVG
at verification/dark-mode-browser/contract-corrected/original-drawing.svg before
accepting its geometry as a regression fixture. Both modes retain full per-path
and cross-path RGBA equality checks. Original mode correctly expects zero radical
atoms; the default radical mode still expects one.

The fresh original-regression run passed JavaScript syntax validation, then
exited 1: the production server did not announce readiness within the existing
20-second startup limit. The Node test ran for 28.798 seconds. No browser was
launched and no browser assertions ran. Its receipt has an empty checks list and
confirms that owned server PID 81480 exited 0 through shutdown/server_close. The
readiness delay's cause is unknown: the harness did not retain its in-memory
child stdout/stderr on that timeout path. No timeout was increased and there was
no blind retry.

Artifacts are verification/dark-mode-browser/original-regression/receipt.json
and verification/theme-browser-original-regression.txt. This failure does not
verify or falsify the current export repair on the original geometry. The later
radical fixture changes layout, so its passing 640×552 result must not be used as
proof that the exact earlier 640×646/647 discrepancy was repaired.

Earlier launch-permission and cross-path failures remain preserved. Their
historical boundaries remain distinct from the successful final-integrated
fixture. No antivirus settings were changed and the flagged conversation log
was not accessed.

## Remaining runtime gates

- Exact-original-fixture regression now passes on the current browser; broader
  corpus, platform and font behavior remain separate gates.
- Verify new imports and edited drawings, document replacement, and toggling
  while render/export is pending; the current fixture covers a settled import.
- Verify full control reachability at 1920×1080 and real Windows scaling,
  screen-reader behavior, and chemical-artwork contrast.
- Run native acceptance against the eventual rebuilt artifact, including actual
  dialogs, saved profiles, batch file writing, and installed-app lifecycle.

Pure controller tests cover state/ARIA synchronization, independent windows, and
the attribute-write boundary. Their three passing cases and earlier absent-module
failure are preserved separately in verification/theme-pure-after.txt and
verification/theme-before.txt; pure tests are not substitutes for rendering.

## Later native observations

The exact installed source-60b27810 candidate passed the synthetic ethanol dark-mode check: two canonical black bond strokes became grey, PNG bytes were identical in both themes, SVG/document/history were unchanged, and light mode was restored. Overall installed acceptance failed later at a PNG-import UI wait, so it was not a full installer pass. The independent next-revision source run (`verification/native-png-stages-source-1.json`) passed all 18 recorded checks, including the theme check, PNG metadata import/undo, a 9999x744 PNG with 200px opaque white margins, and normal WebView2 shutdown with managed cache removal. That source result does not establish that the packaged failure is fixed. The diagnostic revision changes only acceptance observations; production behavior and wait budgets are unchanged.

## Exact original fixture recheck, 2026-09-20

After a bounded production-server probe reached readiness in876ms with empty
stderr and normal shutdown, the unchanged original-fixture harness passed12
checks (Node exit0). The drawing exactly matched the preserved failing SVG.
All six outputs were640x647, with zero differing RGBA bytes, opaque white13px
margins and preserved artwork/document/history. RGBA SHA-256:
762e26126770adf7b8194f3d99e34394376ab5ea61214aabb2ef7eb23e434298.
Receipt: verification/dark-mode-browser/original-verified-20260920/receipt.json.
Both owned browser and server exited0 normally. The two blocked AdGuard
injection requests remain a boundary on network claims. Edge had updated from
153.0.4234.32 to153.0.4234.48: current contract behavior is verified, but this is
not a controlled comparison attributing the historical change solely to code.
The earlier startup failure and original export discrepancy are retained.
