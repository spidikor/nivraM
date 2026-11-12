# PNG metadata increment (partial coordinate-output goal)

Every existing user PNG export path now adds `Software: nivraM <runtime version>` from `/api/status` (server `nivram.__version__`). Export fails if that identity is unavailable. A loaded project's stored version is not substituted. Main drawing, batch drawings and profile previews also contain uncompressed `iTXt` named `nivraM Coordinates`; population/reference chart PNGs contain Software only and no invented molecular coordinates.

## Recoverable 2D coordinates

Schema `nivram-png-coordinates/1` records the exact JSON-number drawing x/y and chemical graph declarations, IDs, dimension 2, origin `drawing-2d`, units `drawing`, producer, runtime version, and SHA-256 of the exact UTF-8 graph JSON string. It does not store cropped pixels, projected viewer coordinates or inferred 3D. Private analysis, source images, paths, filenames and unrelated viewer data are excluded. Anyone receiving the PNG can recover the embedded graph; the export panel discloses this.

Embedding preserves existing PNG chunks and compressed image bytes without raster re-encoding, adding CRC-valid chunks before IEND. Existing Software/native-coordinate keywords are rejected rather than overwritten ambiguously. Native metadata is uncompressed, limited to 1 MiB, with graph JSON capped at 512 KiB and existing 512-atom/1024-bond limits. Output is bounded to 32 MiB, or the existing stricter 8 MiB chart limit.

The writer requires the exact validated snapshot JSON serialization (no duplicate keys, alternate whitespace/number spellings or silent normalization), a nonempty graph, and valid Unicode strings including paired supplementary characters. Import checks schema, bounds and graph hash and retains original drawing coordinates; it does not generate a fresh layout. Existing legacy MolSource/MOL/SMILES handling and source warnings remain. Imported producer/origin fields are claims. Checksums do not authenticate authorship, validate experimental geometry, verify recognition, or prove the metadata describes the visible pixels. This contract does not bind a graph to pixel data; a copied or deliberately rewritten payload can be self-consistent. User review remains required.

## Explicitly not completed: 3D PNG/reopen

The initial 2D-only increment described here has been extended by explicit 3D PNG records and viewer export/reopen. See [3D PNG coordinates](../THREE_D_PNG.md). Supported supplied CFG/VAL uses schema4 and always revalidates exact embedded MOL in the bounded native worker before viewer adoption; historical receipts are not current trust. Browser/Windows qualification and broader PNG-to-3D capabilities remain open.

Other actual output capabilities: `.nivram` already preserves 2D graph coordinates; 2D MOL carries serialized coordinates with MOL precision; supplied 3D MOL retains original accepted bytes; generated 3D MOL retains its V3000 artifact/readback coordinates. Existing provenance JSON remains separate. Chart JSON/CSV describe populations/references, not invented molecular geometry. No new SVG/PDF/SDF/MRV/XYZ output or universal format/Marvin compatibility is claimed.

## Acceptance boundaries

Pure JS encoder tests cover runtime identity, exact graph bytes, unchanged IDAT, preserved existing text, malformed PNG/CRC, duplicate reserved keys, oversized metadata and rejection of extra/private graph fields. Python tests cover independent recovery, strict schema/hash/resource rejection and native PNG save validators. These do not establish actual browser downloads or Windows installed behavior. Browser/Windows/native save/optical gates remain open; no installation, network setup or publication is part of this increment. Historical private PNG checks do not establish support for arbitrarily large editable graphs or prove 3D recovery.

Private fixture names, molecular-size counts and recovered candidates are omitted from this public source tree. Legacy metadata inspection remains a bounded, potentially partial reconstruction; it is not a lossless-conversion or 3D claim.

Direct callers of the internal `exportPng` API must now pass `{graph}` alongside optional signal; production callers have been migrated. Known direct-call browser test helpers have been migrated, including explicit synthetic graph/runtime fixtures for isolated raster stress tests and the isolated chart status/module routes. Actual browser execution remains pending beyond the known unavailable-browser launch failure.
