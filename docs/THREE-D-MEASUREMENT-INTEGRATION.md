# Generated and recovered 3D measurement integration

The optional viewer already supported measurements. This increment adds a missing
cross-module regression check; it does not change application behavior or expand
the generator's chemical domain.

The test drives actual viewer measurement controls in a controlled DOM through
the existing pinned RDKit 2025.09.4 disposable generation and recovery workers.
An ethanol drawing produces canonical source coordinates and a separately bound
generated-hydrogen display attachment. The test selects canonical atoms and an
oxygen-attached generated hydrogen for distances, angles and signed torsion.

Expected values come from RDKit's Cartesian `GetBondLength`, `GetAngleDeg` and
`GetDihedralDeg`, using accepted coordinates assembled separately from the
JavaScript display/measurement helpers. The printed four-decimal result must
agree within 0.00005001 in its stated unit. This tolerance checks display rounding,
not experimental accuracy. These are geometry calculations, not bond, energy,
stereochemical assignment or population predictions.

The same generated result is exported through the production metadata path,
reopened through the native schema6 recovery worker, measured again, and re-exported.
The test checks exact canonical MOL, hydrogen attachment and historical provenance
preservation, as well as the recovered/unverified-origin warning. It does not turn
an unkeyed checksum or internal topology consistency into authenticated history.

Further assertions cover default implicit-H selectors, explicit-H attachment labels,
selection clearing on H-display changes, model replacement and close, unchanged
measurements after rotation/zoom/style changes and failed replacements, source
revision invalidation, and nonmutation of the source graph/history. Disposable
mutation probes demonstrate detection of missing generated-H measurement wiring
and a reversed torsion sign; these are simulated regressions, not bugs found in
the accepted application.

## What this does not qualify

- The DOM and download sink are controlled test adapters. No browser, operating
  system save dialog, accessibility technology or Windows application was tested.
- PNG rasterization uses an explicitly synthetic 1×1 encoding fixture and an inert
  scene string. Only metadata/export/recovery control flow is exercised, not image
  appearance or coordinate-to-pixel correspondence.
- One generated ethanol fixture is not broad chemical-domain validation or proof
  of an observed physical structure. Source units are interpreted as Å.
- No recognition model runs, image reconstruction acceptance or full
  image-to-3D journey is established by this test.
- The transport adapter calls the real local worker services without HTTP. Existing
  HTTP/authentication tests are separate evidence.

Run with the approved pinned interpreter in `NIVRAM_TEST_PYTHON`, `PYTHONPATH`
including the source root and tests directory, and `PYTHONDONTWRITEBYTECODE=1`:

    node --test tests/three-d-measurement-interface.test.mjs

Exact executed results and source bindings are in the accompanying checkpoint.
The broader calculation-verifier, molecular-population-predictor, rendering,
recognition, browser/Windows and release gaps remain open.
