# Graph scene response binding

The modern graph consumers of `/api/render`, `/api/clean`, `/api/reflect`, and
`/api/rotate` request `nivram-render/1` responses and reject missing or mismatched
binding. The local engine independently identifies the original graph and the
returned graph. The response carries the exact original operation payload,
request ID, operation and document revision. Scene properties must identify the
returned graph. Drawing style must match the requested style with defaults.

Original and returned graphs are deliberately separate: rendering may normalize
aromatic/Kekule bonds and omitted graph defaults; cleanup changes coordinates;
reflection and rotation retain their existing operation-specific safeguards.
Render additionally requires the same canonical structure before and after
normalization. Existing persisted graph hashes are unchanged. Scene property
identities retain their existing null revision; the transport revision is separate.

The shared client helper captures immutable request data before asynchronous
transport, verifies current job/revision after asynchronous reads, validates the
closed envelope and property report, and returns immutable admitted data. Full
scene/position/SVG admission remains a separate required `prepareRender` step in
the application, image correction review, and profile preview. Existing commit
revision, rollback, history, cancellation, timeout and cleanup single-use checks
remain in place. Cleanup retains its private admitted response until Apply.
Profile PNG preview uses the fully admitted output graph for recoverable chemistry
metadata, consistent with the graph used to produce its scene, rather than the
pre-normalization input graph.

Raw legacy scene admission and legacy server callers remain compatible. Modern
clients never retry or silently accept an unbound response. Source-text import
remains in its resource-limited child with its existing protocol; this change
does not move parsing or native chemistry into the host import route. Other
prepared-result protocols are not covered by this envelope.

## Limits and validation

This is local response association and consistency checking. It is not a digital
signature, independent descriptor validation, proof that SVG geometry depicts a
molecule, or evidence of scientific or visual equivalence to another product.
A coherently fabricated envelope within the trusted local engine is outside this
binding guarantee. The JavaScript client does not recreate Python graph serialization. Instead, the
server supplies a bounded `outputGraphJson` string serialized from its captured
output graph. The client parses that exact string and compares it to the returned
graph, then hashes its original UTF-8 bytes with WebCrypto SHA-256 and compares
that digest to the output graph hash. It also hashes the output SMILES string and
compares the structure hash. Currency and cancellation are checked again after
each asynchronous digest. Missing WebCrypto fails closed.

These witness checks reject one-field output graph mutations and false hashes
that previously passed structural admission. They still do not detect a
coherently fabricated graph, witness, identities and report. Scientific producer
correctness remains a distinct concern. Pre-witness acceptance characterizations
are preserved in the change evidence; post-witness regressions require rejection.

Validation for this bounded change uses selected data/controller-only Node tests
and graph-only Python tests with controlled scene producers. Actual renderer,
font, browser, Windows, external provider and private-reference checks remain
unexecuted. Browser fixture migrations are protocol maintenance only and do not
constitute browser test results. Existing test assertions are retained and the
prior fixture files are archived separately in the change evidence.

The original `sourceIdentity.graphHash` remains server-derived and opaque to the
JavaScript client; the client checks the exact `sourceRequest` echo rather than
recomputing that source hash. This differs from the verified output graph witness.

WebCrypto availability and secure-context behavior in the target Windows WebView
have not yet been verified; those platform checks remain deferred.
