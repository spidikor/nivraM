# Explore a supplied population model

This source increment adds a local model-file chooser, population curves and a
state table. It calculates a model supplied by the user. It does not predict
chemical states or energies from the drawing, verify the model's chemistry, or
establish that its input identity matches the drawing.

## Use

1. In the maraPssiwS panel, select **Open population model**.
2. Select **Choose model JSON** and open a UTF-8 JSON file, at most 2 MiB.
3. Review the source, input identity, conditions, equilibrium scope and warnings.
4. Move across the plot, use its arrow/Home/End keys, or enter an exact 0.1 pH
   value. Each control reads the same cached calculation.
5. Toggle curves with the state checkboxes. Hidden states retain their original
   percentages; the mass summary reports them separately.
6. Export the full results as JSON or typed CSV, or save the visible chart as an
   opaque-white PNG. See [population exports](population-exports.md) for the
   formats and their limits.
7. To inspect a state's molecular drawing, choose **Inspect** in its table row
   and load a matching structure file. Review the 2D preview before explicitly
   using it as one undoable edit; see [state adoption](population-state-adoption.md).

The chart covers pH 0 through 14 in 141 positions. Values outside the supplied
model's domain remain gaps. A model with unknown omitted mass remains explicitly
conditional on the represented states. Closing the window or changing the
drawing clears the session's result. The drawing and its undo history are not
modified until an explicitly reviewed 2D state is adopted.

Choose an evidence-qualified pH reference context inside the open model window.
Changing it updates the reference lane without reopening the model or recalculating
its populations. The selected pH, visible curves, inspected state and pending state
preview remain unchanged. The selection is shared with the editor's reference
controls, including existing session-only custom comparisons for each context.
Edit custom comparisons in those editor controls before opening the model.

Exact reference bounds and statistical meanings remain separate from the model
calculation. These references do not validate in-vivo populations. Curves still
work if the reference dataset is unavailable; the reference selector stays disabled
until it is available. Late reference loading does not reset the current model.
See [reference switching](population-reference-context.md) for failure handling and
the offline verification boundary.

## Model file

The file must contain the complete object produced by `PopulationModel.to_dict()`
in `nivram/populations.py`: `states`, `conditions`, `provenance`,
`reference_state_id`, `supported_classes`, `equilibrium_scope`,
`equilibrium_justification` and `ensemble_complete`. Every state, condition and
provenance field is required. Missing scientific values are rejected. This input
is a model object, not a saved result envelope, raw pKa list or drawing project.

[The analytical pKa-7 example](../examples/population-analytical-pka7.json) is a
synthetic two-state demonstration. It gives 50/50 at pH 7 and a tenfold A/HA ratio
at pH 8. It is labeled synthetic in both the file and the interface. It is not an
analysis of any actual molecule. See [the scientific contract](scientific-feasibility.md)
for the energy, identity, degeneracy and ensemble requirements.

The current file route does not accept per-sample externally supplied omitted
fractions. An incomplete model therefore retains unknown omitted mass. The
underlying mathematical API's richer omitted-mass support is unchanged.
The file route rejects a returned omitted-mass value or source that was not
supplied by its request; it does not silently discard or normalize that claim.
For a complete model the existing implicit zero remains a model declaration,
not an independently verified measurement.
This model-only evaluator computes every sample inside the declared domain or
rejects the request. The controller rejects an in-domain `unavailable` result;
outside-domain gaps remain explicit and are not extrapolated. The generic
envelope contract still supports unavailable samples from other adapters.

## Integration and limits

The authenticated loopback route `/api/populations/evaluate` accepts a unique
request ID, the exact UTF-8 model text and its SHA-256, plus drawing revision and
graph digest as freshness context. That context is not proof of chemical
correspondence. The returned full model is checked against the supplied model
before the result is displayed. The service now independently verifies supplied-model
result arithmetic and the producer metadata/domain contract before publication;
see [result verification](population-result-verification.md). Client envelope
validation remains an additional check, not the sole arithmetic control.

The file route additionally checks original and returned numeric tokens, including
each repeated condition record, and preserves exact model numeric values/types in
JSON and CSV. See [numeric fidelity](population-numeric-fidelity.md). Numeric token preservation alone does not verify arithmetic. The separate service
verifier now binds returned arithmetic to the original model; hashes remain
identifiers, not cryptographic authentication.

The evaluator allows one owned worker at a time with a 60-second subprocess
timeout. Parent parsing and verification are outside that timeout.
Cancellation has a separate local route and supports cancellation before worker
registration. Shutdown cancels owned work. Request transport is capped at 8 MiB
to accommodate escaped JSON; result transport is capped at 16 MiB. All 141
sample positions and all supplied states are retained, up to the existing
4096-state limit when the complete response fits. A response exceeding the limit
fails explicitly instead of truncating states or provenance. No model download,
provider request, new scientific dependency or drawing write is required.

## Verification and boundaries

- Backend and existing server checks: 25 passed, exit 0. These exercise real
  authenticated HTTP and a real owned mathematical worker, cancellation,
  arithmetic, model validation, byte limits and unchanged capability declarations.
- Existing population-view checks: 17 passed, exit 0.
- Pure calculation-controller checks: 10 passed, exit 0. They cover exact model
  binding, response tampering and byte limits, cancellation, stale responses,
  document edits, timeout, malformed input and disposal.
- Chart browser checks: 2 passed, exit 0. They cover cached selection, unchanged
  percentages, gaps, unknown/known omissions, exact reference bounds, theme and
  operation without reference data.
- Production editor integration: the complete model-file-to-worker-to-chart
  flow passed, with an unchanged document, correct analytical values and no new
  evaluation on pH selection or visibility changes. Invalid input starts no
  calculation. The owned browser and server exited normally.

AdGuard remained enabled. Browser fixtures blocked additional AdGuard-injected
script requests; this is not a system-wide network-isolation result. No changes
to AdGuard settings are required for this supplied-model feature.

The base chart feature was included in the delivered installer with source
digest beginning `69ffc0dca66c`. The export increment is documented separately in
[population exports](population-exports.md); consult its delivery report for
packaging status. Mathematical and interface checks do not establish scientific
predictive accuracy, clean-machine installation or antivirus results for a new
installer. Supplied 2D state adoption has its own scoped checks and limitations.
Validated prediction, physical 3D conformer adoption and broader scientific
acceptance remain open.
