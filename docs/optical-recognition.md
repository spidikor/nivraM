# Local PNG structure recognition

This source implementation adds an explicit **Recognize structure in crop**
operation to the existing PNG review dialog. It is optional: embedded metadata
import and correction continue to work without a recognition runtime. The
matching delivery report identifies which installer contains each increment.

Model provisioning, completed recognition, accuracy and packaged operation are
separate checks. Consult the matching development or delivery report for their
outcomes. A verified checkpoint or passing protocol, chemistry and UI fixture
tests does not establish successful real recognition.

## User flow

1. Choose, drop or paste a PNG and choose a detail crop.
2. Explicitly request local recognition. The main drawing stays unchanged.
3. Review the exact crop and proposed structure, correct the draft and read its
   warnings. A model score is an uncalibrated score, not a probability that the
   structure is correct.
4. Acknowledge the reviewed draft before importing it. One main-editor Undo
   restores the preceding drawing.

Optical proposals and embedded metadata components are reviewed separately.
Changing the source, crop or selection revokes the current acknowledgement.
Recognizing a new crop is required before using a proposal for a different
region. Late, cancelled and superseded results cannot authorize acceptance.

## Optional local runtime

The core application does not install OCR libraries or download models during
recognition. MolScribe runs in a separately provisioned Python environment;
the core application's dependency lock and installed environment are unchanged.
Use **PNG recognition setup** in the toolbar, or the setup button in PNG
review, to choose the prepared runtime's `python.exe` and the pinned model file
with native file dialogs. **Verify model and save** checks the model's exact
size and SHA-256 in a bounded core worker, then saves and activates the setup
for future recognition. It does not run the selected interpreter during setup.
See [local recognition setup](optical-setup.md) for cancellation and failure
behavior.

The native application also reads `optical-runtime/runtime.json` in its own
profile directory at startup. Neither image content nor a web request can
supply setup paths. The in-app choices are retained only by the native host;
browser calls carry opaque setup identifiers.

The configuration has exactly these fields:

```json
{
  "schemaVersion": 1,
  "method": "MolScribe",
  "modelSha256": "32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501",
  "python": "C:\\absolute\\isolated-runtime\\Scripts\\python.exe",
  "model": "C:\\absolute\\models\\swin_base_char_aux_1m680k.pth"
}
```

These are example paths, not installed locations. A configured status loaded
from disk only establishes that the named files exist and the checkpoint has
its expected size. Saving through setup additionally verifies the complete
model hash. Every recognition checks integrity again and establishes runtime
compatibility for that request; setup does not certify the chosen executable.

Pinned model: repository `yujieq/MolScribe`, revision
`a0189776b7415b82795c7ee81eed311bf5c8724b`, filename
`swin_base_char_aux_1m680k.pth`, exactly **1,134,173,494 bytes**, and the SHA-256
above. The worker hashes the same file handle that it then passes to an explicit
`torch.load(weights_only=True, map_location='cpu')`. There is no unsafe-pickle
fallback. Encoder and decoder parameters must load strictly; incompatible
parameters are not silently ignored.

Checkpoint hashing and the initial Torch import run concurrently. Loading waits
for successful verification, and import failure still joins verification before
closing the checkpoint handle. This changes startup scheduling without reducing
the integrity checks or extending the recognition deadline.

Provision the checkpoint from the pinned official Hugging Face revision above.
The download may redirect from `huggingface.co` to `us.aws.cdn.hf.co`; these
hosts need to be reachable for that setup step. Verify the complete size and
SHA-256 before configuring the runtime, and retain normal TLS verification.
Provisioning does not require turning off AdGuard or a VPN. Local recognition
uses the already provisioned file and has no model-download path.

### Prepare the supported development runtime

The known MolScribe 1.1.1 / Albumentations 1.1.0 development runtime eagerly
imports plotting, CSV-training and domain-adaptation dependencies that the
selected recognition path does not use. The explicit preparation helper defers
those imports until their drawing, training or augmentation operation is called:

```powershell
python scripts/prepare_optical_runtime.py apply --runtime-root C:\absolute\isolated-runtime
```

This is a local modification to the separately provisioned runtime, not a
dependency installation. The helper checks exact supported source and package
metadata hashes, retains original bytes and a receipt, and rejects unexpected
source changes. The original package RECORD files remain intact as evidence of
the original installation. All statements other than the relocated imports,
including the inference transforms and model computation, must remain unchanged.

To restore the recorded original files, use the same command with `rollback`
instead of `apply`. Restoration also rejects unrecorded source or backup changes.
Do not run preparation while that runtime is processing a request. Successful
preparation establishes the recorded local modification; a real recognition
request is still needed to check runtime compatibility and completion time.

## Boundaries and provenance

- The decoder produces an RGB/RGBA crop of at most one million pixels and at
  most 2,048 pixels per edge. It retains only a validated sRGB declaration when
  present; other source metadata are not copied. PNG bytes are bounded to 4 MiB.
