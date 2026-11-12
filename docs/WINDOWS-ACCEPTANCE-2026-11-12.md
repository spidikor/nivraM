# nivraM Windows acceptance plan: November 12, 2026, Mountain

Status: prepared, not executed. This is a development-candidate acceptance plan,
not a release certificate or a confirmed usage reset. The user's requested target
is November 12 Mountain time. Actual Windows and browser work remains deferred
until that session. Reconfirm actual environment availability and the live task
scope then; do not infer a quota reset from the date or start a scheduled job from
this document.

## Start here

1. Recover the exact source checkpoint and matching receipt. Preserve the existing
   installation, projects, profiles, shortcuts, and all earlier failure reports.
2. Inspect prerequisites before spending time building. Use a fresh, short local
   source path and an isolated environment outside the source directory.
3. Qualify exact source behavior first, then the frozen executable, then an
   authorized isolated installer run. Each layer needs its own results.
4. Prioritize saving/data preservation and the new-feature journey below. A `full`
   harness pass does not cover every new feature.
5. Stop on a policy denial, wrong runtime, source drift, ambiguous write outcome,
   cleanup failure, or missing required input. Preserve evidence and resolve only
   that blocker. Do not disable Windows protections or silently weaken a check.

## Known setup traps and inputs

- The source convenience setup has now been repaired; see
  [Windows source setup](WINDOWS-SOURCE-SETUP.md). It accepts explicit new external
  environment and local-wheel paths and launches the current desktop source.
  Offline mocked checks do not qualify cmd parsing, real dependency availability,
  installation, WebView2/fonts or actual startup. Exercise those in this session
  before treating the convenience route as Windows-accepted. Existing environments
  are refused and preserved; no implicit legacy `.venv` migration occurs.
- The actual build enforces **CPython 3.12.14 Windows x64**, not merely Python 3.12.
  Use standard CPython, not a free-threaded or ARM64 substitute.
- Windows requires local **RDKit 2025.9.6+nivram.2**. This is an adaptation of
  pinned Anaconda artifacts, not the official PyPI wheel. Linux's supported
  2025.9.4 checks are useful but do not qualify that Windows runtime.
- Expected wheel: `rdkit-2025.9.6+nivram.2-cp312-cp312-win_amd64.whl`.
  SHA-256: `d88f7f54f920f07c7d0d1376dc16e9e867d1e0cc0545d746ff9d718aa9f9e403`.
  Verify the exact recipe, provenance, lock and all 194 native components through
  the existing signed-wheel/evidence controls. Never change the pin just to pass.
- Verify the existing trusted-system Arial, WebView2 and exact dependency lock.
  Do not bundle a substituted font. The naming runtime needs the pinned Temurin
  25.0.4.1+1 and OPSIN 2.9.0 inputs; the source archive has their manifest/notices,
  not the complete binary runtime. NISPO is pinned separately to 0.1.7.
- `scripts/browser_test.py` has an automatic in-memory alternate route after
  `ERR_BLOCKED_BY_ADMINISTRATOR`. **Do not run that script unchanged if the route
  can execute.** A reviewed fail-closed adjustment or permitted direct test path
  is needed before using it. If navigation is denied, stop that operation rather
  than use the fallback. No browser/security setting changes are authorized here.
- `check_installer.py --phase isolated` actually installs/registers, reinstalls
  and uninstalls a synthetic test instance. It refuses an existing nivraM
  registration. Do not bypass that refusal. A user installation is a separate
  consequential step, not a verification command.

See [signed RDKit](SIGNED-RDKIT-BUILD.md), [naming runtime](naming-runtime.md),
[native acceptance](native-acceptance.md) and [native save acceptance](native-save-acceptance.md).

## Session evidence and commands

These are preparation instructions, not executed commands. Substitute verified
paths and a fresh run identifier. The shell must stop when a command has a nonzero
exit code; do not paste the entire sequence and continue past a failed gate.

