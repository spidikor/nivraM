# nivraM 0.2.0-dev.1

This repository contains development source, tests, documentation, and synthetic examples. It does not include installers, the custom Windows RDKit wheel, Java/OPSIN runtime binaries, OCR model weights, or private reference/evaluation archives. Historical delivery and test reports describe earlier local builds, not qualification of this repository snapshot. Binary redistribution and native release acceptance remain separate, incomplete work. See [public source scope](docs/PUBLIC-SOURCE.md).

A local Windows small-molecule editor with coordinate-preserving drawing and PNG export. This is a development candidate. Exact Marvin JS cleanup equivalence and the full scientific Goal are not yet satisfied.

## Run the source on Windows

The Windows development target is Windows 11 (build 22000 or newer); historical native checks were performed on Windows 11 build 26200. This candidate requires 64-bit CPython 3.12.14, the locally prepared signed RDKit 2025.09.6 adaptation (`2025.9.6+nivram.2`), and the versions in requirements-windows-lock.txt. Follow [signed RDKit preparation](docs/SIGNED-RDKIT-BUILD.md) before installing the lock. The desktop shell uses pywebview/WebView2. Microsoft Edge WebView2 Runtime is an explicit prerequisite.

The repaired SETUP-WINDOWS.cmd / START-NIVRAM.cmd entrypoints now prepare and launch the current desktop source, using explicit external-environment and local-wheel paths. See [Windows source setup](docs/WINDOWS-SOURCE-SETUP.md) for prerequisites, exact commands and failure handling. They refuse existing environments, enforce the pinned runtime/lock/signed-wheel evidence, and do not install the application or certify a desktop build. This repair has offline mocked test evidence only; actual Windows setup and native startup remain pending. The old browser-only launcher is not their target.

Core drawing, project saving, local properties, cleanup and PNG export need no online provider. Optional PubChem lookup requires a separate review and explicit consent for each submitted structure; there is no automatic provider request or telemetry. Its loopback service uses a session token, Host/Origin checks and a content-security policy. The native window additionally intercepts content requests. Those checks do not assert that Windows or the separately installed browser runtime makes no independent background connections.

## Drawing and projects

Use Paste structure for SMILES or 2D MOL, or the built-in examples. Atom, bond and ring tools, selection, dragging, explicit isotope/charge editing, undo and redo are available. V selects, A draws atoms, B draws bonds, R draws rings, E erases, Delete removes a selection, Alt-drag pans, and scrolling zooms.

The desktop Open/Save/Save As actions use native project dialogs. A project contains its editable graph, coordinates, drawing appearance, export settings and supported analysis provenance. New or imported documents require a new save destination. New destinations use exclusive publication so a competing file is preserved.

Existing-file updates now acquire a Windows transactional writer before checking the expected content and retain the preceding bytes in a verified .bak copy before committing. The protected update supports ordinary, resident, single-link files on local fixed NTFS drives with transaction support and ordinary non-reparse parent directories. Unsupported locations require Save As to a new file; there is no ordinary-overwrite fallback. If a conflict occurs, reopen the newer file or save a separate copy. If commit, cleanup, or final readback cannot be confirmed, the drawing stays open and no new saved state is acknowledged; the next save requires Save As. This development source still needs broader native interruption, storage-failure and compatibility acceptance. It has not yet replaced the installed development build.

