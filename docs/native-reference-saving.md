# Native reference export saving

In the Windows host, pH reference JSON, CSV and PNG buttons use a scoped native Save dialog. Browser-only use retains ordinary browser downloads. This changes the reference-chart save route after repeated WebView successive-download failures; it does not establish the cause of those earlier failures or change chemical PNG rendering.

The native operation accepts only the selected format, encoded reference content and current session token. It does not accept a caller-supplied filename or path. Authentication and bounded format validation happen before the dialog. JSON/CSV retain their exact UTF-8 bytes, reference-only classification, provenance and spreadsheet escaping. PNG validation checks the complete encoded stream and the chart's current fixed 1000-by-560 dimensions. White-background and theme equivalence require separate pixel checks.

Each payload is limited to 4,000,000 decoded bytes. This is a reference-artifact transport limit, not a new limit on chemical PNG widths. Unsupported formats, incomplete PNGs, malformed reference envelopes, unsafe CSV cells and oversized input are rejected. The native method is added to the existing exact method allowlist; private and dotted method traversal remain rejected.

The user chooses the destination. Paths must be unambiguous absolute local-drive paths. Device, named-pipe, UNC, alternate-stream and normalization-alias paths are rejected. A missing format extension is appended before replacement confirmation, so confirmation names the actual target. A newly selected destination is published exclusively and cannot overwrite a file that appears in the meantime.

An existing destination is read within the same size bound before confirmation. Its expected hash is then supplied to the existing conditional writer. Replacement supports that writer's local fixed-NTFS, ordinary single-link file domain; unsupported destinations are reported without an unsafe replacement fallback. A unique short `.nv-ref-*.bak` file retains and verifies the old bytes before commit. Such backups are preserved on failure and are not silently pruned. An unrelated existing `.bak` file is never selected for replacement.

A saved receipt requires complete readback, byte count and SHA-256 equality. The frontend independently compares that receipt with the original blob. Cancellation, conflict, unsupported destination, failure and uncertain completion have distinct fixed messages. Native errors do not trigger a browser-download fallback or expose underlying paths and exception text. An uncertain result asks the user to inspect the selected destination before repeating the save.

There is one active reference save per desktop owner. Closing the chart cancels preparation before native dispatch and prevents a late response from updating a reopened chart. It cannot recall a native file operation already submitted. Application finalization rejects future work and waits at most 15 seconds for an active operation; incomplete cleanup remains an error. A transaction admitted before shutdown is allowed to settle and is not falsely labeled cancelled.

The operation does not change the molecular document, dirty state, project destination or pending project-open state. Its validation establishes the bounded reference transport, not independent scientific correctness of the reference dataset.

Source, browser-boundary, serializer/native-validator and native UI evidence are recorded separately in external increment receipts. Native chooser behavior, ordinary application shutdown, packaged/installed acceptance and the full platform/resource matrix must be verified against the exact source or artifact being claimed. The newer source remains distinct from the older installed candidate.
