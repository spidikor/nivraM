# nivraM local prototype implementation plan

Goal: deliver runnable source, not another requirements-only response. The supplied
confirmed requirements remain authoritative. This first alpha is not certified
as visually or algorithmically equivalent to Marvin.

Architecture: a loopback-only Python standard-library server owns RDKit parsing,
chemistry checks, 2D coordinates, and SVG depiction. A dependency-free browser
client provides drawing tools, atom/bond selection, history, project files, and
native-resolution PNG rasterization. No external requests occur at runtime.

Dependency constraint: external package downloads failed in this build sandbox;
the proposed Ketcher/Electron foundation cannot be fetched or tested here. Use
the installed RDKit for a source-based alpha. Windows setup requires Python and
an initial dependency installation. Do not call this a portable Windows binary.

## Global constraints
- Label the actual button `2D Clean`; identify its RDKit backend and unverified
  Marvin equivalence visibly. Preserve the parity requirement as an open gate.
- All molecular data stays local. Bind only 127.0.0.1; require an API token and
  check Host and Origin; no arbitrary server filesystem reads/writes.
- Preserve atom identities, charges, isotopes and stereochemistry. Reject
  unsupported query, polymer, 3D and non-covalent structures explicitly.
- PNG: opaque white; integer total width at most 9999; p=ceil(W*0.02) per side;
  optional percent/pixel override; native vector rasterization, automatic height.
- Export must not invoke cleanup. Reject resource exhaustion instead of silently
  reducing resolution. No bundled fonts, user reference images, or private data.

## Tasks / independently testable deliverables
1. Chemistry model and layout (`nivram/chemistry.py`, `tests/test_chemistry.py`).
   Tests precede implementation: import ethanol; invalid SMILES/coordinates;
   stable isotope/charge/stereo round trips; no coordinate mutations on export;
   full and selected-atom clean with fixed unselected atoms; unsupported features.
2. SVG scene (`nivram/scene.py`, `tests/test_scene.py`). Use RDKit depiction of
   supplied coordinates without regenerating them; explicit reference-derived
   element palette, adjustable line/label sizes; no font files. Return atom draw
   coordinates and affine mapping for accurate editor hit testing.
3. Export geometry (`public/modules/export.mjs`, `tests/export.test.mjs`).
   Test 1000/4000/9999 widths, p rounding, invalid/empty content and large-height
   guards. Browser tests inspect decoded PNG pixels and actual white borders.
   Rasterize vector artwork at each requested resolution, never upscale a bitmap.
4. Local editor/server (`public`, `nivram/server.py`, `tests/test_server.py`).
   Real atom/bond/ring creation, selection, dragging, deletion, undo/redo, explicit
   import/save, clean and export. Test transaction rollback on chemistry failure,
   title/filename escaping, route traversal and cross-origin rejection.
5. Windows launchers, dependency pin, documentation and reproducible tests.
   Run browser interaction tests with all non-loopback requests denied. Inspect
   screenshots. Package source ZIP and publish measured verification results.

## Review focus
1. Charges/isotopes/stereochemistry must not disappear during visual edits/export.
2. Partial clean must leave all unselected coordinates byte-for-byte unchanged.
3. Glyph bearings/stroke extent/antialiasing must not clip or erase white margins.
4. A 9999-pixel width is not a 9999-pixel height cap; excessive allocations fail.
5. A failed request must leave the previous document recoverable and editable.

Validation: `python -m unittest discover -s tests -v`, `node --test
 tests/export.test.mjs`, and `python scripts/browser_test.py`. Windows launch and
pixel/cleanup equivalence to Marvin require separate evidence, not Linux tests.
