# Local naming job lifecycle

Each `LocalServer` owns its own `WorkerSupervisor` and `NamingJobs` registry.
The HTTP route passes that supervisor and a per-job `CancellationToken` through
NISPO generation and OPSIN verification. The registry permits two active naming
jobs; no chemistry lock is held while waiting for runtime readiness or a child.
Legacy `/api/name` calls that omit all job-identification fields still work and
get an internal owned token. They cannot address that token through the public
cancellation endpoint. The real frontend always supplies public identifiers.

Naming POST bodies carry the graph, document revision and:

- `clientId`: a random 128-bit lowercase hex ID for this frontend page.
- `jobId`: a fresh random 128-bit lowercase hex ID for this calculation.
- `expiresAt`: integer Unix milliseconds, 30 seconds after frontend submission.

The server validates both IDs as exactly 32 hex characters and rejects deadlines
more than 60 seconds in the future or already expired at registration. Partial,
null, oversized and malformed IDs cannot fall through to legacy compatibility.
Successful public responses echo the IDs. The frontend requires both those IDs
and its document revision to match, then rechecks the job immediately before
updating the displayed result.

`POST /api/cancel-job` accepts only the three identifier/deadline fields, with the
same loopback Host, Origin and session-token requirements as scientific POSTs.
The cancellation request never contains the molecular graph. It uses a fresh
AbortController, a three-second transport timeout and a small keepalive request,
so it does not inherit the already-aborted calculation signal. Document edits,
newer naming requests, explicit cancellation, client timeout, close review and
abandoned transport/validation failures all request cancellation. Failure of that
transport is not a receipt proving that a native process already exited; native
worker deadlines and server shutdown remain independent limits.

The bounded registry keeps at most 256 active/recent records and retains completed
or pre-registration cancellation records for 120 seconds. It refuses new unknown
records when full rather than evicting a still-valid cancellation record. An
authenticated cancellation arriving before registration creates a tombstone;
the later naming request is rejected before starting a worker. A queued request
whose deadline expired cannot become runnable merely because its tombstone was
purged. This registration deadline uses the local host clock; it is not a claim
of distributed clock synchronization or an execution-time deadline.

Cancellation receipts distinguish `cancellation_requested` for an active token,
`cancelled_before_registration`, already `cancelled`/`finished`, `expired`, and
`closed`. A full registry returns HTTP 429 with `unconfirmed` and records no
fabricated cancellation. Duplicate active/recent IDs return HTTP 409; callers
must create a new job ID. Recent records contain only IDs, states and expiration
times, never structures or command lines.

Normal shutdown stops accepting HTTP requests, then calls `server_close()`.
That method closes the job registry, cancels its tokens and calls the server's
own supervisor with a two-second shutdown budget. The return observation is kept
in `server.worker_shutdown_receipt`: `ownedProcessesExited` is false when exit
could not be confirmed. The receipt concerns direct owned child-process exit;
request threads may still be unwinding. No claim covers arbitrary descendant
trees, OS crashes, forced termination of the parent, or interrupting process
creation itself. `/api/status.scientificJobs` exposes safe aggregate counts and
the latest shutdown receipt without exposing job IDs or molecular data.

`tests/test_server_cancellation.py` checks real local HTTP cancellation of a real
owned synthetic child, authentication, race order, independent server ownership,
registry limits and both confirmed/unconfirmed shutdown receipts. Synthetic child
work isolates the lifecycle contract; it does not validate a naming engine's
chemical accuracy. Existing naming-worker tests cover real NISPO/OPSIN paths.
`tests/analysis-request.test.mjs` checks fresh cancellation signals and tokens,
request identity binding and stale-result rejection at the frontend boundary.
