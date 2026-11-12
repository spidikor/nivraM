# Optional local generated conformers

The existing optional 3D dialog now offers **Generate from current drawing** beside the independent supplied-coordinate file input. Accepted PNG review commits a graph to that same drawing, so a reviewed PNG-derived graph can use this action. This increment does not provide or validate the unavailable optical recognition runtime, and no PNG-to-3D end-to-end claim is made.

Generation copies the current graph to an exact UTF-8 JSON string, SHA-256 digest and document revision. The protected authenticated local endpoint delegates native chemistry to a disposable worker. Cancellation has bounded request-ID tombstones; a source revision change, closing the viewer or application close review invalidates pending/generated results. Neither generation nor view transforms mutate the 2D document or its history.

## Scientific scope

- One connected nonradical structure, elements H/B/C/N/O/F/Si/P/S/Cl/Br/I, at most 128 source atoms and 256 source bonds. Hydrogen addition is temporary for embedding, bounded to 256 atoms and 512 bonds.
- RDKit ETKDGv3, seed 61453, one thread, at most 100 embedding iterations; 15-second parent wall timeout plus bounded process cleanup. Worker CPU limit 10 seconds. Linux address-space ceiling 2 GiB; Windows existing fail-closed job-object memory ceiling 1 GiB. Resource ceilings are containment, not security sandboxing or a calibrated workload guarantee.
- No force-field minimization. Source atoms/IDs, isotope, charge, atom-map and specified stereo identity are retained. MOL V3000 readback verifies identity including stereochemistry; the displayed coordinates come from that exported readback.
- Potential unspecified atom/bond stereochemistry is explicitly rejected rather than silently selecting and claiming a stereoisomer. Disconnected structures and unsupported domains fail without replacing the current view or drawing.
- Generated MOL and provenance are explicit separate downloads. The provenance identifies method, engine version, seed, exact source binding, output digest and limitations. Source image correspondence is not independently verified.
- A generated conformer is not an experimental structure, receptor-bound pose, global energy minimum, MD topology, thermodynamic state or aqueous population prediction.

The separate supplied V2000 parser accepts bounded inline charge codes (excluding radical code 4), M CHG (−8 to +8), absolute M ISO (1–400), and M RAD codes 0–3 as unverified declarations; nonzero inline isotope mass differences, inline radical code4, stereo/query/maps and other properties reject. Supplied RAD codes are declarations, not electron counts or verified spin. This does not broaden generated chemistry: radical generation remains rejected. This declaration-only route preserves exact source bytes and does not perform native chemical validation. Live generated V3000 results bypass that restricted parser and retain generated provenance. A separately opened supported V3000 MOL is instead treated as unverified supplied input; see supplied-3d-viewer.md for the limited reopening subset.

## Verification and release limitations

Actual selected Linux runtime: RDKit 2025.9.4 / NumPy 2.3.5 / Pillow 12.3.0. Python tests exercise real embeddings, the protected worker entrypoint and handler route without binding a network socket. JS tests exercise actual Python output validation and projected scenes, deterministic controller cancellation and source-change fencing, plus supplied-reader regressions. Chromium was not retried after the prior socket restriction. Windows installed worker admission, installer/build and native UI/browser rendering remain unverified release gates. This is a separate candidate, not an installed or published release.

## r002 source-context preservation

Generated output now carries a separately SHA-256-bound safe projection of the current document's historical image-import context, when present. It preserves image source SHA, import kind, warnings, recognition method/model/input hash/uncalibrated confidence, reviewed crop and correction uncertainty. The projection excludes source filenames, storage locations, request IDs, raw graphs and unrelated document/private metadata. It is explicitly labeled historical review with unverified correspondence to the current graph. Existing review provenance must not be mistaken for evidence that subsequent edits still match the image. No context is invented when image-import context is absent (JSON null).

The worker independently validates an exact allowlisted schema, at most 49,152 UTF-8 bytes, at most 32 warnings of 1,024 characters each, bounded recognition/review strings, numeric confidence, bounded crop coordinates and hashes. Invalid allowed values fail; no warning is silently truncated. Graph hash, context hash and document revision are independent response bindings. The controller checks current graph and projected context at subscriptions and asynchronous completion, including context-only changes with the same revision.

r001 source/evidence originals are preserved separately. Independent r001 review found two deep-JSON HTTP-status regression tests also failing on the untouched baseline (404 versus expected 400); this increment does not change that unrelated behavior.

## Optional continuation after PNG review

A successful acknowledged PNG structure review can offer **Continue to 3D** in
the toolbar. Clicking it opens the optional viewer and invokes the same bounded
local generator as **Generate from current drawing**. Import alone never starts
generation. Review/acceptance remains mandatory; recognition is not autoaccepted.

The shortcut is bound to an exact successful import receipt (graph, source context,
and document revision). A later document change, close review, or page exit hides
it. Missing/stale receipts and an identical no-op re-import may omit the shortcut
without failing an otherwise successful import. The ordinary 3D button and
current-drawing generator remain available for any supported drawn or imported
graph. Existing chemistry limits, cancellation and late-result guards still apply.
The generator does not use pixels as geometry or a length standard.
