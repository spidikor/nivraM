# Optical recognition resource limits

Optional PNG recognition now has one 90-second deadline beginning before its
source crop is prepared. The existing crop worker keeps its shorter 45-second
limit; elapsed crop preparation is deducted from recognition's remaining time.
The inference worker and trusted chemistry validator run sequentially, each
using the remaining deadline. No native result parsing or layout runs in the
desktop host. A late result cannot publish a review candidate.

The service rechecks the original cancellation token and total deadline after
retiring its active registration, before returning success. Cancellation or
deadline exhaustion while that finalization waits suppresses the result using
the existing cancellation or time-limit error. Earlier worker, validation, and
cleanup failures keep their existing precedence.

Each inference/validation worker starts suspended. Before its primary thread
resumes, the desktop creates a private Windows job, sets and reads back a
4 GiB aggregate committed-memory ceiling plus kill-on-close, assigns the worker,
and verifies membership. The worker inherits exactly stdin, stdout and stderr.
The job handle stays with the owner. Descendants inherit job ownership; no
breakaway permission or unbounded fallback is configured. A stricter ancestor
job can impose lower effective limits.

Cancellation, timeout, stream overflow and application shutdown terminate only
that private job. A normally exiting root also causes leftover descendants to
be stopped. Completion requires observing zero active job members and signaled
process handles retained from verified membership. Job accounting alone can
reach zero before a terminated process finishes kernel teardown. The cleanup
budget is four seconds after a stop is observed, leaving headroom for the
five-second cancellation target. OS process creation, filesystem operations and
thread scheduling are not hard real-time primitives; an unconfirmed stop is
reported as failure, and that optical owner is closed to further requests.
Successful-stage cleanup is charged to the shared deadline. One final abort
cleanup can extend wall time beyond the 90-second work budget.

If the lifetime process count cannot be reconciled with retained member handles,
exit coverage is unconfirmed and recognition fails closed. Membership tracking
is bounded to 4,096 process handles; it never scans unrelated machine processes.

The quota is committed virtual memory shared across job members, not resident
physical RAM and not a per-process allowance. Allocation can fail at the limit
without Windows immediately terminating the program. These controls do not
provide an OS network sandbox or certify safety against hostile native code.
Existing offline library settings and Python socket audit denials remain in
place; no security-product settings or domain exceptions are required by this
change. Existing short chemistry-worker quotas are unchanged.

## Evidence and limits

The old 300-second default was reproduced against the planned 90-second value
before implementation. Focused tests exercise actual Windows jobs with synthetic
process trees, cancellation, timeouts, output limits, exact handle inheritance,
failed setup, aggregate memory pressure at a lowered test limit, native chemistry
validation, deadline accounting and stale-result rejection. The production
4 GiB setting is checked through Windows readback without allocating 4 GiB for
a test. In the reduced 192 MiB probe, further allocation was rejected and the
independently queried current job commitment stayed within its ceiling, while
Windows reported a higher historical peak. The peak discrepancy is unexplained;
the check does not claim that every peak statistic stays below the configured
limit or establish a physical-RAM ceiling. The source-bound check receipt records exact tests, failures/skips,
process cleanup and source identity. Packaging/native-startup evidence is
reported separately for each delivered installer.

The checkpoint was unavailable when the initial resource-limit checks were
recorded. It has since been obtained and verified. A separate source-runtime
check on September 29, 2026 recognized one aspirin fixture with the expected
canonical isomeric SMILES in 48.828 seconds, preserving the recognized MOL
coordinates and requiring human review. That single run is evidence for that
fixture and prepared runtime; it is not a chemical-accuracy benchmark,
cold/warm latency study, 30-second warm-target result, inference-traffic audit,
or installed-app acceptance. The resource-limit tests themselves still use
synthetic workloads. Packaging and clean-machine acceptance remain separate.

Windows semantics are documented in Microsoft's
[job memory limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information),
[nested jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs),
[handle-list inheritance](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute),
and [job termination](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject).
