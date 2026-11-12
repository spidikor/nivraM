# Local PNG recognition setup

Open **PNG recognition setup** from the toolbar or use the setup button
in the PNG review dialog. Choose the separately prepared recognition runtime's
`python.exe`, then choose `swin_base_char_aux_1m680k.pth`. Both choices use native
file dialogs. The app shows the selected filenames; the browser does not
receive or submit filesystem paths.

Select **Verify model and save**. The app checks a local regular runtime file
without executing it and verifies the pinned model's exact size and SHA-256
using the trusted core worker. The verification has a 90-second work limit,
bounded input/output and the same 4 GiB owned-process memory ceiling described
in [recognition resource limits](optical-resource-limits.md). A successful save
activates the setup for future recognition without restarting nivraM.

This step does not install Python, install recognition packages, prepare an
environment, download a model, or run recognition. Use the provisioning and
preparation instructions in [optical recognition](optical-recognition.md).
Choose a runtime you trust. Model verification does not certify the selected
executable or establish its package compatibility; recognition checks
compatibility when it runs and rechecks the model hash on every request.

## Keep the current review

Setup changes only the configuration used by future recognition. An open PNG,
its crop, corrected draft, review acknowledgement and Undo/Redo history remain
in place. Saving does not recognize or accept a structure automatically. A
recognition already in progress must finish or be cancelled and finish
stopping before the configuration can be changed.

Cancelling a file chooser retains the staged choice. Cancelling setup discards
the staged choices and requests cancellation of any model verification still
running. Page reload and application shutdown also cancel verification. A
stale setup result cannot save over a newer setup transaction. Cancellation
does not undo a save that has already committed.

Invalid selections, a failed model check, or a settings conflict do not replace
the active configuration. Settings are written atomically and read back before
activation. If a write fails, the app checks whether the previous settings can
be confirmed; an uncertain disk outcome is reported explicitly. Reopen setup
after a conflict or uncertain outcome. The runtime, model and source PNG files
are never modified by setup.

## Network and verification boundaries

Setup verification and recognition use local files. AdGuard and the VPN can
remain enabled. Initial model provisioning may require the specific download
hosts documented in the recognition guide; ordinary local recognition does
not download a model.

Tests with synthetic native-dialog and recognition responses check UI and
state transitions, not native file-dialog operation or model accuracy. Source
tests, real-model checks, packaged worker dispatch, native startup and an
installed application's behavior are distinct validation scopes. The earlier
PNG installer does not acquire this feature merely because source files have
changed; a new package must include and validate it before delivery.
