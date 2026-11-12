# Supplied-model identifier consistency

The population job service checks the successful worker envelope's `modelSignature`
against the original, validated request's immutable `modelText`. The expected
identifier is SHA-256 of strict parsed JSON serialized with sorted object keys,
compact separators, `allow_nan=False`, and Python's default ASCII escaping. This
matches `PopulationModel.signature` for the complete-model route: every init field
must be supplied, constructors preserve scalar values, and list/tuple conversion
has the same JSON representation. Changes to that route or its constructors must
revisit this equivalence and its tests.

The service does not coerce integers, floats, booleans, signed floating zero or
Unicode. Object-key order and JSON escape/float spelling aliases have their existing
parsed meaning. Integer and floating values, signed floating zero, adjacent large
integers and different Unicode code points can have different identifiers. The
existing exact input-text digest remains separate. Missing fields with constructor
defaults are still invalid on the complete-model route.

A mismatching envelope identifier fails with `worker_failed`. Existing worker
errors are handled first; invalid models retain their prior error codes. A valid
response returns the original UTF-8 worker bytes unchanged. The one-child limit,
worker timeout and input/output caps are unchanged. The original cancellation token
is checked before/after added parsing, serialization and encode/hash stages, and
the existing after-finalizer publication guard remains. These bounded parent stages
add latency and temporary allocations outside the worker timeout; parsing,
serialization and encode/hash cannot be interrupted internally. There is no hard
real-time cancellation guarantee.

**Historical identifier-only limitation:** Before the subsequent verifier below,
this was not calculation verification or worker authentication. A worker returning the correct original-input identifier with
wrong vectors or altered model content could pass the identifier-only service check. The identifier
is not a keyed signature and does not hash the response vectors. Existing client
model/numeric, provenance, freshness and vector-shape checks remain unchanged.
No independent replay, scientific validation, molecular applicability proof or
protection against a compromised parent is provided.

`tests/test_population_signature_binding.py` covers forged identifiers (including
consistent row identifiers), supported representations, unchanged bytes, preserved
worker errors, cancellation at new stages and rejection of the formerly accepted wrong vector.
Run the existing `test_population_publication.py` in its own Python process to
preserve its parent-import-boundary assertion. Local stdlib/Node tests do not prove
Windows packaging, frozen-worker or browser execution.


## Subsequent source verification boundary

The historical arithmetic limitation above is superseded for the complete-model
service route by [independent result verification](population-result-verification.md).
Original model binding now precedes independent arithmetic and producer-contract
checks before publication. Generic adapters are unchanged; this does not
cryptographically authenticate the worker or verify molecular science. Historical
checks and timing above retain their original source scope.
