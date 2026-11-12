# Fresh supervision receipt component — 2026-11-01

This is newly authored work, not recovery of the missing historical S2.2 bundle.
Baseline: the verified 449-file preserved source, aggregate
`ae206385f524fddc4528f928510ef654f57f1f478a7c034caa73794920f8a792`.
The historical staging, original Case-A outcome and uncertain candidate-listing
delivery remain unresolved. This component does not implement or impersonate the
historical `/3` supervisor or `nivram-durable-terminal/1` interfaces.

## Bounded increment and interfaces

Owned source files: new `nivram/supervision_receipts.py`, new
`tests/test_supervision_receipts.py`, and this document. No existing application,
worker, native-lifetime, ownership, chemistry or acceptance interface changes.

Target behavior:

- Represent one attempt's bounded, privacy-limited terminal observations with a
  fresh schema. Unknown observations remain unknown; supplied facts are not host
  authentication. No command, path, process ID, molecule or arbitrary error text
  is accepted in the record.
- Distinguish recorded exit/ownership/output/cleanup facts, complete recorded
  evidence, a normal exit, successful persistence and writer-thread exit.
  A valid JSON document is not evidence of any of the latter by itself.
- Write a single fresh attempt using exclusive file creation; never overwrite or
  remove an old receipt. A partial or failed write is retained as failed evidence.
- Acknowledge persistence only after writing all bytes, flushing, successful file
  `fsync`, closing and exact-byte bounded readback. This does not claim atomic
  publication, directory-entry crash durability, power-loss recovery or Windows
  filesystem qualification.
- Retain one daemon writer handle per submitted attempt. A finite caller wait
  reports a snapshot; it does not cancel a blocked writer or imply it stopped.
  Later acknowledgement cannot mutate the earlier timeout snapshot.

Offline tests use only standard-library Python, synthetic evidence, in-process
threads and task-owned temporary files. They cover incomplete and contradictory
observations, invalid types, strict/duplicate-key JSON, oversized or wrong-bound
records, nonzero exit, missing EOF/cleanup, exclusive publication, write/fsync/
readback failures, timeout followed by late acknowledgement, frozen snapshots,
and safe concurrent attempts. No application or child process is launched.

## Public API and evidence meanings

`TerminalEvidence` is a frozen record with a canonical UUID4 attempt ID,
the candidate's lowercase SHA-256, and an outcome of `returned`, `timeout`,
`cancelled`, `interrupted` or `error`. The primary outcome is fixed before
persistence and is never rewritten by later writer success. Unknown facts are
`None`; strict booleans and bounded integers prevent truthy substitutions.
The caller must assign a fresh attempt ID; format validation does not establish
global uniqueness or freshness across separate directories or hosts.

The facts are separate: owned-handle binding, root exit, completed stdin write,
stdin close, stdout/stderr EOF, output drain, output close, reader-thread exit,
cleanup confirmation, remaining descendants and whether cleanup was forced.
`stdin_write_completed` requires evidence that all intended bytes were written
(or the explicitly specified input was empty); closing stdin is insufficient.

`assess_recorded_evidence` reports completeness within this schema and whether
the supplied facts describe an unforced zero-code returned outcome. It proves
neither authoritative outer-owner completion nor authenticity. Timeout,
cancellation, interruption and error remain non-normal even when all subsequent
cleanup facts are available. No assessment grants execution admission.

`encode_terminal` / `decode_terminal` use the new schema
`nivram-fresh-supervision-terminal/1`, with an 8,192-byte limit, exact fields,
strict JSON and explicit expected attempt/source bindings. `read_terminal`
checks a bounded regular file. It returns evidence, not a recovered persistence
acknowledgement. Reading valid bytes after failed fsync does not prove fsync.

Create `ReceiptWrite(directory, evidence)` in an existing task-owned directory,
retain that object, then call `start()` once. `wait(seconds)` accepts a finite
zero-to-thirty-second wait and returns an immutable `WriteSnapshot`. Its
`receipt_acknowledged` and `writer_exited` fields are separate observations.
The file-stage flags record successful acknowledgements; a false flag means the
stage was not acknowledged, not proof that the underlying filesystem effect
never occurred.
Repeated start is rejected. A collision or partial failure leaves the prior file
untouched or the newly created partial file retained; a later attempt needs a
different identity. No automatic retry, cancellation or next case occurs.

The record contains no arbitrary exception strings, paths, commands, process IDs
or scientific payload. The writer reports only a fixed failure stage. The API
requires a trusted owned directory; it is not a sandbox against an attacker
concurrently replacing parent directories or file contents.

Relevant standard-library contracts are documented in Python's
[file sync guidance](https://docs.python.org/3.12/library/os.html#os.fsync) and
[thread wait guidance](https://docs.python.org/3.12/library/threading.html#threading.Thread.join).
Those pages describe the Python 3.12 series; actual checks here use the available
3.12.14 Linux interpreter, not a newly installed or qualified Windows runtime.

## Genuine remaining host contract

No adapter is connected to `WorkerSupervisor.run` or the Windows launcher.
Its current returned `CompletedProcess`, exception diagnostics and snapshots do
not by themselves establish exact-Job membership, retained-handle lifetime,
owner-specific tool-abort cleanup, output-drained/closed acknowledgement,
descendant accounting or authoritative coordinator completion.

A future adapter needs a separately reviewed exact candidate/runtime binding and
actual supported observations for each field. The Windows host must establish
owned process/job handles, assignment-before-resume where applicable, completion
and interruption semantics, bounded output closure and cleanup acknowledgements.
It must not translate a timeout, EOF alone, PID, callback return, outer containment
or valid terminal bytes into successful application shutdown. No next attempt is
automatically admitted by this component. Native launches and related gates stay
held pending parent review and their own prerequisites.
