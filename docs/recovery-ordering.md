# Recovery publication ordering

Recovery producers submit an immutable document together with a host-owned page generation and the document-store revision:

`ProjectStore.autosave(document, generation, revision)`

The generation is 32 lowercase hexadecimal characters. Revisions are exact integers from zero through JavaScript's maximum safe integer, 9007199254740991. Identity is envelope metadata; it does not enter the saved project or change the `nivram-recovery/1` disk schema.

## Page ownership and producers

The native host advances the generation through the private `DesktopApi._begin_recovery_page` callback. It is attached to the pinned pywebview `before_load` event, which is synchronous and precedes bridge injection. A failed transition leaves the API unavailable rather than reusing the previous page's ready state. `recovery_context()` only reads the current identity; the reset operation is not exposed through the native bridge. The publisher documents the event ordering in the [pywebview API](https://pywebview.flowrl.com/api/#window-events-before-load); implementation and synthetic vendor-event checks also cover the installed dependency.

Each document store binds its supplied generation once. Ordinary editing, opening, recovery, undo and redo retain that generation and use the existing increasing revisions. A new page receives a new host generation. Requests from an earlier generation cannot adopt or replace the current one.

Normal frontend autosave and `CloseCoordinator` both submit the generation/revision tuple to the same store. Manual-save and recovery locks remain separate, so a blocked manual save does not hold up recovery.

Manual native saving and the edit subscription initialize before waiting for recovery context. Context retrieval has a five-second deadline; a late response cannot bind after expiry. On successful initialization, the current snapshot is inspected so settled edits made while waiting still receive the ordinary two-second recovery debounce. Failure leaves manual saving available and explicitly reports recovery as unavailable.

## Storage and receipt contract

Storage validates and clones a document before publication. Under the recovery lock, it checks identity again and reserves the highest validated revision and canonical document digest before filesystem work. Lower revisions and other generations return `superseded` without publishing or rotating files. The fence remains after failed or uncertain publication.

A duplicate revision with different contents fails. A matching duplicate is acknowledged only after reading and verifying its exact candidate. An uncertain candidate is reconciled by identity and bytes; it is never blindly overwritten. Confirmed absence permits exclusive publication of that same candidate. Unreadable or altered candidates remain unconfirmed.

The in-memory reserved timestamp also survives failed publication and host page transitions. This prevents a clock rollback from ranking a later accepted publication below a possibly published candidate from the same store lifetime. Old schema-1 records remain readable and participate in the existing bounded history.

Verified success has exactly these fields:

```json
{"status":"recovered-copy-saved","id":"<timestamp>-<32-hex>","generation":"<32-hex>","revision":1}
```

Stale submissions return `status`, `generation` and `revision` with status `superseded`. They do not certify a newly saved copy. The frontend checks the submitted tuple and prevents stale or duplicate failure responses from replacing a verified current status. Close review validates the matching saved receipt before giving saved-copy assurance, then rechecks generation, revision and document contents before destruction. Unknown receipts and exceptions use an unconfirmed warning, including exceptions after publication.

## Verification and limits

Tests exercise the reproduced paused-validation inversion through actual storage, the real bridge/close producers sharing one store, page-generation transitions, duplicate conflicts, uncertain write/readback, candidate substitution, timestamp rollback, schema-1 compatibility and manual-save contention. Separate source-native checks cover crash/restart recovery and declined/approved dirty-close workflows. The synthetic concurrency test establishes the controlled ordering invariant; the native checks do not deliberately create that interleaving.

This protocol assumes the application's existing single-instance profile ownership and one active store for that profile. It does not establish arbitrary concurrent-process filesystem semantics, power-loss durability, universal filesystem compatibility, or ordering of previously unreadable records across process restarts. Corruption/migration, storage-failure UI, packaged/installed acceptance and the broader release requirements remain separate gates.
