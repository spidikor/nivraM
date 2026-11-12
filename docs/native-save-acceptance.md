# Native project save acceptance

The fixed `save` scenario in `nivram.acceptance` exercises the production desktop save controls and actual local storage using synthetic documents in an exclusive profile. Its report must pass the existing process, native runtime, network guard, picker, worker and cleanup gates.

The journey requires all of the following:

- Cancel the real Save chooser without dispatching a store write or changing document/history state.
- Save a new file, then update it through the Windows conditional writer and verify the exact previous-file backup.
- Hold a save while a later native edit completes. Read back the submitted snapshot and require the newer editor state to remain unsaved.
- Hold a bridge save before writer acquisition while another thread replaces its destination. The actual conditional adapter must reject the stale hash and retain the newer bytes and earlier backup.
- Inject one acknowledgement error after the real exclusive atomic writer has returned and its published bytes have been observed. Require an unconfirmed receipt, exact unsaved state, retained original destination, usable recovery copy, and a chooser on subsequent ordinary Save attempts. Cancel once, then save a separate copy.
- Create a real second hard link to a synthetic saved file. Require the adapter's unsupported result, retained bytes at both names, explicit Save As guidance, and a separate verified save after a canceled chooser.

Seven actual chooser returns, seventeen trusted button gestures, eleven bridge receipts and eight store calls are required. The frontend bridge observer preserves every argument, result and exception. Only the controlled save barriers and explicitly recorded post-publication error alter execution; production save code and document algorithms are unchanged by this scenario.

Protected updates currently require an ordinary resident, single-link file on a local fixed writable NTFS volume that supports transactions, with ordinary non-reparse parents. Unsupported paths do not fall back to an unprotected overwrite. A separate new destination can be selected with Save As. A conflict means reopen the newer file or save a separate copy. An unconfirmed outcome means inspect the destination before saving again; the drawing remains open and recovery availability is reported separately.

The scenario establishes a source-runtime result only when its exact-source receipt passes. It does not establish power-loss durability, arbitrary concurrent-process behavior at every Windows transaction boundary, network or removable filesystem compatibility, restart recovery, packaged or installed acceptance. The replacement is a controlled second-thread operation; the acknowledgement error is injected. Physical hardware input and standalone browser qualification remain separate.

