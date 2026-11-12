# Signed RDKit for the Windows candidate

This candidate uses CPython 3.12.14 x64 and a local `rdkit==2025.9.6+nivram.2` wheel. It adapts fixed Anaconda RDKit 2025.09.6 artifacts for nivraM. It is not an official PyPI wheel or a complete conda environment.

The Windows dependency lock requires this local version. Ordinary `pip install rdkit` does not provide it. Non-Windows requirements retain their previous RDKit pin; this change does not qualify other platforms or Python versions.

## Prepare the wheel

From this source directory, with the pinned Windows Python:

```powershell
python scripts/signed_rdkit_wheel.py --cache ../signed-rdkit-cache --output ../signed-rdkit-wheel
```

The output directory must not already exist. The cache may be reused, but any mismatched cached artifact is rejected. The recipe obtains ten fixed archives only from the URLs in `scripts/signed-rdkit-lock.json`, checks their exact sizes and SHA256 hashes, reads selected ordinary members without executing package hooks, and verifies all 194 selected DLL/PYD components with Windows Authenticode. Valid signatures must identify Anaconda, Inc.

The wheel contains 11 upstream license notices, a complete SHA256 RECORD, the original archive identities and metadata, and an honest local version. Native binary bytes are unchanged. The only RDKit Python adaptation is a bootstrap that checks the pinned DLL manifest and registers its directory before importing RDKit's native modules. Font files and bytecode caches are excluded.

The expected wheel is `rdkit-2025.9.6+nivram.2-cp312-cp312-win_amd64.whl`, SHA256 `d88f7f54f920f07c7d0d1376dc16e9e867d1e0cc0545d746ff9d718aa9f9e403`. `scripts/signed-rdkit-wheel-pin.json` also pins its provenance and recipe. Reproducing different bytes is a review failure; do not silently change the pin.

## Install into an isolated build environment

Use a new environment with the supported Python. After producing and verifying the wheel, install the exact build lock while supplying its local directory:

```powershell
python -m pip install --find-links ../signed-rdkit-wheel -r requirements-windows-lock.txt
python -m pip check
python scripts/build_windows.py
```

The other dependency versions remain those in the existing Windows lock. A complete environment still needs those packages and the existing validated naming runtime; the RDKit wheel alone is not the entire application. These commands do not install nivraM for the user or sign its executables.

Before and after packaging, the build verifies installed RDKit metadata, its pinned provenance, every recorded package file, and RECORD hashes. PyInstaller must copy every DLL/PYD, the native manifest and package metadata. The evidence step rejects any changed or missing bundled native component, manifest, or upstream license file. It also rejects copied local installation-path metadata. The package records retain the wheel filename/hash and upstream artifact identities without disclosing its local build-machine location. Provenance identifies the adaptation and its ten upstream archives separately instead of labelling it as a PyPI distribution.

## Scope and remaining gates

The RDKit binaries retain their publisher signatures; nivraM's generated executables have separate signing and Windows acceptance requirements. Do not disable Windows policy or modify a trust store to pass these checks.

This Anaconda build does not provide MolDraw2DCairo. nivraM uses RDKit SVG scenes and browser PNG rendering; test that actual path when qualifying a new source/runtime or bundle. Optional RDKit integrations and complete conda dependency equivalence are not claimed.

Copying notices is not public redistribution clearance. The original conda dependency declarations and license strings remain in provenance, including dependencies whose obligations need separate review. The full release's native/transitive licenses, naming-runtime obligations, installer and installed acceptance remain open.

Format references: [Python wheel specification](https://packaging.python.org/en/latest/specifications/binary-distribution-format/) and [direct URL provenance](https://packaging.python.org/en/latest/specifications/direct-url-data-structure/). Upstream: [Anaconda RDKit](https://anaconda.org/main/rdkit).
