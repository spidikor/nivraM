# Population result publication after registry finalization

## Candidate repair

`PopulationJobs.evaluate` previously prepared a Python return inside its `try`
block. Its `finally` block could then wait for the request-registry lock. A
cancellation or shutdown completed during that wait could change the token or
closed state without stopping the already prepared return.

The candidate retains the original UTF-8 worker wire in a local variable, lets
existing registry finalization finish, and checks the original attempt's token,
the service's closed state, and the existing supervisor `check_cancelled(token)`
API before returning those exact bytes. The new checks run only on the successful
path. Existing worker, timeout, output-limit, parsing and cleanup failures retain
their existing precedence and error mapping. Alternate injected supervisors do
not acquire a new required `closed` property.

No worker mathematics, response schema, document, fixture, default, byte cap,
worker-count limit, or worker-only timeout contract changes. No verifier process,
scientific replay, new absolute deadline, or intentional wait is added.

## Registry semantics and limits

History remains unchanged: `finished` records that an attempt left the registry,
including attempts that fail. It is not proof of successful result publication.
A token cancellation or shutdown after registry retirement can therefore reject
publication while history still says `finished`. API cancellation after retirement
returns that existing status, rather than acknowledging a new cancellation.

The publication guard closes the reproduced registry-finalization scheduling gap.
It is not an atomic transaction with HTTP delivery or browser adoption, and cannot
prevent a later cancellation after the final check. HTTP transport, browser state,
Windows native execution, full response integrity and release readiness remain
separate, unverified boundaries.

## Focused regression

Run from the source root:

    python -B -m unittest discover -s tests -p test_population_publication.py -v

The module imports only standard-library code and the portable jobs/supervisor
modules in the parent. It launches real normal subprocesses and uses events to
pause immediately before registry-finalizer acquisition or after its lock release.
This is deterministic synthetic scheduling, not an observed spontaneous production
race or a claim of corrupt scientific output.

The checks cover exact-byte controls, API cancellation, original-token cancellation,
service shutdown, direct supervisor shutdown, primary-error precedence, later-request
isolation, the existing injected-supervisor interface, unchanged caps/timeouts,
request preservation, child exit, closed pipes and zero active children.

The final unchanged module has 9 tests: it fails the preserved baseline with exactly
7 cancellation/shutdown publication assertion failures and passes this candidate.
The 14 existing portable worker tests and 22 existing analytical population tests
also pass. See the external source-bound evidence packet for exact commands,
observations, timings, hashes and limitations. No browser, HTTP server, RDKit,
Windows runtime, dependency installation or external upload was used for this work.
