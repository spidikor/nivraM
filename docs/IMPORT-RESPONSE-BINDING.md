# Source import response binding

Source import uses local request/response association at two boundaries. This detects mismatched, replayed or inconsistent results. It is not independent chemical verification or authentication against a producer that fabricates a coherent response.

## Isolated preparation child

The existing `nivram-preparation/1` import request includes a mandatory, closed `importBinding`:

- `schema`: `nivram-import-child/1`
- `invocationId`: a fresh random 32-character lowercase hexadecimal value per invocation
- `sourceSha256` and `sourceBytes`: SHA-256 and byte count of the exact UTF-8 source
- `style`: the normalized six-field drawing style

The parent privately snapshots the expected binding before dispatch. The child independently checks source bytes, digest and style before parsing. Whitespace and line endings are not normalized for association. Invalid Unicode, empty source and source exceeding 4,000,000 UTF-8 bytes are rejected.

An import success retains `result: {graph, scene}` and adds a closed `importReceipt` containing those five binding fields plus `outputIdentity` and `outputGraphJson`. The latter is the original canonical JSON graph hash preimage. The identity contains graph and canonical-SMILES hashes, canonical SMILES and a null document revision. Source text is not required to equal canonical SMILES: legitimate parser normalization remains supported.

The child captures the parsed graph, identity and witness, renders separate copies, and rejects mutations or inconsistent output. Before publication, the parent checks the fresh invocation binding, exact witness/graph consistency, hashes, report identity and requested style using data-only validation. Missing or mismatched receipts fail closed, including for legacy callers. Existing cancellation, shutdown and response-size checks still apply.

Batch and population-state requests and success envelopes do not acquire import bindings or receipts; their schemas and return shapes remain unchanged. Fixed graph-free error envelopes retain exactly `schema`, `status` and `code`, with the existing allowed codes. They do not require receipts because they release no drawing; resource-limit setup can fail before the child reads its request.

## Modern HTTP and browser admission

Modern `POST /api/import` requests contain only:

- `sourceBinding: "nivram-import/1"`
- `requestId`: 32 lowercase hexadecimal characters
- `documentRevision`: a nonnegative JavaScript-safe integer
- `source`: exact source text
- optional `style`

Any binding transport field requires modern validation. Incomplete metadata cannot fall back to legacy handling.

A successful response has exactly `schema`, `action`, `requestId`, `documentRevision`, `source`, `style`, `outputIdentity`, `outputGraphJson`, `graph` and `scene`. The schema is `nivram-import/1`, action is `import`, and `source` is exactly `{sha256, bytes}`. Style is normalized. The host builds this envelope only after validating the independent child receipt; the child invocation ID is not exposed to the browser.

Both main source import and image text correction use the shared asynchronous client helper. It captures immutable source, style and request metadata before awaiting work, rejects unpaired UTF-16 surrogates rather than silently replacing them, and verifies exact UTF-8 source bytes/hash. It also checks the closed response, metadata, style, graph witness hash, canonical-SMILES hash and property identity. Job currency and cancellation are rechecked across asynchronous boundaries, including digest completion. Missing WebCrypto or an unbound successful response fails closed, without a legacy retry.

Transport admission does not replace `prepareRender`: complete scene validation and SVG sanitization still precede document installation. Existing transaction rollback, history, settings preservation, image crop review and explicit image acknowledgement remain in their existing paths. Image text correction retains its stricter existing source-size limit.

## Compatibility and limits

Legacy HTTP requests containing source/style still receive raw `{graph, scene}` after mandatory child receipt validation. Native callers of `prepare_import` retain that default raw return shape; the modern host explicitly requests the admitted receipt alongside the result. Typed HTTP errors remain errors, not a success fallback.

Focused data-only tests cover association and asynchronous admission. This contract does not establish actual browser/WebCrypto availability, Windows/native installation acceptance, renderer/font correctness, scientific correctness, or operating-system quota enforcement. Those require separate runtime and acceptance evidence. Existing resource and response limits are preserved; no external inference service is introduced.