- The original source hash, crop geometry, crop PNG hash and model hash are
  carried through review and accepted provenance. Original bytes are preserved
  only when the existing acceptance operation prepares the import.
- CPU inference and trusted chemistry validation run sequentially in separate
  Windows-owned jobs sharing one 90-second deadline, including the preceding
  crop preparation. Successful-stage cleanup consumes that same budget. A final
  stop has a four-second cleanup budget and a five-second cancellation target.
- In source runs, chemistry validation uses the core interpreter in isolated
  mode with the trusted source directory supplied explicitly. Launching from a
  different working directory or setting `PYTHONPATH` cannot select a different
  validator. Packaged runs use the sibling `nivram-worker.exe`.
- Each recognition/validation job has a 4 GiB aggregate committed-memory limit
  shared by its descendants. The worker starts suspended, is assigned to a new
  private job and has its limits/membership verified before it can run. Only
  its three standard-stream handles are inherited. Failed setup has no
  unbounded fallback. The existing short chemistry-worker quotas are unchanged.
- Cancellation, timeout, excessive output and shutdown stop the owned job tree.
  A parent exit also stops remaining descendants; completion requires observed
  zero active job members. Native chemistry parsing runs in the bounded core
  worker, not the desktop host. See [recognition resource limits](optical-resource-limits.md).
- These are resource and lifetime controls, not an OS network or hostile-code
  sandbox. Windows memory limits constrain committed virtual memory; allocation
  can fail without immediately terminating a process. They are not a physical
  RAM reservation or a guarantee that the model will fit.
- Offline library settings and Python socket audit denials prevent Python-level
  network calls during recognition. They are defense in depth, not a claim of
  operating-system network isolation.
- Matching MOL and canonical isomeric SMILES identities preserve the recognized
  MOL coordinates. Contradictory identities and unresolved atom abbreviations
  reject the entire proposal; neither output is silently chosen as correct.
  The message offers a clearer crop or a user-supplied SMILES/2D MOL structure
  for review. See [structure consistency](optical-consistency.md).
- Unsupported queries, unresolved groups, explicitly ambiguous stereochemistry,
  invalid valence and structures beyond the existing editor limits are rejected.
  Recognition remains a proposed reconstruction, not independently verified
  chemical identity.

## Focused validation

The affected Python tests are `test_optical_import`, `test_optical_worker`,
`test_optical_jobs`, `test_optical_native`, `test_image_optical_session`,
`test_image_session`, `test_image_region_session`, `test_image_intake`,
`test_worker_processes` and `test_native_bridge` under `tests`.
The optional runtime preparation transactions and import-only source changes
are covered separately by `test_optical_runtime_preparation`.

The frontend tests include `tests/image-optical-review.test.mjs`,
`tests/image-optical-browser.test.mjs` and the existing image
review/intake/browser tests. Synthetic engine results exercise
review, correction, acceptance and cancellation; they do not measure optical
recognition accuracy. Full model inference and a rebuilt native installer must
be verified before describing ordinary-PNG recognition as ready to use.

Historical initial recognition outcomes: the ten Python modules above passed **115 tests, exit
0**. After a final malformed-configuration handling change, the affected
`test_optical_jobs` module passed **10 tests, exit 0**. The seven affected
JavaScript suites passed **45 tests, zero failures and zero skips, exit 0**;
all eight test-owned browsers closed normally. These are scope-specific checks,
not a repeat of the full application audit.

The frontend command was:

```text
node --test --test-concurrency=1 --test-timeout=100000 tests/image-optical-review.test.mjs tests/image-review.test.mjs tests/image-optical-browser.test.mjs tests/image-import-browser.test.mjs tests/image-region-browser.test.mjs tests/image-intake.test.mjs tests/image-correction-chemistry.test.mjs
```

## Files changed from the delivered PNG-correction build

- Local recognition: `nivram/optical_import.py`, `nivram/optical_worker.py`,
  `nivram/optical_jobs.py`.
- Native integration: `nivram/image_session.py`, `nivram/desktop.py`,
  `nivram/native_bridge.py`, `nivram/worker_processes.py`, `nivram.spec`.
- Interface: `public/index.html`, `public/modules/image-import.mjs`,
  `public/modules/image-review.mjs`.
- New tests: `tests/test_optical_import.py`, `tests/test_optical_worker.py`,
  `tests/test_optical_jobs.py`, `tests/test_optical_native.py`,
  `tests/test_image_optical_session.py`, `tests/image-optical-review.test.mjs`,
  `tests/image-optical-browser.test.mjs`.
- Updated tests: `tests/test_worker_processes.py`, `tests/test_native_bridge.py`.
- Documentation: `docs/optical-recognition.md`.
- Optional development-runtime preparation: `scripts/prepare_optical_runtime.py`
  and `tests/test_optical_runtime_preparation.py`.
