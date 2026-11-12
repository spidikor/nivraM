# Reviewed PNG graph to optional 3D: offline integration

This test-only increment closes a specific cross-module coverage gap. Existing
image-review tests covered acceptance receipts; bridge/controller tests used
synthetic graphs and responses; the existing real conformer interface test seeded
PNG provenance directly in a document store. The new test connects acceptance of
a PNG metadata draft to explicit continuation and the real bounded generator.
No production code is changed.

## What runs

`tests/image-to-3d-interface.test.mjs` invokes the companion Python helper through
an explicitly selected `NIVRAM_TEST_PYTHON` interpreter. It requires the existing
project-qualified RDKit 2025.09.4 environment and performs no installation.

1. Construct a small valid PNG carrying 2D MOL metadata, then use the actual
   `ImageImportSession` and its metadata worker to inspect it. Select a component
   and prepare acceptance; verify the session preserved the exact PNG bytes in a
   test-owned temporary directory. The temporary data is removed after capture.
2. Replay the actual inspection/preparation records through `createImageReview`.
   Use a real document store and a controlled editor adapter returning its actual
   successful commit snapshot, matching the app's receipt shape.
3. Check review acknowledgement is required and the draft stays isolated until
   acceptance. Correct an ethanol draft to ethylamine and prove the corrected
   graph, not its baseline, is the generator input. Separate unchanged fixtures
   exercise specified stereochemistry and isotope/charge identity.
4. Pass the accepted receipt to `mountImageTo3d` and `createConformerController`.
   Acceptance and offering continuation make zero generation calls; only the
   explicit controlled click calls the real `conformer.run_request` worker.
5. Verify source graph/context and output hashes, metadata provenance, accepted
   atom IDs, generated labeling, and chemical identity by independently parsing
   the output MOL with RDKit. MOL and scene coordinates agree to 10^-6 Å.
   Original metadata coordinates are retained to 0.00011 drawing units, allowing
   the fixture's four-decimal MOL serialization. Signed zero normalization is
   permitted by the document's existing JSON clone and moves no coordinates.
6. Check 3D generation leaves the accepted 2D graph, coordinates, provenance and
   revision unchanged; one Undo restores the preceding document. Private source
   names/storage and embedded correction graphs do not enter exported context.
7. An edited accepted receipt makes zero backend calls. For edit/cancel during
   delivery, hold an actual completed worker result, invalidate/cancel, and then
   deliver it late: no model is adopted and the matching cancellation request is
   sent. This is late-response/UI-controller cancellation coverage, not a test of
   interrupting an active native worker.

## Deliberately controlled or excluded

- The PNG is an 8×8 white fixture with MOL text, not a molecular raster recognition
  example. Recognition is explicitly `not_run`. No optical model, model accuracy,
  chemical correctness of image correspondence, or physical structure is claimed.
- The local environment has no qualified system Arial. The fixture therefore
  explicitly replaces only `nivram.scene.render_scene` with a normalized-style
  test response during candidate/preparation. No alternate font is selected and
  no rendering or visual fidelity claim is made.
- Source-image review acknowledgement, editor rendering/commit adapter, native
  receipt transport, and DOM events are controlled. The real desktop editor,
  toolbar event wiring, browser layout, native file dialogs, HTTP transport and
  Windows packaging are not exercised.
- Generated conformers remain generated coordinates, not experimental structures,
  bound poses or demonstrated global energy minima. This test does not extend
  supplied-3D coordinate/provenance validation or repeat the measurement work.

## Run

From the source root with the existing approved scientific interpreter:

```sh
NIVRAM_TEST_PYTHON=/absolute/path/to/python PYTHONDONTWRITEBYTECODE=1 \
  node --test --test-concurrency=1 --test-timeout=120000 \
  tests/image-to-3d-interface.test.mjs
```

The 2026-11-12 focused verification also ran `image-review`,
`image-optical-review`, `image-to-3d`, `continuation-controller-race`,
`conformer-source`, `conformer`, and `conformer-interface` JavaScript suites:
**55 tests passed, zero failures or skips**, including four new tests. The final
run was 8.75 seconds in this environment, not a performance benchmark. Historical
failed attempts are retained separately: first, missing Arial during rendering;
second, two fixture assertions distinguished JSON-normalized +0 from -0. Neither
was a demonstrated production defect. No production behavior was relaxed.
