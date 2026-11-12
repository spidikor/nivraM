# Reliability increment: source changes and evidence boundaries

The complete Goal remains active and incomplete. Installed source 23f09d94 is
the verified development baseline. The changes described here are not built or
installed. Historical delivery files remain unchanged.

## Close review

`nivram/lifecycle.py` bounds the caller's waits for a close-review snapshot,
revision recheck, and release of the editor after cancellation. Only one native
evaluation can be outstanding. An explicit confirmation distinguishes a saved
copy of the reviewed revision from an unverified latest editor state. Late
results do not save a document or close a window automatically.

Independent review reproduced a regression in the first patch: a failed native
window destruction left its evaluator closed and the editor suspended. The
repair reopens the existing evaluation lane and releases the token that actually
owns the suspension, including an older attempt whose native call is still
outstanding. A failed release retains its ownership for subsequent recovery.

The historical focused source suite passed 14 tests, exit 0. Root independently ran
the same suite: 14 passed in 0.372 seconds, exit 0 (tool receipt 52c155). The
independent baseline/current destroy-failure reproduction passed both cases.

These focused tests are controlled native-boundary tests. Ordinary source-native
Windows close/recovery has now separately passed ten checks on source f5acdd57:
clean close, declining dirty close then editing again, approval with a durable
reviewed copy, recovery as unsaved, and exact saved-document equality. All three
test instances and 27 helpers exited normally; new WebView sessions were removed.
Receipt: `work/native-close-source-f5acdd57-1.json`. This is historical native
evidence for f5acdd57, not acceptance of the later integration below. At that
revision, services stopped by before-destroy cleanup were not automatically
restarted if window destruction failed. Actual vendor-stall/timeout UX and
failed-destruction service recovery were not demonstrated by this native run.

## Frozen source close integration

The later source repair removes irreversible teardown from close review. Failed
or vetoed native closure releases the editor review while services remain live.
The owned native FormClosed callback records actual closure before vendor work;
a destroy return alone is insufficient. A permanently terminated evaluation lane
prevents a late closed notification from being undone by a stale resume path.
The first three regression tests failed on the original implementation (exit 1,
tool receipt 1f44ae). A controlled late-close race separately failed before its
repair (exit 1, receipt 75a82c).

A per-window hidden WinForms guard keeps the STA callback loop available during
finalization. Owned download cancellation and idempotent WebView disposal run on
the native thread after actual closure. Independent resource owners receive
shutdown once, so a stalled or failed stage does not prevent another stage from
starting. Post-disposal workers consume cached receipts and matching
BrowserProcessExited evidence without posting to a disposed control. Missing or
mismatched collection-exit proof prevents session deletion.

The four affected source suites passed 73 tests in 1.195 seconds, exit 0
(receipt 3717e2). All nine changed production/test files passed AST, whitespace,
newline, conflict-marker and git diff validation, exit 0 (receipt 0ae31e).
The earlier four-case synthetic lifetime spike supports feasibility; it is not
native acceptance of this production integration. No integrated native app run,
build or installation has been performed. Its watchdog requires a responsive
STA and cannot interrupt a genuinely stalled synchronous native callback on
that same thread. It is not an unconditional 35-second process-exit guarantee.
Returned, pending, failed, retained and unconfirmed finalization receipts must
remain distinct; exit code alone does not prove complete cleanup.

Root's full source suite on tested runtime identity
`17c8eec81fbed7a510d2e964b1db09bf95b7d902a6551e5b5c16d7e89328da08`
ran 477 tests: 476 passed, one known save-race failure, zero errors, exit 1, in
33.936 seconds (receipts 59acaa, 3e429a and d6f44c). The source snapshot contained
179 files (receipt e6a467, exit 0). Documentation reconciliation follows that
runtime snapshot; this is not a claim that the later documentation-inclusive
source hash equals the tested runtime identity.

## Editor regression coverage

The Alt-modified shortcut guard was repaired. The pure/controller JavaScript
suite passed 124 tests in 6.820 seconds, exit 0 (receipt 8940ca). Five pointer and
four keyboard production-route browser scenarios passed together: nine passed,
exit 0, in 71.434 seconds (receipts b17f17, 2a3372, 8e7594 and 72435a). The final
focused valence-assertion check passed, exit 0, in 7.908 seconds (receipt c602a5).
These are browser/source checks. Actual Windows scaling, screen-reader behavior,
native focus, broader chemical-artwork contrast and packaged acceptance remain
open. See `docs/keyboard-editing.md`.

## Saving