Choose either an existing verified isolated environment or the new-environment
route below. Existing environments are checked, not changed or repaired by setup.
Prepare missing inputs only within the then-authorized scope. Keep every new
environment outside `source`: the source inventory excludes named build/output
directories, not arbitrary virtual environments. These commands run from `source`.

For an **existing** isolated environment, set `$ENV_DIR` to its absolute directory
and `$PY` to its Python executable, then check it without installing anything:

```powershell
$ENV_DIR = 'C:\nivram-work\existing-source-env'
$PY = Join-Path $ENV_DIR 'Scripts\python.exe'
& $PY -B scripts/windows_source.py check --environment $ENV_DIR
& $PY -m pip check
```

For a **new** environment, set `$BASE_PY` to the separately verified standard
CPython 3.12.14 Windows x64 base interpreter. Check that interpreter first:

```powershell
$BASE_PY = 'C:\verified-python-3.12.14\python.exe'
& $BASE_PY -B scripts/check_environment.py
```

If the exact signed wheel is missing, prepare it separately using the existing
signed recipe. Use a fresh output destination; preserve existing caches and reject
mismatched cached bytes. This does not create the application environment or waive
license/signing requirements:

```powershell
& $BASE_PY scripts/signed_rdkit_wheel.py --cache ../signed-rdkit-cache --output ../signed-rdkit-wheel
```

Then use the repaired helper with the verified local wheel and a **fresh external
environment directory that does not exist**. Its parent must already exist. The
helper checks the complete lock before effects and installs its verified staged
copy; do not substitute a direct pip-install command for this acceptance route.
Only after successful setup, set `$PY` to the new environment for subsequent checks:

```powershell
$ENV_DIR = 'C:\nivram-work\source-env-new-run'
$WHEEL = 'C:\nivram-work\signed-rdkit-wheel\rdkit-2025.9.6+nivram.2-cp312-cp312-win_amd64.whl'
& $BASE_PY -B scripts/windows_source.py setup --environment $ENV_DIR --wheel $WHEEL
$PY = Join-Path $ENV_DIR 'Scripts\python.exe'
& $PY -B scripts/windows_source.py check --environment $ENV_DIR
& $PY -m pip check
```

Naming-runtime staging is a separate step after either environment route, only
if its validated inputs/runtime are missing:

```powershell
& $PY scripts/stage_naming_runtime.py --jre-zip <local-pinned-JRE.zip> --opsin-jar <local-pinned-OPSIN.jar> --destination ../naming-runtime/bundle
```

The naming destination's parent must already exist; the destination must not.
If preparation requests an unanticipated permission, legal agreement or unrecognized
executable source, handle the applicable approval rather than treating this plan
as permission. No money, public distribution, security-warning bypass, persistent
credentials, or installation into the user's existing app is included.

### A. Exact-source checks

Record OS/build, architecture, Python, RDKit distribution/module, WebView2, fonts,
Node, naming inputs, source manifest and every command/exit/result. Use fresh
report/profile names, e.g. a run identifier in place of `<run>` below. Capture
test totals with skips and failures, not just process exit status.

```powershell
& $PY -B -m unittest discover -s tests
$env:NIVRAM_TEST_PYTHON = $PY
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$PWD;$PWD\tests"
node --test tests/three-d-measurement-interface.test.mjs tests/three-d-png-save-status.test.mjs
& $PY scripts/check_native_acceptance.py --scenario full --report verification/source-full-<run>.json --profile verification/source-full-<run>-profile
& $PY scripts/check_native_acceptance.py --scenario save --report verification/source-save-<run>.json --profile verification/source-save-<run>-profile
& $PY scripts/check_native_acceptance.py --scenario drawing-png --report verification/source-drawing-png-<run>.json --profile verification/source-drawing-png-<run>-profile
& $PY scripts/check_native_acceptance.py --scenario batch-png --report verification/source-batch-png-<run>.json --profile verification/source-batch-png-<run>-profile
```

