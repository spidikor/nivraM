# Fresh parent-I/O receipt projection — v007, 2026-11-03

This is a newly authored continuation of v006, not recovery of the missing historical S2.2 staging bundle. The finite change consists of `nivram/supervision_observations.py`, its dedicated tests and this document. All 452 v006 source files, including the 449 original preserved files, remain unchanged. No production caller is rewired.

The adapter closes one small representational gap: the existing parent pipe capture supplies a bounded scalar packet, while the portable receipt component expects explicitly named terminal observations. It projects only facts supported by that packet. It cannot complete Windows supervision or qualification.

## Source contract inspected

`parent_io.py` validates the available/status, input-writer expectation, retained-byte bounds, reader entry, EOF, input write and input close marks. The inspected v006 file has SHA-256 `180ab12e6aad667008b842f4e935182d628b0bfd65e6476fbce1f20357e6f9c3`.

In `WorkerSupervisor.run`, `worker_processes.py` records EOF only after reading an empty byte string (around line 303), input completion after its entire payload loop (331), and input close after closing stdin (341). A BrokenPipe path can close stdin without completing its write. EOF is recorded before stream close; it proves neither successful close nor reader-thread exit. The producer freezes the capture before owned cleanup (390). Stream facts are collected independently, so the packet is not an atomic view at a deadline. The inspected worker file has SHA-256 `da177cf3f797183b296a4b9fba0f4ae0fe67823618c7cc82aee2a75c82730e70`.

The adapter imports only the existing scalar sanitizer and portable receipt types. It does not import `worker_processes`, whose import creates a default supervisor and registers shutdown. It neither reads handles nor calls a capture, thread, filesystem, worker, native API or provider.

## API and accepted evidence

`bind_parent_io(packet, attempt_id=..., source_sha256=..., origin=..., boundary=...)` copies a stable caller-owned exact scalar packet into a frozen `ParentIoObservation`. The caller must supply a canonical UUID4 and lowercase SHA-256. `origin` is explicitly `synthetic` or `host_reported`; `boundary` is exactly `primary_pre_cleanup`. These are caller declarations. The raw producer packet contains no invocation identity, provenance or proof that freeze was called. This binder does not authenticate those claims or make a live snapshot frozen retroactively.

`project_parent_io(observation, expected_attempt_id=..., expected_source_sha256=..., outcome=...)` rejects a different attempt or candidate and returns a frozen `ParentIoProjection`. It creates fresh `TerminalEvidence` and preserves the supplied primary outcome; it does not merge previous evidence or infer an outcome from successful I/O. Malformed packets, extra/private fields, noncanonical unavailable packets and truthy type substitutions raise a fixed diagnostic without input text. Canonical `busy` and `unavailable` packets are accepted as unknown.

| Affirmative mark in a valid packet | Projected field |
| --- | --- |
| `stdin_write_completed=True`, with writer expected | `stdin_write_completed=True` |
| `stdin_close_completed=True`, with writer expected | `stdin_closed=True` |
| `stdout_eof=True` | `stdout_eof=True` |
| `stderr_eof=True` | `stderr_eof=True` |

False marks remain `None`: not yet observed does not establish that an event never occurred. When no input writer was expected, both stdin fields remain unknown. This conservative policy does not infer the existing receipt contract's possible empty-input completion allowance.

All other facts remain `None`, including ownership, root exit, return code, output drain and closure, reader exit, cleanup, descendants and forced-cleanup history. Even an all-affirmative pipe packet cannot produce complete or normal-exit evidence. EOF, reader entry, retained-byte count, lack of an input writer, or a caller's `returned` outcome cannot fill those gaps.

## Provenance and persistence boundary

The return wrapper retains origin, declared boundary, packet status and names of mapped fields. Synthetic fixtures must retain that label in reports. `host_reported` means only caller-declared reporting, not authenticated host events. No implementation here connects it to genuine Windows observations.

The existing receipt encoder and writer accept exact `TerminalEvidence`; passing the projection wrapper directly is rejected. Deliberately extracting `.evidence` and encoding it with the unchanged `/1` schema drops the wrapper's provenance and boundary. There is intentionally no persistence bridge in this increment. A later bridge requires a reviewed provenance-preserving contract; receipt bytes alone do not recover writer acknowledgement or prove host occurrence.

Frozen records and strict scalar validation prevent ordinary caller mutation and custom mapping/value hooks. They do not provide a security boundary against hostile Python code, concurrent mutation of an input being supplied, or false caller declarations. The caller must provide a stable packet from the correctly bound invocation.

## Offline acceptance scope and remaining dependency

Tests use synthetic scalar packets and in-memory `ParentIoCapture` operations only. Required cases cover each supported mark, false/unknown marks, missing input writer, busy/unavailable captures, incomplete stdin write despite close, all-true-but-incomplete evidence, binding mismatch, unsupported origin/boundary, malformed/private inputs, caller mutation, frozen labels, and rejection of direct wrapper persistence. No child processes, native workers, Windows calls, provider operations or installations are required.

The remaining real host dependency is unchanged: a reviewed adapter must bind the actual candidate/runtime and retained Windows Job/process handles, preserve pre-cleanup versus cleanup phases, and independently observe owner completion, interruption, process exit, output drain/closure, descendant state and cleanup. In particular, an ownership snapshot's `forced_count=0` is not an execution history, and finalizer or guard flags do not establish application/outer-owner completion. No execution admission follows from this projection. All historical recovery, native, security, reference and publication holds remain.
