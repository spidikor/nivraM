# Optional PubChem name lookup

The IUPAC Name panel offers a separate **Lookup in PubChem** action. Local systematic naming and all core editor operations remain independent of this optional service. No PubChem connection is made at startup, during property calculation, or merely when opening the naming panel.

## Review and consent

The first action prepares a local, immutable snapshot. Its review shows PubChem (NCBI/NLM), the exact canonical isomeric SMILES to be submitted, the fixed destination, the lookup purpose, the free service/internet requirement, and standardization limitations. **Send to PubChem** explicitly approves only that reviewed request. Cancel or Escape revokes it without sending a lookup.

Consent is represented by an unpredictable, one-use proposal identifier, a literal true consent value and disclosure version 1. The server retains at most eight pending reviews, expires them after 120 monotonic seconds, admits one lookup at a time and allows at most one admitted start per second. Completed, failed, cancelled or expired requests cannot reuse consent. No setting enables future automatic uploads.

The submitted graph, document revision and exact graph/structure identities remain bound to the proposal. A changed drawing, cancelled review, newer operation or application close prevents a late result from being associated with the current structure. Cancellation can stop owned work and ignore a result; it cannot recall a request already transmitted to PubChem. If cancellation and completion race, the server orders them at one registry lock boundary.

## Provider contract and scientific limits

The adapter sends only the disclosed SMILES in a form-encoded HTTPS POST to this fixed endpoint:

    https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/property/IUPACName,SMILES/JSON

The current PubChem specification defines the IUPACName and SMILES properties and returns a CID with each property record. SMILES includes stereo/isotope information; ConnectivitySMILES is not used for identity validation. PubChem standardizes Compound records, so retrieval alone is not proof that a record matches the drawing. See the [official PUG REST specification](https://pubchem.ncbi.nlm.nih.gov/pcfe/docs/markdown/pug-rest.md), [property schema](https://pubchem.ncbi.nlm.nih.gov/pug_rest/pug_rest.xsd), [glossary](https://pubchem.ncbi.nlm.nih.gov/pcfe/docs/markdown/glossary.md) and [standardization documentation](https://pubchem.ncbi.nlm.nih.gov/pcfe/docs/markdown/standardization-service.md), inspected on 2026-09-21.

Only one returned record whose complete supported molecular identity matches can provide an accepted name. The comparison retains stereo, isotopes, formal charge, radical/hydrogen semantics and all fragments. It does not neutralize, desalt, choose a parent, tautomerize or relax stereo to obtain a match. Atom maps are drawing annotations excluded from nomenclature identity comparison, and that limitation is disclosed. Coordinates and the original document never change.

The result is labeled **retrieved database IUPAC name**, with no Preferred IUPAC Name claim. Comparing the record structure does not independently verify the database's name-to-structure assignment. Missing names or structures, invalid chemistry, standardization mismatches, and multiple records cannot become accepted names. The session result retains provider, submitted input identity/SMILES, returned structure/name, CID, retrieval time and comparison outcome. It is cleared when the drawing changes and is not silently persisted or added to a project.

## Transport and lifecycle

Network I/O runs in the existing owned scientific worker, outside the UI process and global chemistry lock. The worker uses a fixed destination, standard TLS verification, no cookies or credentials, no redirects, no automatic retry/polling and no provider fallback. The current adapter connects directly and does not use ambient proxy credentials; proxy-dependent deployments require separate evaluation and may report unavailable. No system proxy or security setting is changed.

Application limits are 16 KiB decoded SMILES, 64 KiB request/worker input, 128 KiB provider response/worker output, a ten-second HTTP timeout and a fifteen-second supervised worker deadline plus bounded cleanup. The browser has a separate 25-second local-request limit. These are application limits, not PubChem service guarantees. Current PubChem documentation describes free services, five requests per second and dynamic throttling; this adapter's admission policy is more conservative. [Official service and throttling documentation](https://pubchem.ncbi.nlm.nih.gov/pcfe/docs/markdown/dynamic-request-throttling.md).

Fixed failure messages exclude raw provider error bodies, URLs containing structures, exceptions, credentials and personal paths. Metadata-only diagnostics identify per-request consent; they do not include submitted structures or result history. The browser continues to contact only its authenticated loopback API. The existing Host, Origin, session-token and content-security-policy protections remain active.

## Evidence and remaining acceptance

Offline provider tests cover request/response bounds, TLS and redirects, error privacy, no network during preparation, exact identity dimensions, cancellation and nonmutation. Registry and real loopback tests cover consent, replay, expiry, capacity, cancellation/completion ordering, local editing during lookup and shutdown of an actually running owned worker.

A live public synthetic ethanol check on 2026-09-21 passed through the production authenticated prepare/consent/worker/comparison route: HTTP 200, CID 702, returned name ethanol, submitted and returned SMILES CCO, matched identity. Its server thread exited, the worker supervisor confirmed shutdown and no owned workers remained. The preserved external receipt is work/pubchem-20260921/live-ethanol.json. This is one public-fixture observation, not broad chemical accuracy validation or a native UI test.

Browser validation uses the real local application with intercepted local provider routes for deterministic consent, text safety, stale-result, cancellation and failure behavior. Its exact final counts and failures are recorded in the external increment report. No private structures or reference archives were uploaded during development.

The new adapter has not yet been packaged, installed or accepted in the native Windows UI. The older installed candidate does not contain it. General recognition, molecular population prediction, exact Marvin compatibility, native reference-export/input failures, strict offline network acceptance and the remaining full-Goal requirements remain open.
