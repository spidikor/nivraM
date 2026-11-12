# Naming acceptance diagnostics

The native acceptance report separates two public-ethanol checks:

- `naming_ui_verified`: the naming operation finished with the exact visible name
  `ethanol` and nonempty response details. This can include `generated_unverified`.
- `naming_roundtrip_verified`: the UI check passed and the details begin with the
  exact rendered status `round trip matched`, followed by the UI's ` · ` separator.
  The actual backend status is `round_trip_matched`; `analysis.mjs` replaces its
  underscores with spaces. `generated_roundtrip_matched` is not the current status.

Both flags start false. Observing an optional unavailable/unsupported engine can
remain part of a general UI check, with both flags false. A bundled naming release
gate must require `naming_roundtrip_verified == true`. Neither flag establishes a
Preferred IUPAC Name, success on other molecules, or exact Marvin parity.

The observer accepts the actual `Calculating locally…` pending label and its
legacy ASCII spelling. It waits for Cancel to hide, captures bounded terminal
result/details text even when details are empty, and fails a terminal error.
Reports omit fields containing detected filesystem paths, URLs, or credential
assignments. A real poll expiry retains the last observed state. The acceptance
deadline remains 35 seconds; the application deadlines were not changed.

## Static deadline finding

`make_server` starts `discover_naming_runtime` once in a background thread. Full
runtime inventory/hash verification is not repeated on each naming request.
However, `/api/name` can wait up to five seconds for that thread before calling
`name_structure` with its separate default 20-second budget. The frontend starts
its 25-second timeout before sending the request. Graph/identity preparation
precedes the naming timer; the OPSIN JAR hash precedes its inner subprocess timer;
worker cleanup can add up to one second; response handling also takes time.

These boundaries provide no guaranteed margin at the UI deadline. They are a
possible latency contribution, not a demonstrated explanation of the retained
installed acceptance failure. No timeout or runtime change was made on that basis.

## Bounded installed checks, 2026-09-20 UTC

The retained candidate was `0.2.0-dev.1-9795f7cdd29496d9`, built from source SHA-256
`06e58caf8c3094e6d6cfdde670d9dcd9d422f1f814d3425c6609b53c3499c935`.
All four checks used public ethanol, hidden owned child processes, bounded output,
the installed `_internal` working directory, and the original 20-second limit.

| Check | Observed output | Exit | Seconds |
| --- | --- | --- | ---: |
| Installed worker `--name`, input `{"smiles":"CCO"}` | NISPO 0.1.7 generated `ethanol` | 0 | 7.672 |
| Installed Java/OPSIN `-h` | OPSIN 2.9.0 help | 0 | 2.000 |
| Installed Java/OPSIN `-osmi`, input `ethanol` | `C(C)O` and the fixed stdin prompt | 0 | 0.954 |
| Same parser call with process-local DLL directory set to installed `_internal` | `C(C)O` and the fixed stdin prompt | 0 | 0.906 |

The last check tested one documented inherited process condition; it did not
reproduce failure. The diagnostic host restored its previous observed DLL
directory afterward. PyInstaller documents this child-process inheritance in its
[external-program guidance](https://pyinstaller.org/en/latest/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application).

All launched children exited normally, streams settled, and none required forced
termination. The worker executable, Java executable, OPSIN JAR, and runtime
manifest matched the existing build receipt before each run and retained their
hashes afterward. These checks do not establish full application-request success.
Other verification may have overlapped the initial run; durations are descriptive,
not cold-start or isolated-performance measurements.

Local receipts are retained outside the application/source deliverable tree under
the workspace's `work/installed-naming-diagnostics/` directory:

- `20260920T045541596441Z-worker-d474b3.json`
- `20260920T045602158951Z-opsin-help-96ebb1.json`
- `20260920T045621697342Z-opsin-ethanol-1c6e68.json`
- `20260920T045820729747Z-opsin-ethanol-fc70fe.json`

The earlier installed acceptance failure remains unresolved. Updated native
acceptance is needed to observe the exact terminal error and the separate naming
flags; these direct checks are not a substitute for that evidence.
