# Deterministic JSON request nesting

The loopback HTTP API now rejects request bodies with more than **64 nested JSON
objects/arrays**, counting the root container as one. Rejection happens before the
JSON decoder and before any route handler, returning the existing HTTP 400
`Invalid JSON.` response. All routes use the same depth rule, including the two
population routes with their existing larger byte limit.

## Why this changed

The earlier handler caught `RecursionError` from the JSON decoder but did not have
an application-owned depth limit. The retained baseline tests supplied 4,000 nested
objects and expected 400. On the actual Python 3.12.14 environment, the JSON C decoder
accepted that input even with `sys.getrecursionlimit()==1000`, so the unknown route
returned 404. Both socket and direct-handler failures were reproduced unchanged.
This is a runtime-dependent admission gap, not evidence of a chemistry regression,
network interception, or a defect in the Python JSON implementation.

The repair does not raise/lower Python's global recursion limit or weaken the tests
by accepting 404. It adds a constant-state scan of the already byte-bounded body.
The scan counts structural braces/brackets only outside quoted strings and handles
escaped quotes/backslashes. Encoded JSON, SMILES, MOL text and Unicode inside strings
remain data. Depth is checked on raw wire content so an overwritten duplicate-key
value cannot hide an excessive subtree. The existing decoder still owns UTF-8,
JSON grammar, nonfinite constant rejection and the top-level-object requirement.
This guard does not become a second full parser or change duplicate-key semantics.

The 64-container limit is a conservative transport ceiling, consistent in scale with
the existing bounded preparation data tree. It is not a claim that arbitrarily
nested 64-level payloads satisfy an individual endpoint schema. Domain validators
keep their stricter contracts. JSON embedded inside a string is not another transport
container; the consumer's existing validation still applies if it parses that text.

## Preserved boundaries and limits

Host/Origin/token validation, content type, request framing, byte limits, receive
and rejection-drain deadlines, provider consent, cancellation and route behavior are
unchanged. No session setting, system/network security setting or runtime dependency
was changed. The guard uses constant extra state and linear work in scanned bytes,
stopping at the first excess opening container. It does not establish a complete
CPU/memory budget, total node limit or maximum number of simultaneous HTTP clients.

Tests retain the original two regressions and add exact 64/65 boundaries for objects,
arrays and mixed containers; real loopback requests; quoted delimiters, escaped
quotes/backslashes, Unicode and encoded source; overwritten duplicate-key branches;
malformed data; and proof that excessive bodies reach neither decoder nor endpoint.
Independent review uses a separate depth oracle and handler probes. Exact outcomes
and source bindings belong to the accompanying checkpoint receipt.

The prior deep-JSON failures are resolved for this candidate, not retroactively
changed in older checkpoints. Trusted-system Arial rendering, actual browser UI,
Windows installed-app acceptance and broader scientific/release qualification remain
separate holds. No installer or additional feature is included.
