# Bounded native fixture progress

The two synthetic batch chooser fixtures opt in to checkpoints over the existing
child stderr pipe. Ordinary scientific workers and legacy single-file fixtures
remain disabled by default. The terminal stdout request/result contract is unchanged.

Each record contains a fixed protocol prefix, the invocation nonce, a consecutive
sequence number, and one allowlisted event. A line is at most 96 bytes including
its newline. There are at most 31 normal records and one final limit marker;
the existing 4096-byte stderr limit also remains enforced. The parser rejects
wrong nonces, malformed framing, repeated or missing sequences, unknown events,
and excess bytes. Rejection does not stop normal stderr draining.

The exported diagnostic contains only availability, a fixed status, the last
observed event, sequence, and whether the final record was partial. It contains
no nonce, path, caption, payload, command line, handle, PID, or exception text.
The nonce binds a record to an invocation; it does not authenticate a child's
claim or prevent an already-compromised child from inventing events.

Selection emits a checkpoint only on the first entry into each distinct operation
group, so repeated polling does not exhaust the record budget. Progress starts
after bounded stdin reading and JSON parsing; startup or input failures can
therefore have no checkpoint. Cancellation and backend cleanup have separate
events. A checkpoint proves only that a record was received, not the child's
current location, successful UI action, or the cause of a timeout.

Diagnostic writes can block even though the record count and sizes are bounded.
They occur before the existing final ownership/deadline checks for field writes
and submission. No telemetry I/O is inserted between those final checks and the
corresponding write or Invoke. Existing parent deadlines and owned cleanup apply.

The parent freezes its packet at the supervision boundary, before cleanup starts.
Later cancellation or cleanup emissions cannot replace that packet. Pending
observer snapshots use a separate tiny parser lock, never the supervisor lock;
they return an unavailable/busy status instead of waiting on the parser.

Diagnostics do not authorize dialog selection, substitute original native chooser
returns, relax any of the six native acceptance gates, or prove graceful cleanup.
Unit tests use controlled streams and backends; native Windows acceptance remains
a separate source- and executable-bound gate.

## Initial field-identification observations

The field-identification continuation adds paired entry/return events for child
control enumeration, the existing UI Automation bootstrap group, initial IDOK
element/name retrieval, and initial edit element/name retrieval. Separate IDOK
and edit contexts distinguish their first lookups even when repeated scans occur.
Together with the existing events, the normal vocabulary contains 31 names;
the 31-record budget and reserved sequence-32 limit marker are unchanged.

Bootstrap groups COM initialization, CLR import, runtime directory lookup,
assembly references, and type binding. An entry without its return does not
identify which operation within that group stalled. FromHandle observations
include conversion of the native handle into the managed argument; Name
observations include reading the current property and converting its value.
No returned name, path, or handle enters the diagnostic packet.

Only initial label classification opts into these backend observations. Later
mode/ownership validation and Invoke preparation/readiness use the existing
uninstrumented label path. Existing adapters keep their original label interface.
The events never authorize a native chooser return or alter acceptance criteria.

## Optional parent pipe observations (S1)

`NativeFileChoice(parent_io=True)` holds one `ParentIoCapture` and passes it to
the existing `WorkerSupervisor.run`. This is independent of child progress;
default supervisor and fixture callers remain disabled. The synthetic native
batch fixtures opt in. No child event, protocol limit, chooser argument, deadline,
process ownership rule, or native acceptance gate changes with this observation.

The separate `parent_io` packet contains exact booleans for stdout/stderr reader
entry and actual EOF, and stdin writer entry, complete write and complete close.
It also reports whether an input writer was expected, the existing stdout byte
limit, and the bounded number of stdout bytes retained by the existing capture.
It never retains or exports those bytes, paths, captions, identities, nonces,
commands, arbitrary exception text, handles or PIDs. Counts describe retained
bytes, not all emitted bytes or a complete valid response.

EOF means an actual read returned the empty stream value. Reader completion,
exceptions, process exit and local stream close cannot substitute for EOF.
`stdin_write_completed` means all existing writes returned successfully; it does
not prove the child consumed the request. `stdin_close_completed` means the
existing close returned successfully and may occur after a failed write. No new
write or flush is added. Existing `child_running`, return code and completion
flags retain their original meanings and remain separate from this packet.

Snapshot and freeze each attempt the capture lock once without waiting. Busy or
unconfigured observations contain unknown facts, not zero or false guesses. A
contended freeze remains busy; later events cannot fill in its primary boundary.
The first packet is published once through a builtin dictionary operation on the
pinned CPython runtime. Capture locks cover only fixed in-memory scalar updates,
never read, write, flush, close, process wait, join or cleanup.

The supervisor freezes before its owned cleanup; the fixture also freezes before
shutdown and before external close requests cancellation. Pending batch records
use `parentIoAtSnapshot`, including when an original chooser has returned but
fixture completion is still pending. Parent I/O, child progress, process state and
thread completion are separately collected observations, not an atomic view of
all channels at the exact deadline. They cannot authorize selection, accept
incomplete output, establish a causal diagnosis or promote a failed native gate.

Controlled pipe and finite direct-child regressions test these boundaries. An
inherited-style held-pipe fixture is not real descendant-process coverage. Actual
Windows native evidence is still required to locate the recorded failure and to
qualify the candidate with all six gates, then repeat with a fresh profile and the
identical executable. The full eight-stream programme remains governed by the
preserved `IMPLEMENTATION-PLAN-20261004.md`; this S1 change does not alter tracker
statuses or the deferred 3D/scientific scope.