Also run affected fragment/SVG/inspector/comparison/metadata JavaScript regressions
against the exact source and inspect their results. Existing Linux pass receipts
are historical comparison evidence, not Windows results. The `save` scenario needs
a supported local fixed writable NTFS location. Unsupported paths must not fall
back to unprotected overwrite. The visible native harness must confirm owned
worker exit and runtime cleanup; a timeout or forced termination is not a pass.

### B. Focused real-UI journey

Use synthetic fixtures only. Record screenshots where useful and actual saved-file
hashes/readback. Snapshot the graph, document revision and history before and after
read-only/export operations. Repeat this journey on the frozen candidate; later
installed checks must not silently inherit source-only results.

| Area | Inputs and actions | Acceptance evidence |
|---|---|---|
| Save safety first | Cancel chooser; create and update a synthetic project; exercise existing controlled conflict/unconfirmed cases | Exact disk readback and prior-file backup; canceled/failed/unconfirmed outcomes remain distinct; newer edits stay unsaved |
| Reusable fragments | `N[C@@H](C)C(=O)O`, `F/C=C/F`, `F/C=C\F`, `[13CH3:17][C@H:18](O)F.[Na+]`; duplicate whole graph and complete disconnected components, then try a selection cut across a bond | Fresh IDs/reference remapping, translation-only placement, stereo/isotope/map preservation, no added connecting bond, one Undo; partial bonded selection rejects; shelf save survives restart without dirtying project; browser persistence stays unavailable |
| SVG artwork | Export representative fixtures at 1000 and 4000 pixels, both background modes and themes; include labels, wedge/hash and two-color bonds | Open the actual saved SVG and inspect clipping/margins/paint; visual-only output has no embedded chemistry/source identifiers/external resources; cancel is not reported as saved |
| Stereo inspector | `C[C@H](O)F` (R), `C[C@@H](O)F` (S), `F/C=C/F` (E), `F/C=C\F` (Z); `CC(O)F` and `FC=CF` unresolved; `CC=C=C(C)F` unsupported ordinary E/Z | Match exact site IDs to selected rows, preserve graph/history, leave unresolved sites unassigned, clear stale results after edits; compare engine-version differences instead of silently rewriting expectations |
| Pinned comparison | Pin ethanol `CCO`, compare with `CCCO`; also use invalid saved-project input and charged/mixture examples | Immutable pin, current-minus-pinned property deltas, independent fit, invalid pin input retains old pin, correct applicability warnings, inappropriate LogP differences suppressed; session pin clears at restart; no atom-by-atom highlighting claim |
| PNG metadata option | Fresh launch: main, profile-preview, native batch and 3D; export each with checkbox off/on | Exact label **Remove chemistry metadata?**, initially unchecked; unchecked native record can recover chemistry, checked complete native record is absent while Software label remains; equal decoded pixels for otherwise equal exports; no document/history mutation |
| Pending PNG work | Change metadata choice during preview encoding/3D generation; change source while work is pending; cancel and retry | No stale bytes exported; preview Save writes the exact shown preview bytes; cancellation and failure produce no success claim |
| JSON admission | Existing 64/65-container tests and original 4,000-depth cases on Windows Python | Root counts as one; quoted/escaped brackets do not count; over-limit JSON gets 400 before decoder/route dispatch; valid boundary still reaches ordinary validation |
| 3D geometry | Generate ethanol, rotate/zoom, show generated H, measure distance/angle/signed torsion with canonical and attached H atoms; export schema6 PNG and reopen | Recovered coordinate values match within documented display rounding; exact canonical MOL/H attachment retained; recovered/unverified-origin warning remains; hidden H/close clears selectors and source edits fence stale generated work |
| 3D PNG save status | Retained and removed metadata choices, canceled and completed chooser | Status says PNG **prepared** with the appropriate metadata notice and explicitly leaves saving unconfirmed; only independent saved-file inspection establishes disk bytes |
| PNG reconstruction to 3D | A supported synthetic PNG, review/correction then explicit acceptance, followed by **Continue to 3D** | Never autoaccept image recognition; shortcut uses the accepted current graph and the same supported generator; unrelated later edits invalidate stale requests |

