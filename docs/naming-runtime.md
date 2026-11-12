# Reproduce the local naming runtime

The Windows build uses Eclipse Temurin **25.0.4.1+1**, Windows x64 HotSpot JRE,
and **OPSIN 2.9.0**. NISPO **0.1.7** is installed through the checked Python
dependency lock. The editor does not download these components at startup.
The source-owned staging script accepts local files and produces the exact
runtime inventory used by the evaluated naming adapter.

This is a local development/installation recipe. **Public redistribution
clearance remains unmet.** The runtime's GPL corresponding-source delivery
route has not been completed under the Goal's strict no-font distribution
constraint. Staging success does not establish release clearance.

## Inputs

Use the checked Windows x64 build environment: CPython 3.12.14 and
`requirements-windows-lock.txt`. Obtain the following files from their official
releases, then pass their local paths to the script. The script performs no
downloads, installations, account operations, or system Java discovery.

| Local input | Required bytes | SHA-256 |
| --- | ---: | --- |
| `OpenJDK25U-jre_x64_windows_hotspot_25.0.4.1_1.zip` | 58,475,080 | `4c95451cea98556def2c54f7782933f52a26d4a36bd85e1d59f0364464828b07` |
| `opsin-cli-2.9.0-jar-with-dependencies.jar` | 14,335,652 | `c2e29326c281f87b59a05d934d8589adac6e9d17b95b984931b3e739111b360f` |

Official sources:

