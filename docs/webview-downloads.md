# Deferred native downloads

The download adapter belongs to one nivraM window. It accepts current-origin
`blob:http://127.0.0.1:PORT/UUID` URLs with PNG, SVG artwork, MDL MOL, JSON/project, or CSV MIME
types. The native save dialog is the only source of an output path. Paths, names,
URLs, and structure contents are excluded from the adapter's receipts.

## Integration

Before `webview.start()`, after attaching the native request policy:

```python
from nivram.webview_downloads import attach_downloads
downloads = attach_downloads(window, url)
```

The pinned pywebview 6.2.1 callback reads `browser.on_download_starting` when
initialization finishes. The adapter replaces that attribute on this window's
browser instance before initialization, so the original callback is never
subscribed for this window. The posted initialization callback checks instance
identity; it does not remove an absent native delegate. Vendor files, global
classes, other windows, navigation, request filtering, and cleanup callbacks are
unchanged. Setup must happen before the native core exists; partial setup failure
disposes the affected control instead of continuing with an unguarded handler.

In an acceptance worker, read `before_id = downloads.snapshot()['last_id']`
before requesting an export, then finish the native chooser interaction and call:

```python
receipt = downloads.wait_for_download(after_id=before_id, timeout_seconds=30)
assert receipt['state'] == 'Completed' and receipt['settled']
```

Only a native `DownloadOperation.State` receipt can report `Completed`. A readable
PNG, selected file path, clicked button, or returned dialog is insufficient.
After this receipt, independently decode and verify the actual saved bytes.

From the close coordinator's worker, before managed WebView disposal:

```python
download_cleanup = downloads.shutdown(timeout_seconds=5)
# Record shutdown_confirmed; false remains an explicit lifecycle failure.
browser_cleanup = session.shutdown(timeout_seconds=20)
window.destroy()
```

`cancel(download_id, timeout_seconds=5)` cancels only the matching owned operation
and returns whether its terminal state was confirmed. Cancellation and shutdown
never kill processes or remove downloaded files. Shutdown rejects later download
requests, cancels only unfinished owned operations, and completes pending
deferrals. Completed operations are never cancelled. An active save dialog is
sent Cancel only when its title, native class, direct owner, thread, and process
match the adapter's chooser. An unmatched/nested dialog is not closed by guessing;
the bounded result remains unconfirmed if that dialog does not return.

Worker waits cannot be called on the UI thread. UI callbacks hold the COM objects;
worker threads read only cached receipt fields. At most one download is active,
and the last 32 settled receipts are retained. Diagnostic fields use fixed codes
and numeric identities. A JSON export permits `.nivram` or `.json`; the picker
uses matching native filters and overwrite prompts. An unexpected final extension
is rejected rather than rewritten after the user's overwrite decision.

## Evidence and limits

The existing `native-visible-9999-1.json` run confirmed the synthetic PNG contents,
worker exit, and profile cleanup, but WebView2 reported `BrowserProcessExited`
`Failed`. Its pre-close-to-final interval was 15.453 seconds, versus 4.438 seconds
for `native-visible-dialogs-3.json`, which did not perform a browser PNG download.
Those observations identify a useful differential; they do not prove a cause.

The first adapter native run stopped before navigation because its redundant
attempt to remove the never-subscribed vendor callback raised pythonnet's
`ValueError: unknown event handler`. A no-window experiment with the installed
pythonnet and `System.Timers.Timer.Elapsed` reproduced the exact contract:
removing a never-added callback fails, removing an added callback succeeds, and
removing it a second time fails. The redundant removal is now absent; the test
event mirrors this managed bridge behavior. Fresh native acceptance is still
required after this correction.

Inspection of installed pywebview 6.2.1 `edgechromium.py` found a synchronous
`SaveFileDialog.ShowDialog()` inside `DownloadStarting`, without a deferral or
`Handled=True`. The adapter takes `GetDeferral`, marks the event handled, posts its
dialog work to the UI queue, returns from the browser event, and completes the
deferral in `finally`. This follows Microsoft's documented threading contract.
It suppresses the default download panel, so closing that panel is not used as
an assumed cure. Microsoft documents `CloseDefaultDownloadDialog` as a visibility
operation, not as a mandatory disposal prerequisite or download cancellation.

Focused tests cover event order, strict origins/types/extensions, native chooser
authorization, completion receipts, bounded cancellation, privacy, and per-window
isolation. They do not prove native interop, actual modal cancellation, or that
the adapter cures the recorded browser failure. Those require fresh native runs.
For the next differential, keep the same runtime, synthetic 9999-pixel fixture,
isolated session, request policy, and browser shutdown checks; record native
download completion before decode and close. Also record time-ordered
`ProcessFailed` kind/reason/exit code and `BrowserProcessExited` kind. A successful
run establishes observed behavior for that build; a failed exit still fails
lifecycle acceptance even when files and worker cleanup pass.

Primary contracts reviewed 2026-09-19:

- [WebView2 threading and deferrals](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/threading-model)
- [DownloadStarting event arguments](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2downloadstartingeventargs?view=webview2-1.0.3856.49)
- [Download operation states and cancellation](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2downloadoperation?view=webview2-1.0.3856.49)
- [Closing the default download dialog](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.closedefaultdownloaddialog?view=webview2-dotnet-1.0.3856.49)
- [Process lifetime and failure events](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/process-related-events)
- [Owned native popup lookup](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastactivepopup)

## Passive permission observations

The per-window download adapter also observes `PermissionRequested` on the owned
UI thread. It subscribes after core initialization and removes its retained
callback during shutdown. It does not set permission state, mark requests
handled, acquire a deferral, change profile permissions, or display a dialog.
Observer failures are separate from download failures and do not disable the
application or grant an exception.

Local diagnostics expose a strictly filtered `permission_observer` record:
subscription status; bounded seen, read-failure and dropped-event counts; and at
most four events. Each event contains a sequence number, the current download
trace sequence, a fixed permission-kind code, the user-initiation flag, an
allowlisted state, and whether the requesting origin matches the application.
URI contents and exception text are never retained. Unknown permission kinds
use -1; the inspected SDK's `MultipleAutomaticDownloads` kind uses 7. Snapshots
copy cached Python values and do not read native objects from a worker thread.

These observations distinguish a browser permission request from an event that
reaches `DownloadStarting`; they do not establish what ultimately granted or
denied a permission. Zero events are interpretable only when subscription is
confirmed, and read/drop counts must be considered. Actual callback delivery,
removal and export behavior require native acceptance on the exact source.
The native helper retains its existing 7000-byte diagnostic payload limit.

Contract inspected 2026-09-21: bundled WebView2 Core SDK 1.0.3856.49 and
[Microsoft's permission event arguments](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2permissionrequestedeventargs?view=webview2-dotnet-1.0.3856.49).
