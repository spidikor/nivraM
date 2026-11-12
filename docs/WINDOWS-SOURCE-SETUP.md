# Windows desktop source convenience setup

This repairs the old browser-alpha entrypoints for the current desktop **source**
candidate. It is not an application installer or a build/Windows acceptance receipt.
Actual Windows setup and native startup of this repair remain untested. No existing
installation, shortcut, project, profile or legacy `.venv` is migrated or removed.

## Prerequisites and explicit invocation

Use standard **CPython 3.12.14 Windows x64**, on Windows 11 build 22000 or newer.
ARM64, 32-bit, free-threaded Python and different patch releases are rejected.
Install the exact interpreter through the ordinary approved route if missing.
The Python launcher `py -3.12` is tried first, then `python` on PATH; both are checked.
A verified exact interpreter can instead call `scripts/windows_source.py` directly.
Microsoft Edge WebView2 Runtime and trusted-system regular Arial are prerequisites.
Optional naming/recognition runtime preparation remains separately documented.

Prepare the local wheel using [the existing signed recipe](SIGNED-RDKIT-BUILD.md).
Setup does not build or download that wheel and does not bypass Authenticode checks.
Use a trusted local workspace, not a concurrently writable/shared location. Keep
paths short. From a command prompt in the source directory, for example:

```bat
SETUP-WINDOWS.cmd --environment "C:\nivram-work\source-env-1" --wheel "C:\nivram-work\signed-rdkit-wheel\rdkit-2025.9.6+nivram.2-cp312-cp312-win_amd64.whl"
START-NIVRAM.cmd --environment "C:\nivram-work\source-env-1"
```

The environment parent must exist; the environment itself **must not exist**.
Choose a new folder outside source, neither inside nor an ancestor of source.
No arguments prints command-line usage rather than selecting or overwriting an
implicit environment. Do not run the start command if setup returned nonzero.

Setup verifies exact wheel filename/hash, recipe, upstream lock and the complete
Windows dependency-lock SHA-256 before reserving that new directory. The lock is
bound to its exact delivered bytes (including mixed line endings), SHA-256
`095ca209d4bc98eb266fd999ce8aa181e1a39bb56654ba2176003b51f4f16a91`.
Added packages/options, changed pins and newline conversion are rejected; do not
update this digest merely to pass. It creates an isolated venv, stages and rechecks
both the wheel and lock, then installs that explicit local wheel plus the exact
staged **requirements-windows-lock.txt**, using
binary-only packages from PyPI with pip configuration isolation. Other dependencies
may require network access. The existing lock pins versions, not every PyPI artifact
hash; this is not a new reproducible or fully audited package supply-chain claim.
No global Python package install, source build hooks, security-setting changes,
WebView2 installation, browser download, naming-runtime staging or application build
is performed by this helper.

After pip check, the environment's interpreter rechecks its identity, the exact
installed package set, signed RDKit provenance/RECORD/native-file inventory and
recipe identity through the existing evidence controls. Only then is a local
readiness receipt written. Launch requires that receipt for this source location,
repeats installed-environment checks, and invokes **run_desktop.py**, not the old
browser-only `run_nivram.py`. The receipt is a setup completion aid, not a security
credential, source snapshot signature, or certification of native behavior.

Failures stop immediately and return a nonzero code. Existing folders are never
reused or cleared. A partially created environment and staged wheel remain for
inspection; choose a different new folder for a retry rather than deleting data
blindly. The prepared wheel and source inputs must not be changed concurrently. Hashing
streams bounded blocks and rejects observed file changes; this is not atomic
filesystem isolation against another process replacing paths.
Offline mocked tests check control flow, not Windows cmd parsing, real pip artifact
availability, actual installation, font/WebView2 availability or native UI behavior.
The [November 12 acceptance plan](WINDOWS-ACCEPTANCE-2026-11-12.md) retains these gates.
