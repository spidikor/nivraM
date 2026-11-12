# Bounded worker failure diagnostics

The worker supervisor distinguishes input limits, occupied capacity, I/O failure,
and incomplete cleanup using fixed categories. When cleanup fails after another
error, cleanup still determines the final failure. The receipt retains a fixed
category for the earlier error and the earlier timeout observations when present.
It does not inspect arbitrary exception chains, messages, commands, or payloads.

Timeout observations describe the deadline before cleanup. The separate cleanup
packet records process-stop confirmation, each stream-completion event, and
whether the supervisor's threads exited after the existing cleanup budget.
Neither packet certifies native selection, original chooser returns, or success.
Later shutdown confirmation does not overwrite earlier cleanup observations.

Both boundaries validate exact dictionaries and fixed schemas. Extra keys,
custom mappings, invalid types, and incoherent categories become unavailable.
No captions, paths, nonces, exception messages, or stream contents are exported.
The native fixture only accepts attributes of the exact supervisor exception
class; subclass properties are not evaluated. Its attribute dictionary must
also be an exact dictionary containing only exact string keys, preventing
custom getters or colliding key equality methods from running during reporting.
Pre-spawn failures cannot claim child cleanup observations, and incomplete I/O
cleanup must use the cleanup-failure category.

These observations do not change worker deadlines, cleanup budgets, dialog
selection, rendering, chemistry, or the six native acceptance gates. A fresh
build and native run must qualify this source separately from earlier builds.

The running-child timeout test uses no input to establish input completion
synchronously. Previously it assumed a small asynchronous write had finished
before a 0.75-second deadline. The Windows probe observed a completed small
write and a blocked large write on this run; earlier focused runs observed
the small-write completion flag still false. Payload size is not a scheduling
guarantee. The separate real blocked-writer regression remains in place.
