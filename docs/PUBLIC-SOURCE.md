# Public development source

This repository imports the reviewed nivraM development source checkpoint dated
2026-11-12. Application code, tests, synthetic examples, build scripts, dependency
requirements and third-party notices are included. This is not an installer, a
certified release or a claim that the whole test suite passes on a new machine.

The source-only publication preserves executable, test and example bytes from
that checkpoint. Publication edits clarify documentation scope and remove
private reference-derived evidence. The earlier repository README remains in
Git history.

## Intentionally excluded

- Private checkpoint evidence, execution logs, recovery archives, user projects,
  reference images and personal data.
- The private reference inventory and calibration JSON documents and their
  aggregate private-input measurements in documentation.
- Installed environments, generated build outputs, installers, fonts, custom
  Windows RDKit wheels, Java/OPSIN binaries, OCR model weights and ML runtimes.

Public third-party attribution and synthetic privacy/security test strings are
retained. Licensing material for separately staged dependencies does not mean
those dependencies are bundled or cleared for binary redistribution.

## Setup and qualification

The desktop target is Windows. Follow WINDOWS-SOURCE-SETUP.md and
SIGNED-RDKIT-BUILD.md for its pinned prerequisites. Linux source inspection and
scoped tests do not establish Windows, browser or installed-app qualification.
Some rendering tests require trusted installed Arial; font files are not shipped.
The repository does not yet provide a validated molecule-to-aqueous-population
predictor or establish recognition accuracy or Marvin cleanup equivalence.

Historical counts and artifact identifiers in older documents describe their
original local checks. Missing private evidence is not recreated or represented
as fresh public validation. See the README and requirement-matrix.json for
feature-specific limitations.
