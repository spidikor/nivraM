# Mandatory naming verification

The validated input molecule requires independent identity verification whenever an atom has an explicit isotope or radical electrons. A configured OPSIN path alone is insufficient. Both `name` and `generatedName` are suppressed unless the top-level outcome is `round_trip_matched` and the supported molecular graph comparison is `matched`.

Without verifier configuration, these inputs remain `unsupported` and generation is not attempted. With configuration, unavailable, warning, failed or invalid-chemistry verification cannot expose a candidate. An otherwise `generated_unverified` outcome becomes `unsupported`. Mismatch, timeout, failure and cancellation states retain their distinct diagnostics. A late cancellation after a successful comparison still prevents adoption. The API supplies these fields to the analysis UI, which displays `name` directly.

Ordinary non-isotopic, nonradical inputs retain existing `generated_unverified` behavior when verification is absent or unsuccessful. Their mismatch response continues to retain `generatedName` for diagnosis while withholding `name`.

Naming works from a copied input snapshot; it does not rewrite the source drawing. Result identity and document revision remain bound to that initial snapshot even if the caller changes its graph during the job. The unchanged comparator checks supported connectivity, isotopes, radicals, charge, stereochemistry and fragments without tautomer normalization. Atom maps are drawing annotations excluded from naming semantics with an explicit warning. Matching does not establish a Preferred IUPAC Name; `preferredIUPACName` remains false.

## Bounded regression checks

Python tests:

- `tests/test_naming_required_verification.py`: isotope/radical/ordinary branches, preserved errors, cancellation, snapshot binding, missing local files and checksum gates.
- `tests/test_naming_adoption_routes.py`: actual local HTTP handler, candidate suppression, response identity, trusted server configuration and late cancellation.
- Existing `tests/test_naming.py` and `NamingWorkerContractTests` in `tests/test_naming_workers.py`: ordinary naming, supported graph comparisons and worker cancellation behavior.

Node checks:

- `tests/naming-adoption-dom.mjs`: actual analysis UI module with a controlled DOM/fetch environment and recorded HTTP payloads.
- Existing `tests/analysis-request.test.mjs` and `tests/jobs-timeout.test.mjs`: response identity, replacement, cancellation and timeout adoption boundaries.

These tests use controlled candidate generation and controlled verifier responses. Missing-path/checksum handling and supported graph comparisons run through their actual local implementations; local HTTP and JavaScript module entrypoints are exercised. Placeholder Java files are never executed. Passing these tests does not certify real NISPO inference, actual Java/OPSIN parsing, naming quality, comprehensive chemistry, Windows packaging or browser rendering. No dependency installation or external naming provider is required by these bounded tests.
