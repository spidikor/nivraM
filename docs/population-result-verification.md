# Supplied-model result verification before publication

The service-return boundary now verifies successful worker responses against the
parsed original complete model before publishing their original UTF-8 bytes.
`nivram.population_verification` is stdlib-only and does not import the production
population evaluator, dataclasses, native saver, or chemistry stack. The worker
remains the only child. Generic frontend adapters retain their broader contract.

## Checked contract

- Exact original/returned model equality preserves Python integer versus float,
  boolean, signed floating zero and Unicode distinctions. Key order and equivalent
  parsed numeric spellings are not identities. The original-model SHA-256 check
  remains in place, but is an identifier, not cryptographic worker authentication.
- All 141 samples must be ordered at index / 10. Domain gaps must exactly match the
  declared bounds and producer reason. In-domain samples must be computed.
- State order, input/model identities, repeated conditions/provenance, warnings,
  natural-log declaration and complete schemas must match this producer route.
  This confirms retention of declarations, not their scientific truth.
- The route accepts no external omitted mass: a declared complete model has zero
  omitted mass, and an incomplete model has unknown omitted mass and conditional
  represented-state normalization. Invented supplied masses and in-domain gaps
  reject without changing the generic adapter contract.
- Independently implemented stable log-weight/normalization arithmetic uses the
  original energies, proton counts, reference state, units, temperature and
  degeneracy convention. Integer anchor differences precede float/unit conversion.
  Fractions, percentages and finite natural-log fractions must exactly match the
  same-runtime producer operations, including underflow and tiny nonzero fractions.
  No absolute tolerance silently accepts zero for tiny represented populations.

This is deliberately tied to the current producer's binary64 operations. A future
producer numeric algorithm/runtime change needs corresponding verification review;
this is not a portable validator for arbitrary external arithmetic implementations.
Energy-difference terms are initialized lazily at the first computed sample. An
all-gap grid therefore retains valid worker bytes even when individually finite
energies have a difference that would overflow if evaluated. Domain/schema checks
still apply to every gap. The evaluator is used only as a test oracle, alongside independent analytical
ratios and corruption cases. No molecular predictor or chemical-validity claim is
introduced. A compromised common runtime or malicious source replacement is not
addressed by this consistency guard.

## Bounds, cancellation and cost

Existing input/output caps, error codes, worker-error precedence, one-child limit,
registry finalization and final cancellation/shutdown publication checks remain.
Malformed successful output becomes `worker_failed`, not an unexpected traceback.
No successful response is reserialized or rewritten. Repeated metadata is compared
as parsed values, without repeating dataclass/provenance serialization.

Parent verification happens after the child's existing 60-second timeout. It checks
cancellation between rows/states, metadata objects and array/vector chunks of at
most 64 entries. JSON parsing, canonical model serialization, hashing, individual
scalar operations, bounded container operations and final UTF-8 encoding remain
synchronous. This is not a total-request deadline, hard real-time guarantee or
per-operation memory guarantee.

Paired local Python 3.12.14 measurements used identical fixtures, settings and wire
hashes, one warm-up then three retained runs per mode. Ordinary 2-state responses
were 246,592 bytes: baseline/candidate median full service times were 0.0839/0.1062
seconds; parent-return-boundary times were 0.00160/0.00390 seconds. Near-cap 1600-state
responses were 16,055,604 bytes (95.7% of the 16 MiB cap): full service medians were
1.2343/1.6728 seconds, and return-boundary medians were 0.1706/0.4848 seconds.
The near-cap increase is material (~0.439 seconds, 35.5% full service); the new guard
has not been weakened to conceal it. These three-run local samples are not
Windows/browser acceptance or worst-case timing. The earlier 1200-state timing
and baseline counterexample evidence are retained separately.

## Offline checks and remaining work

Focused tests cover wrong normalized vectors, swapped states/model echoes, changed
metadata and omissions, malformed numeric types, all units/degeneracy modes,
complete/incomplete and conditional models, non-first anchors, adjacent large
integers, signed zero, tiny/underflow fractions and finite logs. Existing exact-byte,
worker-error, cancellation and isolated parent-import tests remain applicable.
See matching fresh check receipts for actual pass counts and source identities.

Browser/Windows/native execution remains deferred to the requested November 12
Mountain session, without assuming a quota reset. Installed-app qualification,
other scientific calculations, independent chemical/model validity and the full
project Goal remain open. No installer is updated by these source edits.

## Independent-review compatibility repair

Review reproduced a valid all-gap domain (-2..-1, reference -1) with integer
energies +10**308 and -10**308. The initial verifier eagerly subtracted energies
and incorrectly rejected the successful all-gap response. Lazy term initialization
repairs this without relaxing computed-row arithmetic or model validation. The new
regression preserves exact original bytes, rejects a forged computed gap, and
confirms that the worker still reports invalid_model when the overflowing
arithmetic is actually requested in-domain.

After repair, the focused core run has 112 tests (111 pass, one Windows-only skip),
and the separate publication module has nine passes. The unchanged native payload checks retain their earlier scoped results. The
73 Node-based tests include controlled DOM and Python-helper integration, with
mocked native cancellation seams; no real browser/native GUI was used. Fresh
sequential matched-input baseline/candidate runs observed ordinary return-boundary
medians 0.001874/0.003815 seconds and full-service medians 0.124810/0.086030 seconds.
Near-cap return-boundary medians were 0.180946/0.492239 seconds and full-service
medians 1.174736/1.524913 seconds. Each retains three samples after one warm-up.
The noisy ordinary full-service reversal is not evidence of a speedup; near-cap
overhead remains material (+0.350178 seconds, about 29.8%). These are local
measurements, not statistical confidence, worst-case bounds or performance acceptance. The initial frozen source manifest, regression failure and intermediate
repair error logs are preserved separately from repaired-source evidence.
