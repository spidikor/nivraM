# Third-party notices

nivraM source is distributed under the accompanying MIT LICENSE.txt. Desktop
builds bundle third-party software under its own terms; the nivraM license does
not replace those terms. This project contains no Chemaxon implementation or
vendor license key and makes no claim of Marvin JS cleanup equivalence.

The tested build environment is pinned in requirements-windows-lock.txt. The
Windows build copies the installed distributions' license/notice files into
licenses/, records their original metadata in dependency-provenance.json, and
emits a CycloneDX build-environment inventory. That inventory includes build and
test tools and is not by itself a complete runtime/transitive-license review.

Principal components declared for the current source and Windows build include:

| Component | Version | Declared license / upstream |
| --- | --- | --- |
| CPython | 3.12.14 | PSF and bundled notices; https://www.python.org/psf/license/ |
| RDKit | Non-Windows: 2025.9.4; Windows: custom 2025.9.6+nivram.2 | BSD-3-Clause and bundled notices; https://github.com/rdkit/rdkit |
| NumPy | 2.3.5 | BSD and bundled numerical-library notices; https://numpy.org/doc/stable/license.html |
| Pillow | 12.3.0 | MIT-CMU; https://github.com/python-pillow/Pillow |
| pywebview | 6.2.1 | BSD-3-Clause; https://github.com/r0x0r/pywebview |
| pythonnet | 3.1.0 | MIT; https://github.com/pythonnet/pythonnet |
| clr_loader | 0.3.1 | Installed LICENSE file; https://github.com/pythonnet/clr-loader |
| NISPO | 0.1.7 | BSD-3-Clause and THIRD_PARTY_NOTICES.md; https://github.com/oxpig/nispo |
| CFFI | 2.1.1 | MIT-0; https://cffi.readthedocs.io/ |
| PyInstaller bootloader | 6.22.3 | GPL with distribution exception; copied COPYING.txt governs |
| Eclipse Temurin JRE | 25.0.4.1+1 | GPLv2 with Classpath Exception and module-specific notices; all staged JRE legal files retained |
| OPSIN | 2.9.0 | MIT plus dependency notices in its JAR; pinned upstream LICENSE retained |
| Chart.js | 4.5.1 | MIT; https://github.com/chartjs/Chart.js/releases/tag/v4.5.1 |
| @kurkle/color, embedded in Chart.js | 0.3.2 | MIT; https://github.com/kurkle/color |

The local browser asset public/vendor/chart.umd.min.js is the exact Chart.js
4.5.1 UMD distribution, including @kurkle/color 0.3.2. The embedded version is
identified by the UMD banner and byte-identical source-map/package source; it
is not inferred by resolving the package's broader dependency range. Its two
original MIT notices remain beside the asset as Chart.js-LICENSE.md and
kurkle-color-LICENSE.md. They retain the original copyright and permission text;
the upstream copyright banners in the JavaScript are also unchanged.

The Windows evidence step requires the reviewed asset, both notices, and both
staging receipts to match their pins in the source and the actual application
bundle. It additionally copies these notices and receipts to licenses/browser/,
records two browser components and their embedding relationship in the CycloneDX
inventory, and writes explicit browserDependencies provenance. Missing or changed
browser inputs fail evidence generation. A build environment package entry alone
still does not establish that a Python package is included in the runtime.
These browser checks establish artifact identity and notice inclusion; they do
not establish complete transitive licensing clearance or a security audit.

The pywebview distribution includes Microsoft WebView2 SDK assemblies/loader;
the matching Microsoft terms and native dependency notices must accompany a
redistributed release. The installed Microsoft Edge WebView2 Runtime is a
separate prerequisite. This candidate does not bundle or automatically download
that runtime. NuGet package reference:
https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49

Playwright and its browser installation are development-test tools. No browser
test environment is intentionally included in the application bundle. Inno
Setup is the installer build tool, under its own distribution terms.

The app-local Temurin JRE and OPSIN verifier are separately staged build inputs.
This source repository includes their pinned manifest and licensing/provenance
material, but no JRE or OPSIN runtime binaries. Public binary redistribution clearance remains an
unmet release gate: the complete vendor source archive contains font resources,
which conflicts with this Goal's strict no-font distribution requirement. No
written corresponding-source offer has been created, and upstream URLs alone
are not claimed to satisfy a distributor's source obligations. See
docs/naming-runtime.md for local staging and the exact boundary.

The source includes optional optical setup and worker interfaces. MolScribe/ML
runtime binaries, model weights and evaluation environments are not included.
Historical local model checks do not establish broader recognition accuracy or
current packaged/native acceptance; those qualifications remain incomplete.

No font files, user reference archives, private skill archives, recovered private
molecular documents, user projects, or credentials are redistribution inputs.
Synthetic examples and public QA fixtures are separate from private references.