- [Temurin release jdk-25.0.4.1+1](https://github.com/adoptium/temurin25-binaries/releases/tag/jdk-25.0.4.1%2B1)
- [Pinned Windows x64 JRE ZIP](https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.4.1%2B1/OpenJDK25U-jre_x64_windows_hotspot_25.0.4.1_1.zip)
- [Pinned OPSIN dependency JAR](https://github.com/dan2097/opsin/releases/download/2.9.0/opsin-cli-2.9.0-jar-with-dependencies.jar)
- [Temurin support policy](https://adoptium.net/support/)

The JRE hash was matched against the official release API asset digest and
vendor build metadata. The attempted SHA-256 sidecar download returned an empty
body and was rejected. No PGP or Authenticode verification is claimed.

The source package includes only the following small staging assets:

- `build-support/naming-runtime/manifest.json`, 50,088 bytes; SHA-256
  `4a0e4e2564d9209725d32581348528e2459edb0a21e5624ab8cbb1f28ab75532`.
- `build-support/naming-runtime/OPSIN-LICENSE.txt`, 1,056 bytes; SHA-256
  `aeeafe2053175a5b34cb2ded4410581ccd36b82c734315be1a7a81c6f9fb4834`.
  These are the unchanged [OPSIN MIT license bytes](https://raw.githubusercontent.com/dan2097/opsin/2.9.0/LICENSE.txt).

## Stage and build

Run the commands from the source repository, using its checked build environment.
Replace the two input paths with the locations of your downloaded files.

```powershell
New-Item -ItemType Directory -Force ..\naming-runtime
python scripts\stage_naming_runtime.py `
  --jre-zip 'C:\build-inputs\OpenJDK25U-jre_x64_windows_hotspot_25.0.4.1_1.zip' `
  --opsin-jar 'C:\build-inputs\opsin-cli-2.9.0-jar-with-dependencies.jar' `
  --destination '..\naming-runtime\bundle'
python scripts\build_windows.py
```

The destination's parent directory must exist. The destination itself must not
exist, including an existing empty directory or a redirected path. Existing
runtime installations are never overwritten. For another staging attempt,
choose a new destination. A failed extraction removes only the newly created
directory after a containment check; if safe cleanup cannot be confirmed, the
script reports that the incomplete directory was retained.

The default source build reads `../naming-runtime/bundle`. To build from another
successfully staged directory, explicitly set `NIVRAM_NAMING_BUNDLE` to its
absolute path before running `build_windows.py`. The spec copies the contents
into the frozen application's `_internal/naming-runtime` resource directory.
The source adapter's default remains the sibling `naming-runtime/bundle` path;
the environment override is a build input, not a runtime setting.

A successful staging receipt reports `status: available`, the Java/OPSIN
versions, the pinned manifest hash, **322 files**, and **202,179,202 bytes**
excluding the manifest. It deliberately reports
`publicRedistributionCleared: false`.

## Validation and preserved contents

Both inputs are checked against exact sizes and SHA-256 hashes before the
destination is created. The ZIP receives a bounded preflight for entry count,
expanded size, exact top-level directory, absolute/traversal paths, Windows
reserved names and path ambiguity, duplicate/case-conflicting names, links,
special files, encryption, and correspondence with the pinned inventory.
Extraction uses bounded streaming reads and exclusive file creation. CRC
validation is supplied by Python's ZIP reader; each extracted file also has
its own pinned size and SHA-256 checked.

The script copies the exact manifest, preserves every JRE file and license,
and then calls `discover_naming_runtime(destination)`. That independent helper
checks the complete inventory, required files, reparse points, hashes, and
platform before returning an `OpsinConfig`. No Java process is launched during
staging. Do not add notes or other files inside the staged directory: the
runtime helper rejects unexpected files.

All **320 original JRE files** remain unchanged. The staged layout is:

```text
bundle/
  manifest.json
  jre/
    bin/java.exe
    bin/server/jvm.dll
    lib/modules
    release
    NOTICE
    legal/...                  (all 183 legal files)
    ...                        (all remaining original JRE files)
  opsin.jar
  legal/OPSIN-LICENSE.txt
```

The evaluated pinned runtime contains zero `.ttf`, `.otf`, `.ttc`, `.otc`,
`.woff`, `.woff2`, or `.eot` resources in its regular-file inventory, embedded
JAR entries, and validated `lib/modules` resource-name inventory. The script
also rejects font-suffixed ZIP entries. No font was removed from this JRE.
This evidence applies to the exact pinned archives; it is not a blanket claim
about future Java/OPSIN versions.

## License and corresponding-source boundary

Temurin is provided under GPLv2 with the Classpath Exception, subject to its
included notices and third-party terms. Retain `jre/NOTICE`, all of `jre/legal/`,
and the OPSIN license. Key Java license paths are
`jre/legal/java.base/LICENSE`, `ASSEMBLY_EXCEPTION`, and
`ADDITIONAL_LICENSE_INFO`. See the [vendor licensing FAQ](https://adoptium.net/docs/faq/).

The vendor source archive below contains **29 font demo/test/Javadoc resources**.
Keep it outside every application and source deliverable archive. The separately
downloaded vendor build-script archive is also excluded from deliverables at
this stage. Neither archive is required as an input to the local staging script.

| Local evaluation evidence only; do not package | SHA-256 |
| --- | --- |
| `OpenJDK25U-jdk-sources_25.0.4.1_1.tar.gz` (120,577,922 bytes) | `cb9e50d2eb3de72ffd28466d7c511b4039b4bc4bd8f0d276d1e4cf81e30fdf09` |
| `temurin-build-e6ba7dec3d07654074559310376a3ae89da5f4ac.tar.gz` (584,568 bytes) | `b1340378b9ed62b32acfacd012dd069ec2b9d940fc39d3c73f32142d64e89713` |

[Official source archive](https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.4.1%2B1/OpenJDK25U-jdk-sources_25.0.4.1_1.tar.gz).
[Build scripts at the commit recorded in vendor metadata](https://codeload.github.com/adoptium/temurin-build/tar.gz/e6ba7dec3d07654074559310376a3ae89da5f4ac).
The build-script archive hash was recorded locally; it is not represented as
a vendor-published signature. The source repository is
[adoptium/jdk25u](https://github.com/adoptium/jdk25u), reference
`jdk-25.0.4.1+1_adopt`.

These links establish provenance. They do not substitute for a distributor's
corresponding-source obligations. No written source offer has been created, and
the downloaded evidence has not been established as a complete corresponding
source package for a proposed nivraM distribution. Keeping notices alone does
not complete that requirement. Public release remains blocked until a compliant
source-delivery route that satisfies the Goal's font constraint is established.

An OPSIN-specific runtime built with a pinned Temurin JDK and `jlink` is a
possible further engineering route. Its module closure, licenses, corresponding
source/build scripts, font inventory, and functionality would need their own
evaluation. It has not been implemented or cleared by this staging recipe.
The [jlink documentation](https://docs.oracle.com/en/java/javase/25/docs/specs/man/jlink.html)
describes the tool; no Oracle runtime is bundled or copied.

## Scientific acceptance remains separate

The current local evaluation covered thirteen public synthetic fixtures: eleven
full supported-graph matches, and correctly rejected isotope/radical mismatches.
All input graphs remained unchanged. This is a bounded fixture result, not a
universal naming-accuracy or Preferred IUPAC Name claim. Re-run native packaged
worker, cancellation, shutdown, and scientific checks after creating a new
application build; staging an intact dependency does not establish those results.
