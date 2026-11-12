# Opt-in parent-I/O observation in the native fixture

This is a new, source-level diagnostic feature. It is not the lost S2.2 implementation, runtime qualification, a completion receipt, or a fix to an evidenced existing provenance defect.

## Owner API and actual consumer

`NativeFileChoice(..., parent_io=True, observation_binding={"attempt_id": ..., "source_sha256": ..., "origin": ...})` accepts an exact three-field binding. The attempt must be canonical UUID4, the source a lower-case SHA-256, and origin `synthetic` or `host_reported`. The latter is a declaration, not authenticated host evidence. The fixture copies the identity into an immutable existing observation record before invocation. The PNG hash and native request nonce are never substituted. The acceptance harness's existing chooser forwards the optional binding; a coordinator can supply a separate binding per call through the existing `choose` callback. No default CLI/harness activation or source attestation is added.

`ParentIoCapture(primary_observation=True)` is private fixture-owned capture state for this option. The actual `WorkerSupervisor.run` finally block records a primary packet and outcome together before its existing cleanup. Outcomes are returned, timeout, cancelled, interrupted (KeyboardInterrupt/SystemExit), or error. These are supervision outcomes; a nonzero child return or later invalid JSON may still coexist with `returned`. No process return code or cleanup/ownership evidence is promoted into the envelope.

`NativeFileChoice.observation_snapshot()` uses the unchanged v007 bind/project and v008 encode APIs. The final result contains the ASCII envelope under `parent_io_observation`, or null when a primary packet is unavailable. This is an in-memory string, not a durable save or write acknowledgement. No old receipt serializer is invoked.

The actual `NativeDialogObserver` obtains the expected binding only from the exact owned `NativeFileChoice`, not result data. It passes that binding to `dialog_diagnostic`, which invokes the unchanged strict v008 decoder and re-encoder. Mismatched or malformed data becomes null. With no expected binding, the diagnostic gate retains its old fields and does not expose an envelope. Default constructor behavior and result shapes are unchanged.

## Boundary and race rules

- Packet and outcome are one first-wins record, using the existing nonblocking snapshot/freeze design and CPython `dict.setdefault` publication assumption.
- An ordinary `close`/freeze that wins first permanently suppresses primary evidence. Later primary freeze cannot relabel it. It still preserves the old scalar packet.
- A primary freeze that wins first survives later cleanup and close unchanged. Later JSON decode, result validation, fixture shutdown and selection failures remain separate and still control the original success/failure gates.
- Lock contention at the primary freeze yields a permanently busy packet with no affirmative facts. An unconfigured capture may yield unavailable facts.
- Early command creation, request encoding, supervisor argument validation, pre-spawn cancellation, capacity refusal or spawn failure do not reach the primary finally block. They yield no primary envelope, not a fabricated `error` receipt.
- A pending snapshot never creates a primary label; observer pending reporting stays on the original raw packet route. A running fixture may expose primary evidence only if its actual primary finally boundary has already executed, independently of later cleanup.
- Copying/mutating returned packets cannot rewrite the retained primary record. This is not an atomic simultaneous sample of all pipes.

All non-pipe terminal facts remain unknown. The source identity and origin remain caller declarations; no helper authenticates the current host build or guarantees invocation identity outside this owned path. No persistence, native handle ownership, descendant supervision or durable acknowledgement is added.

## Verification scope

Controlled tests exercise real capture, supervisor, fixture and observer functions with in-memory fake process/supervisor objects. No child process or native dialog launches. The focused runner forbids Popen and network connect and selects inspected suites only. Existing full-suite native/process tests are deliberately not run. Source increment verification cannot qualify Windows/WebView, signed chemistry dependencies, runtime security, UI workflow or installed behavior.
