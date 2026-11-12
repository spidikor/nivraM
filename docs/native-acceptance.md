# Native acceptance and failure evidence

The visible native acceptance run uses synthetic structures in a fresh, isolated
profile. It exercises the production editor, native file dialogs, PNG rendering
and review, disk readback, and owned-process cleanup. Source, frozen executable,
installed executable, and ordinary startup are separate verification scopes.

An installed run of source `575a9408` completed ten checks and then stopped
progressing before the first Save As check. Its supervisor terminated the child
after 180.516 seconds. The installer had exited successfully and its file/shortcut
verification had passed. The acceptance failure remains preserved; it is not
reclassified by later successful runs. The old report did not persist enough
evidence to establish the exact blocking call or an installed-path regression.

The next installed candidate, source `5cd95237`, passed installer and installed
file/shortcut checks but failed the PNG-preview check with a TypeError. It exited
normally after 16.25 seconds, with owned workers and the WebView session closed.
Its source and exact frozen executable had each passed twenty checks. Those
passes do not replace the failed installed check. Its returned preview value was
not captured, so the exact failing value cannot be reconstructed.

A real-browser regression reproduced a relevant readiness race: a PNG may be
complete before the continuation after `image.decode()` assigns its display
width. The previous probe then produces NaN, which JSON encodes as null. The
corrected probe waits for the open preview's Save button to become enabled and
its pixel-view width to be initialized. Typed validation rejects malformed scale
values before arithmetic. Bounded numeric/type evidence is saved before that
validation, and failure records retain their stage, last completed check and
evaluation independently of cleanup. Relative frozen traceback filenames retain
their harness line. Production PNG rendering and preview code are unchanged.

The harness now keeps an explicit stage and the last completed check. Each
JavaScript evaluation records its sequence, fixed stage identifier, deadline,
and terminal outcome. It does not record the script or its returned content in
these diagnostic events. Arbitrary exception messages are omitted; exception
kind and the harness line identify failures. Chemical assertions still use the
underlying returned data locally.

Only one evaluation may be outstanding. A daemon worker bounds the caller's
wait to ten seconds by default, or the remaining polling budget when shorter.
Timeout after dispatch permanently rejects later JavaScript submissions. The
already submitted operation may still execute: its outcome is indeterminate,
and a late result cannot overwrite the timeout record. A deadline exhausted
before dispatch is recorded as not submitted. Pending-operation cleanup has a
three-second caller deadline.

Native file-dialog automation runs in a separate, hidden supervised child. Its
request is restricted to one parent process and creation identity, the parent's
window, and a direct synthetic fixture beneath the isolated profile. It checks
the dialog and filename-field ownership before acting and verifies the typed
filename. Ambiguous or unsupported dialogs fail closed. Only the direct owned
child is terminated on timeout. The parent retains and closes every started
fixture, including a fixture whose JavaScript trigger fails before selection
finishes. Selection success alone does not establish worker exit.

Report replacement is atomic. Original failures are persisted before native
cleanup; a later checkpoint-write failure is recorded separately and does not
skip the remaining cleanup steps. If persistence itself is unavailable, the
process exit and prior complete checkpoint remain evidence of failure, not a
successful run.

The first preview-repair source run passed seventeen functional checks, including
the corrected PNG preview, then failed at evidence replacement with a Windows
PermissionError. Cleanup completed normally in 27.203 seconds. A concurrent
read of that report may have held an incompatible sharing mode; the original
numeric Windows error was not captured, so that cause remains an inference.
A real Windows handle test confirms the sharing-conflict mechanism. Report
replacement now retries only Windows access/sharing errors 5, 32 and 33 for six
attempts, with 0.31 seconds of cumulative sleep. The previous complete checkpoint
is preserved; persistent failure still propagates. This bounds added waiting,
not arbitrary filesystem syscall duration. Numeric OS error codes are retained
in later failure records.

The profile-preview probe samples the top-left pixel and compares its inline CSS
width times the reported device-pixel ratio with the PNG width. It does not
independently measure physical screen pixels or inspect every preview margin.
The separate saved-PNG acceptance check supplies its own decoded opacity and
margin evidence.

The first full preview-repair suite also recorded one Windows connection reset
while reading a rejected foreign-origin POST response. Twenty focused repetitions
did not reproduce it. Immediate connection closure with unread input is a known
response-delivery risk described in [RFC 9112 section 9.6](https://www.rfc-editor.org/rfc/rfc9112.html#section-9.6),
but that does not prove the cause of the observed reset. Early POST rejection now
sends its final response first, closes the write side, and discards at most
64 KiB using a single 250 ms drain deadline. Socket writes also have a short
timeout; this is not a hard 250 ms bound on all OS scheduling or handler work.
Only a single unambiguous decimal length is used for discarding; rejected input
is never parsed or dispatched. Oversized, ambiguous or stalling peers are not
promised graceful response delivery. Real-socket tests verify this bounded close
behavior and unchanged rejection statuses; they do not reproduce the original
reset or establish a general hostile-network robustness claim.

These are bounded test-harness controls, not a new application API. They cannot
interrupt arbitrary native code that holds the Python runtime, a blocked OS
process creation, or a blocked UI-dispatch shutdown. The outer 180-second
acceptance supervisor is still the final process deadline. A forced exit,
unconfirmed owned-worker exit, or incomplete runtime cleanup must not be reported
as full native acceptance. These checks do not establish clean-machine behavior,
cross-version migration, full accessibility, or scientific prediction accuracy.