The existing-file late-replacement race remains unresolved. A deterministic
regression places a real external replacement after the final hash comparison
and before publication. No SaveConflict is raised. Production persistence code
has not been changed, and the failing assertion remains enabled.

A five-case real Windows 11 build 26200 probe tested target sharing guards with
ordinary and POSIX handle-based rename. Denying delete sharing blocked both
tested publication routes; permitting it allowed POSIX replacement but did not
establish conditional publication against an expected destination identity.
The probe exited 0, all ten explicit handles closed, all four direct children
exited, and its synthetic temporary tree was removed. This is evidence against
that proposed protocol, not a repaired save guarantee.

Workspace evidence: `work/save-protocol/windows-rename-probe.json` and its
matching probe source. No Transactional NTFS or production save implementation
was introduced.

## Installed recovery experiment

The external helper uses the ordinary installed executable, a fresh synthetic
profile, native accessibility and file dialogs, and retained spawned-process
handles. It does not inject JavaScript, call the application server, use a
default user profile, or terminate unrelated processes. Every installed bundle
member was freshly checked against the pinned manifest: 769 matches, exit 0
(tool receipt 948f30).

Independent helper review found and repaired child-cleanup failure paths and a
recovery-directory scan race involving temporary or rotated files. Its tests
also cover delayed accessibility value publication. The current helper suite
passed 18 tests, exit 0. These checks do not establish native recovery success.

Attempt 1 reached ordinary editor readiness, then failed while verifying entry
of the synthetic source field. The test instance and all four helper processes
exited; the intended crash/recovery scenario was not reached. Its receipt and
profile remain preserved at `work/native-recovery-installed-23f09d94-1.json`.
After a bounded two-second fresh-value observation was added, attempt 2 passed
all nine checks, exit 0, in approximately 45.12 seconds. Recovery was readable
2.172 seconds after the pre-invocation timestamp for Load structure. The exact
recovery bytes survived intentional termination of the retained test-process
handle. The ordinary restarted application recovered CCO through its production
UI as an unsaved document; Save selected a separate file, which matched the
recovery document exactly. The newer saved CCN project remained byte-identical.
The restarted app exited normally with code 0 and its new session directory was
removed. All 26 accessibility helpers exited normally with code 0; the earlier
crash session was retained. Receipt:
`work/native-recovery-installed-23f09d94-2.json`. These results apply to installed
23f09d94, not to the unbuilt close-review patch. The first failure remains
preserved; its exact root cause is not established solely by the later pass.

Even a passing process-crash/restart test would not establish power-loss
durability, clean-Windows acceptance, or complete descendant cleanup after a
forced crash. Those requirements remain separate.

## Bounded WinRT feasibility experiment

The historical inbox-runtime synthetic transaction experiment completed eight cases, exit 0.
The transaction stream contained the original bytes; stale-before-open content
was rejected; control, abort and precommit termination behaved as recorded.
In-place, ordinary replacement, POSIX replacement and delete/recreate competitors
all failed while the transaction was held. All sixteen owned children were reaped.
Receipt: `work/save-protocol/winrt_run_44c1ae64c33b4d658fd71cf56b18d685/receipt.json`.

These competitors completed before CommitAsync began. The experiment therefore
does not test competition inside a possible internal commit gap, termination
during staging/commit, remote/cloud filesystems, or power-loss behavior. No
production save code changed and the failing late-replacement regression remains.

A later commit-overlap experiment rejected the WinRT proposal. Nine controls
passed; the first tested contender, `inplace-delay0`, produced a counterexample,
so the remaining seven contender cases were not run. A foreign in-place write
and its fresh readback occurred during CommitAsync, followed by the transaction's
own bytes after disposal and competitor exit. This refutes the tested protocol's
protection against that lost-update interleaving; it does not establish that
every possible WinRT-based protocol fails.

Receipt:
`work/save-protocol/winrt_commit_run_9ba1c62ee28a48388471fba553c5f2c8/receipt.json`
(SHA-256 `0533ff8ba13bb4f0dfe11b602007e47d966aa08dcc43424f62b61c24bbf1601d`).
The experiment exited 0 because the counterexample was recorded (receipt 5d2edf),
not because a safe save protocol was established. An independent root verifier,
`work/save-protocol/verify_counterexample_9ba1c62e.py`, also exited 0 (receipt
3b9a97). All 13 owned children were reaped: 12 normal exit-0 completions and one
intentional staged-termination exit 1. Production persistence remains unchanged,
the known regression remains enabled, and no Transactional NTFS was introduced.
