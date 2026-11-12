# Recovery diagnostics and acceptance scope

Recovery now inventories at most 1,000 directory entries and distinguishes an
empty history from a failed scan. Damaged, unsupported, unreadable and interrupted
records remain on disk. Unsupported recovery or project schemas are not migrated
implicitly. Actual atomic-writer staging files use the `.nv-` prefix.

A recovery read prepares a generation-bound token. The previous project destination
is detached only after the editor accepts the recovered document and acknowledges
that token. Saves, accepted opens and host page changes revoke pending recovery.
The frontend rejects replies after the drawing or destination changes; a five-second
bridge deadline releases manual saving without applying late results. Recovered
documents remain unsaved and require a separate destination.

## Verification boundaries

The focused Python checks use real local files, the installed pywebview dispatcher,
generation/revision fencing, concurrent calls and exact owned writer-process
termination. They distinguish completed recovery publication from interruption
after flushing and before publication. They do not establish power-loss durability.

The JavaScript model checks exercise production recovery code with controlled bridge
promises and editor outcomes, including timeout, rejection and failed acknowledgement.
They do not establish native browser rendering or user input.

The `recovery-errors` native scenario uses the actual source application in Windows
WebView2, trusted clicks, real isolated diagnostic files and an actual Save dialog
Cancel return. Only the isolated record-read and directory-enumeration failures are
injected. It checks empty history, retained error notices, unavailable storage,
a stale inventory reply after an edit, and continued manual-save access. It never
replaces a confirmation dialog. Successful native restoration, confirmation rendering
and interrupted native restart remain separate pending journeys.

The initial long-path regression result is retained: a 260-character destination
failed on this Windows runtime. The unchanged 68-test selection passed using a
shorter unique case name. This does not qualify paths at or beyond that boundary.