The Windows update adapter uses Transactional NTFS. Microsoft recommends alternatives and warns that this facility may be unavailable in future Windows versions; that platform limitation remains part of the supported-domain and release review. See [Microsoft's guidance](https://learn.microsoft.com/en-us/windows/win32/fileio/deprecation-of-txf).

Settled dirty documents receive a recovery copy after a short debounce. Recovery is separate from the chosen project and opens as unsaved. Recovery is bounded to ten records and verified before a successful receipt. A graceful close freezes mutations and attempts recovery before asking about unsaved work. Owned-process forced-crash and simulated disk/permission failure checks pass. Complete installed-application recovery acceptance remains a separate gate.

The supported editor limit is 512 atoms and 1024 bonds. Text imports check structural counts before native parsing as well as checking the resulting graph. The SMILES input limit counts every explicit atom token, including hydrogen atoms. Ordinary SMILES import now retains supplied hydrogen atoms and their supported annotations, matching MOL import: `[H]O` has two graph atoms, while `O` has one. Bracket hydrogen counts such as `[NH4+]` remain counts on their atom, and implicit hydrogens are not expanded. This changes graph/layout and identity-hash representations for inputs whose hydrogens were previously removed; it does not rewrite saved drawings or change naming normalization. See [SMILES hydrogen fidelity](docs/smiles-hydrogen-fidelity.md). Excessively nested branches and inconsistent V3000 counts are rejected. Text import and batch preparation now run their complete parse, validation and scene rendering in a separate Windows worker, with verified process resource limits and a parent wall deadline. Other chemistry operations remain outside this boundary. See [import resource containment](docs/import-containment.md) for the limits and remaining verification boundaries. Reactions, query/R-group atoms, S-groups, 3D input and enhanced AND/OR stereogroups are not silently flattened. Explicit unknown stereochemical bond marks are preserved; they are not assigned invented configurations.

## Cleanup and appearance

2D Clean stages current/proposed views and requires Apply or Cancel. Applying is one undoable operation. Selected-region cleanup preserves unselected coordinates. The implemented backend is RDKit. Marvin JS parity remains unverified and mandatory in the full Goal.

Named drawing/export profiles have immutable numbered revisions. Applying a profile changes appearance and export settings while preserving molecular coordinates and chemical fields. Exact PNG preview uses the same exporter and offers physical-pixel inspection plus a fit view. A saved preview uses the exact preview PNG bytes.

Drawing appearance includes an adjustable multiple-bond spacing control (`0.06` to `0.28`, default `0.14`). Apply, Undo/Redo, project loading and rendering profiles use the existing style field. See [drawing appearance](docs/drawing-appearance.md) for the reference comparison and its limits.

Extra label clearance adjusts the additional space around atom labels from 0% to 20% of label size, starting at the existing 4%. It changes bond clipping around labels without changing molecular coordinates. Projects and profiles without this setting retain 4%; newly saved records carry the setting explicitly.

Atom labels are measured and drawn as vector outlines from Windows' installed regular Arial font, correcting the previous mismatch between estimated text widths and browser-rendered labels. Molecular coordinates remain unchanged; accurate label bounds can change scene centering and visible bond ends. No font file is bundled or downloaded. See [drawing appearance](docs/drawing-appearance.md) for font requirements and calibration limits.

Ordinary straight bonds joining different element colors use a short vector color transition informed by the supplied ethanol and carbonyl references. The editor, review panels and PNG export share that preparation step; molecular coordinates remain unchanged. Wedges and other unrecognized stroke geometry retain their existing paint.

The logo mirrors the complete word Marvin, including its glyphs, and exposes the readable accessible name nivraM. Reference-font and whole-corpus visual calibration remain unverified. No font binaries are intentionally redistributed.

## PNG export

The requested total width is an integer from 1 through 9999 pixels, including margins. Height is automatic and may exceed 9999. The default margin on every side is ceil(0.02 × width), with no arbitrary minimum. A width/margin combination leaving no drawable pixels is rejected; width 1 is usable with zero margins.

Visible ink includes labels, charges, isotopes and stereobonds. The result is opaque white by default. Check **Transparent background?** in the image save controls to preserve alpha instead; see [PNG transparency](docs/png-transparency.md). Final artwork is rasterized from vectors at the requested resolution; export never cleans or rescales the molecular coordinates. Selection overlays are excluded.

Resource gates include 32,767 pixels per side, 128 million pixels, and a conservative 512 MiB estimate for peak export buffers. A request can be rejected below the geometric limits when its estimated memory is excessive. There is no silent downscaling. Cancellation and failures release the owned raster resources.

## Properties and local naming

The properties panel shows formula, average molecular weight/molar mass, exact mass, isomeric SMILES and Calculated LogP — RDKit Wildman–Crippen. It records the analyzed structure and engine. Mixtures do not receive a single unqualified LogP; charged/unsupported chemistry receives explicit applicability limits. LogP is not LogD.

NISPO 0.1.7 generates candidate systematic names in a worker. The optional app-local Temurin/OPSIN runtime validates name-to-structure identity, including stereo, isotope and charge checks. A successful round trip is not a Preferred IUPAC Name claim. Missing or invalid runtime files leave verification unavailable without preventing the editor from launching. No Java installation is downloaded automatically or taken from PATH.

The evaluated naming runtime is staged separately for local build/install evaluation. Its exact manifest and runtime files must validate before packaging. Public redistribution clearance remains open under the strict no-font requirement: unchanged vendor source archives contain font resources and must not be folded into deliverables. No future source offer or upstream-link compliance claim is implied.

The separate Lookup in PubChem action prepares a local review of the exact SMILES, provider, purpose and service cost. Only Send to PubChem authorizes that one request. The returned database IUPAC-name field is associated with the drawing only when the supported structure identity matches; standardization differences, missing records and ambiguity remain explicit. The panel retains CID, retrieval time, submitted/returned representations and comparison details for the current result. Editing cancels or discards the old request. Cancellation cannot recall data already transmitted. No consent preference or lookup history is persisted. See [optional PubChem lookup](docs/optional-pubchem.md) for limits, evidence and remaining native/package acceptance.

## PNG import and scientific limits

PNG metadata inspection is local and isolated from drawing. Supported embedded molecular text can produce explicitly selectable disconnected-component candidates. Components are not automatically compounds: salts/mixtures may require selecting multiple components, and unsupported document objects remain disclosed. An accepted reconstruction preserves the original PNG in the local profile source store and records its hash/provenance.

PNG chooser, drag-and-drop and paste feed the review workflow. Bounded overview and original-resolution crop review, atom/bond corrections, replacement by supplied SMILES or 2D MOL, draft Undo/Redo and explicit acceptance are implemented. Acceptance creates one main-editor Undo step and preserves the source identity; PNG import Undo/Redo also restores the associated view context. An overview cannot authorize import. See [PNG text correction](docs/png-text-correction.md), [manual review](docs/png-manual-review.md), [Undo/view behavior](docs/png-import-undo-view.md) and [existing PNG encoding support](docs/png-encodings.md). The current decoder also supports non-interlaced 16-bit grayscale, RGB, grayscale-alpha and RGBA input, with rounded 8-bit review derivatives and full-precision transparent-key matching. Interlaced/color-managed PNG support and broader image-corpus validation remain incomplete.

[PNG recognition setup](docs/optical-setup.md) is included in the delivered optical-setup candidate. Native file dialogs select a separately prepared local runtime and model. The trusted core worker verifies the pinned model before saving; successful setup applies to future recognition without restarting or changing an open review. Setup does not execute the selected ML runtime, download dependencies or accept a structure. Runtime compatibility is checked during recognition, which rechecks model integrity and requires review of its proposed result. AdGuard and the VPN can remain enabled for local setup verification and recognition.

In historical local evaluation, the separately staged pinned model checkpoint passed source and packaged byte verification. Model files are not included in this repository. An earlier, separately recorded source-runtime aspirin fixture produced the expected structure; that single result is historical evidence, not recognition accuracy or a full optical workflow test of the current package. The current packaged worker check verifies model bytes only. Native setup interaction, installed intake/setup acceptance, broader recognition accuracy and independent traffic coverage remain unverified. Parsing or RDKit sanitization alone does not prove correspondence to an image. See [optical recognition](docs/optical-recognition.md) and the scoped delivery evidence below.

maraPssiwS contains a tested mathematical joint-state evaluator and an evidence-qualified physiology dataset. Supplied-model charts, tables, exports and supported 2D state preview/adoption are implemented; they do not establish a validated molecule-to-aqueous-population predictor. See [supplied populations](docs/supplied-populations.md) and [state adoption](docs/population-state-adoption.md). Renal contexts concern nephron luminal filtrate/tubular fluid, not final urine. Missing or non-comparable physiological intervals remain explicit gaps.

The current source adds an Open pH reference chart action beside the physiology selector. Its selected pH moves in exact 0.1 steps by pointer, arrow keys, Home/End, or numeric entry. Published boundaries such as 7.35 and 7.45 retain their exact positions independently of that selection grid. Reference intervals, study points, reported uncertainty and unverified custom comparisons keep distinct labels and visual marks. The table and JSON/CSV exports retain source metadata and explicit evidence gaps. Chart PNG exports use an opaque white background by default in both display themes, with an unchecked **Transparent background?** option for alpha output. These are reference-context exports, without molecular population results or an action to adopt a molecular state.

Desktop reference JSON, CSV and PNG exports now use a dedicated native Save dialog and verify the saved bytes. Existing files require explicit replacement confirmation and protected saving with a retained backup. Cancellation, conflict and uncertain completion remain distinct. Browser-only use retains ordinary downloads. See [native reference saving](docs/native-reference-saving.md) for its supported destination and size limits.

The chart uses a pinned local Chart.js asset, with no CDN dependency. Browser checks cover reference selection, provenance, white chart export and close-review behavior. Exact packaged/native acceptance of this source increment is pending. The strict browser network check currently reports blocked external injection requests; source inspection alone does not establish complete application network isolation in that environment. The previously installed candidate does not include this new chart.

## Diagnostics, tests and builds

About provides an inspectable local diagnostics report. Default diagnostics contain versions, configuration and lifecycle state, excluding molecular content, credentials and personal paths; nothing is uploaded. Scientific and release limitations are recorded in docs/requirement-matrix.json.

The historical, separately delivered optical-setup development installer is bound to source `5dde62c27baf95da4750f21ee728aeb1bcec64aa6978d0f4d6a6d05903cb2ade` and installer SHA-256 `b47465bc60cdf776bc35a8785696d83c69da470b8dcc3e280d37521245295c85`. Its historical local delivery report, not included in this source repository, records 123 focused staged Python tests, 62 JavaScript/browser checks with only the two recorded backend repair files differing afterward, successful packaged native startup, and successful pinned-model verification by the packaged worker. These are distinct checks, not a full suite or complete native workflow acceptance. This installer is unsigned and has not been executed or installed; no fresh installer antivirus result or clean-machine acceptance is claimed. This later documentation reconciliation does not change the delivered source identity or update the installer.

The follow-on non-interlaced 16-bit PNG preview/crop increment is separate from that immutable optical-setup installer. Consult its matching source-check and delivery receipts for validation and package identity. [The continuation plan](docs/NEXT.md) separates that work and the unranked backlog from acceptance and release work. Other feature choices remain pending. Optional 3D source capabilities now include the separate supplied-coordinate viewer, bounded generation and exact MOL/provenance PNG recovery; see [3D PNG scope](THREE_D_PNG.md). Historical installers do not acquire these later source features.

Relevant commands in the pinned environment:

    python -m unittest discover -s tests
    node --test tests/document.test.mjs tests/cleanup.test.mjs tests/profiles.test.mjs
    python scripts/check_native_acceptance.py
    python scripts/stage_naming_runtime.py --help
    python scripts/build_windows.py
    python scripts/build_installer.py --compiler PATH-TO-ISCC.exe

Before building, follow [signed RDKit preparation](docs/SIGNED-RDKIT-BUILD.md) and docs/naming-runtime.md to stage the hash-pinned vendor inputs. The exact Windows x64 CPython patch version, package lock and signed RDKit provenance are enforced. Installer output is bound to the verified bundle inventory, with each build installed into its own versions/version-bundleHash directory. The shortcut targets that build; retained previous directories support explicit rollback without changing user data. This candidate requires full installed file paths below 260 UTF-16 code units; the installer checks the longest bundled path and rejects an overlong destination. Keep a manually extracted portable bundle in a short folder path as well. No Windows path policy is changed.

The native acceptance script uses a visible window and synthetic ethanol. Hidden WebView startup proved unreliable in this host environment, so a hidden smoke result is not interchangeable with normal visible startup. Historical source acceptance recorded 302 Python tests, 86 pure JavaScript tests, and 16 native functional checks with confirmed download completion, owned-worker exit, normal WebView2 exit, and runtime-session removal; these counts do not describe the current optical-setup delivery. Earlier ordinary browser test teardown and native integration failures remain recorded; later passes do not erase that history. Preserve both positive and negative evidence.

Windows builds bind the source snapshot, verify the dependency lock, bundle desktop and worker executables, exclude font binaries, copy notices, and emit file hashes plus a build-environment CycloneDX inventory. The inventory is not yet a complete transitive-runtime release audit. Inno Setup packaging is defined in installer/nivram.iss. A built bundle, an installer, and a verified user installation are separate states; no release or installation is certified by source tests alone.

See LICENSE.txt and THIRD-PARTY-NOTICES.md. Private reference images, recovered private documents, skill archives, user projects and credentials are not redistribution inputs.

The optional 3D viewer now includes ordered-atom distance, angle and signed-torsion
measurements from accepted coordinates. After acknowledged PNG review, an optional
**Continue to 3D** shortcut uses the same current-graph generator. See
[measurement assumptions](docs/supplied-3d-viewer.md) and
[continuation limits](docs/generated-3d.md). Browser and Windows validation of these
additions remains deferred; controlled JavaScript tests are not visual qualification.

### Reusable local fragments

Use **Reusable fragments** in the inspector to duplicate a whole drawing or a
selection containing complete disconnected components. The desktop app can save
named immutable local copies and insert them later. Copies remain disconnected,
retain atom-map labels and stereochemistry annotations, and use fresh entity IDs.
Insertion is checked by the local render/adoption path and is one undoable change.
Partial molecular cuts and covalent attachment are not supported. See
[scope, persistence and qualification limits](docs/REUSABLE-FRAGMENTS.md).

Explicit MOL singlet radical declarations are rejected before their distinction
from triplets is lost by the native parser. This is a bounded unsupported-input
guard, not spin-state support; see [singlet declaration scope and remaining
normalization limitation](docs/mol-singlet-declarations.md).

Validated leading-plus MOL charge fields are normalized in an internal parsing copy,
without changing the original input or sanitization lifecycle. See the bounded
[signed-charge compatibility policy](docs/mol-signed-charges.md).