Synthetic fixture assignments were rechecked on Linux RDKit 2025.09.4 during this
preparation. They are not experimental truth or fresh Windows assignments. The
3D generator remains restricted to its documented chemistry/size/stereo domain;
one generated conformer is not a force-field minimum or a physically validated
structure. Do not interpret the user's broad 3D goal as support for every molecule.

### C. Frozen build, then authorized installer checks

Build only after source is settled. Use the fresh workspace to avoid overwriting
an earlier bundle. Recheck source identity before and after all qualification.

```powershell
& $PY scripts/build_windows.py
& $PY scripts/check_packaged_startup.py
dist\nivraM\nivraM.exe --acceptance-report verification/frozen-full-<run>.json --profile verification/frozen-full-<run>-profile --scenario full
```

Repeat the `save`, `drawing-png`, `batch-png` scenarios and focused journey on that
exact frozen executable with fresh paths. Build receipts must bind the source,
bundle inventory and dependency/native provenance. Reduced-PATH startup is useful
but is not a clean-machine or installed check.

If installer compilation and operations are then authorized:

```powershell
& $PY scripts/build_installer.py --compiler <verified-ISCC.exe>
& $PY scripts/check_installer.py --phase isolated --verify-long-path-rejection
```

The isolated helper's built-in installed acceptance uses `full`; arrange the
additional feature/scenario checks on an authorized isolated installed candidate
before claiming installed coverage. Existing-registration refusal must be resolved
with the user, not bypassed. Final user installation (`--phase user-install`),
ordinary shortcut startup, cross-version upgrade/rollback and uninstall-data
preservation each need their own applicable scope and evidence. An unsigned
installer must not prompt an automatic security-warning bypass.

## Result template and stop conditions

For each layer, retain: source/bundle/installer hashes, runtime identities, command,
fresh report/profile ID, start/end time, counts, skips, failures, disk-readback
hashes, cleanup outcome, and unresolved next action. Report `not run`, `blocked`,
`failed`, `passed within scope`, and `unconfirmed write` distinctly.

Do not retry an uncertain save/install without checking its actual state. Stop
only the dependent lane when a prerequisite is missing. Preserve failed logs;
neither many unrelated passes nor later reruns erase them.

Remaining broader holds include trusted-font/visual acceptance, full recognition
accuracy and reference calibration, full calculation-vector verification,
condition-validated molecular population prediction, complete accessibility,
clean-machine/migration coverage, independent traffic qualification and public
redistribution/signing clearance. This plan does not declare those complete.

## Historical preparation snapshot (before convenience-setup repair)

- Current source was verified against all 602 files in the prior PNG-status
  checkpoint before copying. Production code and tests were unchanged in that
  earlier preparation snapshot; the current repair changes the setup entrypoints.
- 152 selected Linux Python tests ran: 147 passed, 4 Windows-only tests skipped,
  and 1 errored because the desktop `webview` package is absent. That dependency
  failure remains recorded; no package was installed or assertion weakened.
- Static wheel recipe/lock consistency and five pinned local browser assets
  matched. Eight synthetic stereochemistry fixtures passed with graph nonmutation.
- No browser, Windows, installer, native chooser, real PNG rendering, new package
  installation, public upload, or quota/reset check was performed.
- Detailed commands/results and independent review accompany this checkpoint.

## Current convenience-setup repair evidence

The newer source replaces stale interpreter/setup/browser-launch behavior with
explicit pinned desktop-source preparation and launch. Its initial review exposed
an incomplete dependency-lock admission check; the repaired version now binds the
complete delivered lock bytes before effects and installs a verified staged copy.
Extra packages/options and concurrent source-lock drift have dedicated negative
checks. Prior draft logs remain historical, not evidence for this correction. Focused offline tests use
mocked Windows properties and intercepted subprocess calls; no package was
installed and no browser/Windows/native operation ran. Their matching repair
receipt/logs are separate from the 152 historical preparation checks above.
The prior missing-webview failure and Windows/browser holds remain unresolved.
The browser denial fallback is unchanged and remains held as described above.
