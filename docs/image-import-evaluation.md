# PNG molecular metadata recovery evaluation

This report covers the local metadata backend, not optical recognition accuracy.
The evaluated runtime is Windows, Python 3.12.14, RDKit 2025.09.4. The original
private reference archive was read locally and remains outside the repository.
Private molecular contents and recovered candidate graphs are not included here.

## Implemented behavior

nivram.image_import.inspect_png_metadata(png_bytes) checks the PNG signature,
header, chunk sequence bounds and checksums before recovering bounded molecular
text. It does not decode raster pixels. It supports Marvin molSource XML and
text SMILES/MOL metadata, including RDKit text-key conventions. Binary molecular
pickle metadata is never deserialized.

The result includes image dimensions, original-byte SHA-256, the RDKit version,
candidate graphs, unsupported features, warnings, and an explicit
recognition.status = "not_run". Each candidate is a **disconnected component**,
not a verified compound. Several components can belong to one salt, mixture, or
intentionally grouped drawing; the software does not infer that grouping.

select_metadata_candidates(result, selected_ids) requires an explicit,
nonempty selection of supported components. It returns a new graph, retaining
their coordinates and chemistry, without changing the inspection result. It
does not neutralize, desalt, invent missing structures, or merge covalent graphs.
Selection is a partial reconstruction of a new editable document. The caller
must retain the original PNG/MRV and disclose omissions before acceptance.

MRV parsing preserves all selected chemical XML for RDKit rather than stripping
groups to force acceptance. Components intersecting polymer/S-group annotations
are blocked. Unknown chemical fields, reaction documents, query chemistry,
enhanced stereogroups, unsupported bonds and 3D coordinates are rejected or
surfaced as unsupported. Ordinary document properties and bracket annotations
remain in the original source, with explicit warnings. This is not a general,
lossless Marvin document importer.

The graph conversion checks canonical isomeric identity against the parsed
component. That catches some conversion losses. It does not independently prove
that RDKit interpreted every possible MRV feature correctly, nor that embedded
metadata agrees with the raster drawing. Candidate review remains required.

## Resource and XML boundaries

| Boundary | Limit or behavior |
| --- | --- |
| PNG input | 64 MiB |
| PNG chunks | 4,096 |
| Each text chunk | Preflight before copying; payload at most 1 MiB plus bounded header allowance |
| Each expanded metadata record | 1 MiB |
| All expanded text records | 4 MiB |
| XML nodes/depth | 50,000 / 64, checked during incremental parsing |
| MRV source atoms/bonds | 16,384 / 32,768, counted before RDKit allocation |
| Disconnected components | 1,024 |
| Accepted editor selection | 512 atoms / 1,024 bonds |
| Large-image preview threshold | More than 16 million pixels requires a separate crop/preview route |
| Pixel allocation in this module | None; IDAT checksum reads a memory view |

XML accepts explicit UTF-8, ASCII, Windows-1252 and ISO-8859-1 declarations.
It rejects NUL-containing/UTF-16 input, unsupported encodings, DTDs and entity
declarations. No metadata executes, requests external resources, or accesses
the network. Decompression rejects oversized, truncated and concatenated
streams. The separate raster preview/recognition route must enforce its own
decode and memory limits; metadata inspection alone does not validate decoded
pixel contents.

## Executed tests

From the repository, the focused command is:

    ..\venv\Scripts\python.exe -m unittest discover -s tests -p "test_image_import.py" -v

All 20 synthetic metadata tests passed. They cover plain PNG absence, atom
coordinates, tetrahedral and double-bond stereo, isotope/charge preservation,
salt-component grouping, nonmutation, duplicate/unknown selections, multiple
top-level molecular records, text MOL/SMILES, refused binary pickle data, bad
checksums, truncation, invalid dimensions, unknown critical chunks, large-image
preflight, bounded inflation, DTD/entities, UTF-16 rejection, Windows-1252
compatibility, XML depth/atom bounds, unknown chemical fields/namespaces, invalid
stereo, explicit unknown bond marks, unsupported document containers, reactions
and unsupported polymers. These
are metadata tests; they do not count as OCR tests.

