# Export supplied population results

Current background option (2026-11-11): white remains the default; the unchecked **Transparent background?** setting enables PNG alpha. Earlier white-only evidence below is historical. See [PNG transparency](png-transparency.md) for the changed validation contract and current limits.

Open a complete supplied model in **Open population model**, select a pH and
choose which curves to show. Three export buttons are available after calculation.
The drawing and its undo history remain unchanged.

These are mathematical results from a supplied model. Every format carries the
warning: **Supplied-model calculation; not a validated molecular prediction.**
Synthetic analytical models retain their synthetic labeling.

The separate [state structure file](population-state-adoption.md) used for 2D
preview/adoption is not embedded in numerical JSON/CSV exports. Retain that file
alongside the original model when reusing its supplied molecular structures.

## Formats

- **Export full JSON** saves the complete accepted result: every supplied state,
  all 141 pH positions, conditions, provenance, warnings, gaps and explicit
  unknown values. It also records the model filename and original text digest,
  selected pH, visible-state IDs and optional physiological reference context.
  Hiding a curve does not remove its data or renormalize its population.
- **Export full CSV** saves that same complete artifact as typed scalar rows,
  with columns `path,type,value,spreadsheet_escaped`. Paths are JSON Pointers.
  This is a lossless structured-data CSV, not a conventional one-row-per-state
  spreadsheet. String values that could be interpreted as spreadsheet formulas
  receive a leading apostrophe and an explicit recovery flag. Nulls, empty
  containers, booleans and numeric values retain their types.
- **Export chart PNG** saves a 1000 by 720 pixel image with an opaque white
  background, regardless of the interface theme. It captures the selected pH,
  all visible curves, gaps and exact reference bounds. The image includes model
  identity, its full signature, conditions, normalization and mass information.
  Its legend shows at most 12 visible states and reports the actual counts; all
  visible curves are still plotted. Long labels have an explicit notice that
  their full text is in JSON.

Original model-file numeric values and Python integer/float types are retained,
including values beyond JavaScript's exact integer range and repeated model
conditions. Numeric spelling may be compacted. See [numeric fidelity](population-numeric-fidelity.md)
for the binding, cancellation checks and offline verification boundary.

JSON and CSV are bounded to 32 MiB. Oversized output fails explicitly instead of
truncating results. The renderer bounds PNG output to 8 MiB; the native transport
independently allows at most 32 MiB and requires the exact image dimensions.

## Saving and cancellation

In the desktop app, choose a destination in the native save dialog. A success
message requires readback with a byte count and SHA-256 matching the prepared
file. Existing destinations use the same conditional replacement and verified
backup procedure as reference exports. Conflicts and uncertain outcomes are
reported separately from success. The original JSON, CSV and PNG bytes are
preserved after native validation.

In an ordinary browser, the file is prepared for its download/save flow. The
interface does not claim to have verified a file on disk in that mode.

Closing the population view or changing the drawing cancels unfinished export
preparation. A native save already dispatched retains its own bounded save and
shutdown lifecycle. Exports do not add a network request or require an AdGuard
exception.

## Focused verification

- JavaScript serializer and save checks: 6 passed, zero failures/skips, exit 0.
- Browser chart checks: 5 passed, zero failures/skips, exit 0. Real decoded PNGs
  were opaque and identical across light/dark themes. Captured selection,
  visibility, gaps, reference bounds, cancellation and encoder failures were
  exercised; live chart state remained unchanged.
- Production editor export flow: 1 passed, zero failures/skips, exit 0. A real
  local calculation produced all three downloads. The native Python validator
  accepted their exact bytes, including CSV line endings; the drawing and its
  revision remained unchanged.
- Native validator, saver, reference regression, bridge, lifetime and shutdown
  receipt checks: 88 passed, exit 0.

Native validation checks model/result schemas, all 141 sample positions,
arithmetic, selected sample, visibility and reference metadata. Model signatures
must agree across the envelope and results. The original Python JSON preimage is
not reconstructed from JavaScript-normalized number spellings. Neither that
signature nor the model filename digest establishes chemical identity or model
accuracy.

The real native chooser, installed-app export workflow, maximum-state rendering
performance, clean-machine installation and antivirus status of a newly built
installer are separate unverified boundaries. Packaging and native startup
receipts, when available, are recorded in the delivery report rather than treated
as proof of these boundaries.
