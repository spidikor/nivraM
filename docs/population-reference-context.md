# Change a supplied model's reference context

The supplied-population dialog has an evidence-qualified context selector. It
updates the existing chart's reference context without closing the dialog,
reopening the model, or evaluating the model again. Selecting no reference removes
the lane. Source points, uncertainty qualifications, exact interval boundaries,
and missing numerical evidence retain their existing meanings.

The selector shares the editor physiology explorer's accepted selection and
per-context session-only custom comparisons. It does not create a second comparison
store. Existing comparisons return when their context is selected again. Custom
comparisons are edited in the editor reference controls before opening the model.
No comparison is saved into a molecular document or made into a scientific source.

Changing reference context preserves the accepted model, fractions, selected pH,
curve visibility, inspected state and pending supplied-state preview. It does not
modify the molecular document, its revision or history. Late reference-data loading
updates the same chart rather than creating a new model session. If reference data
is unavailable, the selector remains disabled and supplied-model calculations
remain available. Ordinary close, document edits and close review still revoke the
model session and pending state work.

## Export and failure handling

The local reference selector is disabled during export and adoption. Each export
uses its captured accepted model/reference snapshot. An accepted shared-context
change arriving during export does not change that snapshot; subsequent exports
use the new context.

Replacement reference data and the initial subscription are validated/staged
before replacing a working binding. A broken observer does not prevent later
observers from receiving the accepted selection; observer failures are reported
after notification. The accepted selection is not silently reverted.

If the population chart cannot apply a reference update, its preceding reference
snapshot, text and layout are restored where possible. An explicit error disables
and guards new population exports until reference synchronization succeeds. A
persistent rendering failure can also prevent display restoration; exports remain
blocked and the error remains visible. The model and state-review session are not
reset. Choosing another context retries synchronization.

## Verification boundary

`tests/population-context.test.mjs` exercises the production explorer, population
dialog, chart, model controller and serializers using a small controlled DOM and
Chart adapter. It covers shared selection, accepted custom comparisons, exact
bounds, gaps and point semantics, current JSON/CSV, in-flight export snapshots,
late data loading, unchanged model/pH/visibility/state, pending preview ownership,
close/document invalidation, invalid replacements, failed initial subscription,
failed observer notification and failed chart updates with recovery.

The PNG adapter test checks captured drawing labels and export lifecycle, not
raster appearance or decoded plot pixels. No real browser, Windows native host,
keyboard/focus accessibility or installed workflow acceptance is established by
these tests. The physiology dataset bytes are unchanged. This is a workflow
increment for requirements 3.11/3.12, not new biological evidence, a molecular
predictor, or completion of those requirements.
