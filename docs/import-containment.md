# Import resource containment

Text import through `/api/import` and batch preparation of `.mol` and `.nivram`
files use one shared preparation service in the desktop application. Parsing,
project and chemical validation, descriptor calculation, coordinate generation
when required, and scene rendering run in a new worker process for each request.
The parent validates the bounded returned data without invoking RDKit again.
Existing molecular identities, coordinates, metadata, original project validation
and single-record MOL rules remain part of this operation.

## Enforced boundaries

Before reading submitted input or loading native chemistry, the worker installs
and reads back an unnamed Windows Job Object with these required settings:

| Boundary | Value |
| --- | --- |
| Process committed memory | 1 GiB |
| Process user CPU time | 10 seconds, checked periodically by Windows |
| Active processes in the worker job | One |
| Parent wall deadline | 20 seconds for the supervised operation |
| Concurrent desktop import/batch preparations | One; extra requests receive busy |
| Source | 4,000,000 UTF-8 bytes |
| Request transport | 16 MiB |
| Response transport | 8 MiB |
| Worker stderr | 64 KiB |
| Parsed response data | 250,000 values, depth 64, 100,000 entries per container |

The job handle is non-inheritable and retained until worker exit, with kill on
job close. Unsupported platforms, failed installation, unavailable frozen worker,
or missing/mismatched readback do not fall back to in-process preparation.
An unconfirmed worker cleanup closes the service to further work.

The memory limit measures committed virtual memory, not resident RAM. The CPU
limit measures user time across process threads; it is checked periodically and
can overshoot. It is not a precise ten-second wall timer. The parent deadline
provides a separate elapsed-time bound, with bounded cleanup after expiry.
See Microsoft's [job limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)
and [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

## Results and cancellation

Timeouts, abnormal exits, oversized or malformed responses, and quota setup
failures return fixed messages without submitted text or native stderr. The
current drawing remains unchanged. Batch preparation releases the manager lock
while the worker runs. Adoption requires the same current session, item,
reservation and pending state, with no cancellation or close. Late results
cannot revive a cancelled or replaced batch.

Graph identity hashes retain their existing canonical encoding. Transport uses
UTF-8 so non-ASCII metadata does not expand into repeated ASCII escapes.
Project data is retained rather than silently stripped. Byte, depth and total
value limits can still reject exceptionally large metadata; no truncation or
silent chemical simplification occurs.

## Scope and verification limits

Worker interpreter/bootloader startup precedes installation of the worker's job
limits. The supervisor still owns and times the process. Other routes, including
project opening, editing/rendering, cleanup, PNG recognition and export, have
their existing controls and are not newly covered by this preparation job.
This is resource containment, not an operating-system security sandbox or proof
against arbitrary native code execution. It does not restrict filesystem or
network access by itself, and is not antivirus clearance.

Quota tests use owned synthetic workers to check memory denial, CPU termination,
child-process rejection and survival of host/control processes. Calibration uses
representative small stereo/isotope/charge structures, 512-atom connected and
disconnected inputs, and a 448-atom branched/ring example. Such tests cannot prove
acceptance for every topology, machine, dependency version or cold start. Test
receipts bind outcomes to exact source and runtime hashes; later edits require
new relevant checks. A source pass or worker-only frozen pass does not establish
full installer, installed-application, or clean-machine acceptance.

These local operations require no online service or AdGuard exception. Keep
normal protection enabled. Optional user-authorized online lookups require
separate provider-specific testing; these import checks make no claim about
their network reachability.
