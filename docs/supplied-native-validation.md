# Supplied V3000 native validation: backend increment

The separate supplied viewer uses this authenticated local backend for supported
V3000 CFG/VAL inputs. Adoption occurs only after a fresh response passes exact
source/declaration/coordinate/job/deadline checks. Unsupported or contradictory
inputs retain the previous view. Plain V2000 and CHG/MASS/map-only V3000 retain
their declaration-only behavior; existing generated recovery and 2D editing are
unchanged. This is controlled-DOM and actual HTTP/worker integration, not real
browser or Windows acceptance.

POST `/api/supplied-validation` accepts exactly `molText`, `molSha256`, `clientId`,
`jobId`, `expiresAt`. IDs are 32 lowercase hexadecimal characters; SHA256 binds
exact ASCII source bytes. Deadline is an integer Unix millisecond timestamp up
to 60 seconds ahead. No historical provenance, claimed identity, source atom IDs,
or generation authorization is accepted. Cancellation uses the same path plus
`/cancel` and exactly the three ID/deadline fields. Both routes require the
existing local session token/origin protections. Duplicate JSON fields reject.

The existing strict generated-recovery V3000 grammar is reused before native
parsing. Supported inputs are explicit-3D bounded ASCII V3000 with 1–128 atoms,
up to 256 bonds, supported organic elements, bounded CHG/MASS/map/CFG/VAL, no
query/radical/enhanced stereo/additional sections. This is narrower than CTfile.
The native worker requires the tested RDKit 2025.09.4 version, a connected
sanitized molecule, and no unspecified potential stereochemistry.

Native parsing is disposable and parse-only: no embedding, optimization, 2D
mutation or coordinate replacement. RDKit's output is used only as a consistency
check. Exact atom declarations and bond declarations must survive reserialization;
parsed coordinates, map, charge and isotope must equal the supplied values.
Contradictory CFG and redundant/default VAL normalization are rejected, not fixed.
Bond CFG that RDKit normalizes away is rejected even if another tool accepts it.
This conservative roundtrip is not full CTfile semantics or an independent
chemical validation algorithm. E/Z interpreted from planar 3D-marked coordinates
is an engine-derived interpretation, not a claim of explicitly declared stereo.

Response contains exact input hash, client/job IDs, scene with original source
text and source-declared coordinates/bonds/properties, and a separately labeled
engine-derived isomeric SMILES. It expressly does not verify generation claims,
experimental origin or binding to a 2D document. The parent validates the entire
result schema, source, coordinates, declarations, labels and job/deadline binding
before accepting it. Checksums establish consistency, not source authenticity.

The existing WorkerSupervisor controls concurrency/cancellation/process lifetime.
The worker has a <=15 second wall-time bound, 10 second CPU limit, bounded
transport/output, and existing fail-closed Windows resource controls (Linux
address-space ceiling 2 GiB; Windows existing quota module). These are process
resource bounds, not a sandbox proof against arbitrary native-code exploitation.
Windows packaging/runtime and browser acceptance remain unqualified. Richer
formats and ambiguous/enhanced stereo remain unsupported. No new persistent service is introduced.

Format references (earlier excerpt evidence, not a new full-document audit):
- BIOVIA CTfile Formats 2026: https://www.3ds.com/assets/invest/2026-01/biovia_chemistry_2026_ctfile_formats.pdf
- RDKit Book, interpretation of 2D/3D flag and stereochemistry: https://www.rdkit.org/docs/RDKit_Book.html

`tests/test_supplied_validation.py` probes actual RDKit own-generated chiral,
E/Z, isotope/map, VAL=-1 and VAL=3 inputs; atom-CFG flip/reflection, bond CFG,
VAL normalization/conflict, unspecified planar tetrahedral stereo, rejected rich
features, exact source/result binding, malformed responses, real HTTP auth and
worker execution, cancellation and stale/expired results. These checks are not
experimental stereochemistry or browser acceptance evidence.

## Viewer and PNG record version 4

Direct supplied inputs with CFG/VAL are preflighted without chemistry inference,
then sent to the disposable worker. The viewer displays exact declared CFG/VAL
values separately from engine-derived interpretation. New supplied provenance
`nivram-supplied-3d-view/2` contains a bounded parse-consistency receipt, retaining
`generated:false`, `chemistryVerified:false` and no experimental origin claim.

PNG schema `nivram-png-coordinates/4` preserves exact MOL bytes and that historical
receipt. Structural PNG validation does not authenticate the receipt. Every
schema4 reopen performs a fresh native parse of the exact embedded MOL before
adoption, even when the stored hashes/identity/receipt appear valid. The fresh
result is shown separately from historical provenance. Re-export retains the
historical record and exact MOL; it does not promote the old receipt to current
authenticity. Older schema2/3 readers fail closed on schema4. Legacy records keep
their original schema and acceptance behavior.

Cancellation, supersession, failed replacement, close, destroy, timeout and late
responses cannot replace the last accepted scene. Export during a pending load
uses only the existing accepted scene/provenance, never a partial pending result.
No 2D graph mutation, generation or optimization occurs in supplied validation.
