# Exact supplied-model numbers in JSON and CSV

This source change repairs a valid-input export defect, not a molecular prediction
or population-vector authentication feature. Python accepts finite integer energies
larger than JavaScript's exact integer range. For example, energies 9007199254740992
and 9007199254740993 produce different weights, but ordinary JavaScript JSON parsing
rounds both to the first value. Previously, exports could contain that rounded model
alongside the correctly calculated fractions; native arithmetic validation rejected it.

## Contract

The model-file route now reads original and returned model JSON with a bounded,
duplicate-key-rejecting token reader. It compares integers as exact decimal values,
keeps integers distinct from floating-point inputs, and compares floats as binary64
values with signed zero retained. Equivalent float spellings such as 1.0 and 1e0
are accepted. Integer -0 is equivalent to 0. The same checks cover the conditions
repeated in every computed row, so export preservation cannot hide a changed copy.

Checked numeric metadata is bound privately to the immutable accepted envelope
and its original input digest. JSON and typed CSV serialize these model-derived
numbers without losing their original Python values or integer/float types.
Spelling is compacted, so an unusually long exponent does not multiply across
141 rows or exceed the existing CSV field limit. Model text and its byte digest
remain unchanged in the session. Exports retain the existing schema; they do not
embed the original file or promise its original whitespace or numeric spelling.
Computed fractions, percentages, logarithms and coordinates are never substituted.
Native arithmetic validation is unchanged. Generic envelope adapters without an
original file retain their existing behavior and cannot recover lost input precision.

`validatePopulationEnvelope` has an optional third, file-route-only metadata argument.
Existing two-argument callers and the HTTP/export schemas remain compatible.
The internal token-reader module is not a general-purpose JSON or scientific API.

## Bounds and responsiveness

The original 2 MiB input and 16 MiB response limits are unchanged. Parsing is owned
by the calculation task, yields between bounded scans, and checks cancellation
and an absolute monotonic deadline that is not reset by yielding. Decoded duplicate
keys, malformed JSON, excess depth/nodes and unsupported vector lengths are rejected.
The native fast path handles only small flat numeric vectors and checks the existing
4096-state limit before allocation. Invalid original input makes no evaluator request.
Queued cancellation and freshness changes are checked before publication.

Existing envelope validation, exact-binding validation, UTF-8 decoding and export
serialization still include synchronous work. This is not a hard real-time or memory
bound. Local Node probes of genuine 16.44–16.48 MB responses observed cooperative
parse totals of 106–396 ms, with a requested 5 ms timer serviced at 4.3–9.5 ms and
sampled maximum event-loop gaps of 5.4–19.5 ms. A full 2 MiB original-input probe
parsed in 92 ms while servicing its 5 ms timer at 5.3 ms. Full numeric-envelope load
remained about 1.43 seconds versus 1.28 seconds in the compared baseline; existing
whole-envelope validation remains synchronous. These measurements are not browser,
Windows, worst-case timing or per-operation memory guarantees.

## Offline verification

- 80 JavaScript tests pass, including the existing analysis, state review/adoption,
  reference context, export, physiology comparison, omission and gap regressions.
- New tests use real Python evaluator response bytes for 13 models, then validate
  JSON and CSV with the production native validator and a controlled save window.
  Cases include adjacent large integers, negative values, large temperature and
  ionic strength, mixed numeric types, signed zero, subnormal/underflow values,
  long numeric spellings, escaped text and incomplete/narrow domains.
- Corrupted exact model/condition tokens, decoded duplicate keys and mismatched
  export source digests are rejected. Generic adapter behavior remains unchanged.
- Cancel, timeout, document revision and disposal during near-limit response and
  2 MiB original parsing publish nothing. Response cancellation is sent once;
  cancellation before an evaluator request makes no network request.
- 52 executable pure Python math, native-payload and physiology tests pass.
  The separate jobs/HTTP test module could not import because RDKit is unavailable.
  Chemistry-backed state preparation and full native saver/desktop tests were not
  revalidated. No dependency was installed to mask that boundary.

No browser, Windows, installed-app, raster or scientific-validity acceptance is
claimed. The physiology dataset is unchanged. At this historical numeric-fidelity increment, forged arithmetic and
worker-reported signature authenticity remained separate boundaries. The subsequent
service verifier below addresses the former for its specific producer route.


## Condition display follow-up

The chart's full DOM provenance and existing PNG temperature/domain header use the
same private checked numeric binding as JSON/CSV. This prevents a valid large
integer condition from being displayed as its rounded JavaScript Number. All five
numeric condition fields are covered in provenance; PNG retains its existing
subset (temperature and pH domain), without adding an ionic-strength line or a
new visible input-hash field. Null wording, source identity, signatures and generic
adapter formatting remain unchanged. Float spelling may be compacted, including
preserved signed floating zero; integer negative zero is zero.

The read-only condition accessor requires an accepted envelope and an existing
condition field. It accepts no alternate labels or raw text. PNG rendering reads
the captured envelope, so context, pH and visibility changes cannot rewrite an
in-flight label set. No population arithmetic, source hash, document/history,
reference data or native export validation changes.

Three controlled DOM/canvas tests use genuine Python evaluator responses for seven
input variants. They assert actual canvas text calls, captured-export ownership,
cancellation and unchanged JSON/CSV validation. The same test fails on the preceding
source's rounded temperature label. A tiny encoded PNG fixture exercises the existing
encoding/stamping lifecycle only; it does not establish raster, browser or Windows
appearance. Existing regression and independent-review results are in the matching
source-bound evidence rather than inferred from this test definition.


## Subsequent source verification boundary

The historical arithmetic limitation above is superseded for the complete-model
service route by [independent result verification](population-result-verification.md).
Original model binding now precedes independent arithmetic and producer-contract
checks before publication. Generic adapters are unchanged; this does not
cryptographically authenticate the worker or verify molecular science. Historical
checks and timing above retain their original source scope.