The first real reference run exposed a Windows-1252 declaration that the initial
UTF-8-only guard rejected. The decoder was corrected to the bounded encoding
allowlist above, and a synthetic non-ASCII regression was added before rerunning
the reference check.

A second targeted check found that RDKit sanitization retained explicit unknown
stereo marks in original MolFile properties while returning no bond direction.
ReapplyMolBlockWedging restores the original marks before graph conversion.
The regression verifies both the retained unknown direction and its review
warning; it does not invent a defined stereoisomer.

## Private reference evaluation boundary

Private-input measurements, archive identifiers, recovered XML and candidate
graphs are omitted from this public source tree. Historical local metadata checks
do not establish image correspondence, recognition accuracy, independently
validated compounds or universal format compatibility. The synthetic tests and
implementation limits above remain separate from private evaluation evidence.

## Optical recognition feasibility boundary

The optical path is separate and has not been claimed by this metadata module.
[Official MolScribe documentation](https://github.com/thomas0809/MolScribe)
describes a CPU inference interface with graph/atom/bond outputs. Its model
repository labels the weights MIT, but has no substantive model card:
[official model repository](https://huggingface.co/yujieq/MolScribe).
These facts alone do not establish native Windows inference or packaged support.

The first isolated Windows dependency attempt found a release/source difference.
The PyPI 1.1.1 source distribution requires PyTorch >=1.11,<2.0 and the older
rdkit-pypi distribution. No PyTorch <2 wheel resolved for this Python 3.12
runtime. The exact
[official source revision 7296a30413eb55436702011efdff78131f66d162](https://github.com/thomas0809/MolScribe/commit/7296a30413eb55436702011efdff78131f66d162)
instead allows current PyTorch, uses rdkit, and requires NumPy <2. A separate
environment is required to evaluate that revision without changing the core
editor's NumPy 2.3.5 pin.

That exact source revision was installed in an isolated environment with
PyTorch 2.14.0+cpu, torchvision 0.29.0, NumPy 1.26.4, RDKit 2025.9.4,
OpenCV 4.11.0.86, OpenNMT-py 2.2.0, timm 0.4.12 and albumentations 1.1.0.
The complete observed dependency list and installation report were saved in the
evaluation workspace. pip check passed. MolScribe imported successfully with
network connections disabled; the measured cold import took 95.641 seconds.
Deprecation warnings occurred in the legacy OpenNMT/PyTorch API usage.

Inference could not be evaluated because checkpoint provisioning failed.
The frozen target was the official model revision
a0189776b7415b82795c7ee81eed311bf5c8724b, file
swin_base_char_aux_1m680k.pth, expected size 1,134,173,494 bytes and SHA-256
32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501.
The first HTTPS attempt failed with Windows DNS error 11004 on the redirected
storage route. A bounded retry encountered an incomplete HTTP response.
A final standard Windows curl check failed at the Schannel credential handle
with SEC_E_NO_CREDENTIALS. Certificate verification and host security settings
were not disabled. No verified checkpoint exists in the evaluation workspace.
These are observed provisioning failures, not evidence that the model itself
cannot work on Windows.

The resulting capability state is **source import verified; model provisioning,
inference, recognition accuracy and packaged execution unverified**. No optical
worker is included by the metadata module. The isolated environment is an
evaluation artifact, not a redistributable release. In addition to the project
MIT license, the vendored Indigo wrapper carries an Apache-2.0 notice; a future
distribution must audit and retain notices across its complete dependency set.

Before an optical path can be accepted, it still needs a hash-verified model
asset tied to the exact source revision, clean native inference, independent
synthetic/public ground truth,
stereo-aware accuracy and failure reporting, bounded crop processing, review
before commit, and packaged worker checks. No private reference images have
been sent to a model or online provider during this evaluation.
