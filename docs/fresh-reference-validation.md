# Fresh development evidence without the original archive split

The original archive's per-image exposure and evaluation mapping has not been
recovered. This approach no longer depends on recovering it. The archive remains
preserved and unclassified; neither its images nor overlapping new attachments
are relabeled as independently unseen. Historical results retain their scope.

## Two distinct checks

### Known-structure correction challenges

`tests/fixtures/reference-correction-challenges.json` contains five freshly
authored synthetic graph pairs. Intended targets and deliberately incorrect
proposals are declared separately, without generating the target through a
nivraM importer, editor command or renderer. A second set of literal adjacency
and annotation assertions in `reference-correction-challenges.test.mjs` checks
the targets and proves that each initial proposal is distinguishable.

| Challenge | Property exercised |
|---|---|
| Ethanol versus dimethyl ether | Same elemental counts, different connectivity |
| Ammonium annotation plus chloride | Charge, explicit H, isotope and separate counterion |
| Adjacent annotated centers | Targeted wedge/hash edit clears the affected tag, retains the remote tag and bond |
| N-methyl lactam skeleton | Carbonyl order correction retains the ring and other attachments |
| Sulfur-to-oxygen chain correction | Element replacement retains connectivity, isotope and atom mapping |

The actual image-review controller, document store and editing commands perform
the corrections. Each case exercises staged isolation, draft Undo/Redo, explicit
acceptance, baseline-versus-corrected provenance and a single main-editor Undo.
Failure checks cover a rejected correction and counterion loss in the final
native receipt. The previous six structure-preservation tests are retained.

Native inspection, supplied-text parsing, optical recognition, chemical rendering
and visual review are controlled seams in these tests. Their one-pixel PNG
carrier contains no chemical drawing. The declared graphs are not purported
recognition results. In particular, wedge/hash and chiral-tag checks establish
annotation handling, not correct R/S assignment or native chemistry validation.

All fixtures are **exposed development material**. They are not a blind accuracy
benchmark, and their arbitrary coordinates are not correct drawing-scale rules.
No private reference pixels or recovered archive graphs are bundled in the new
source fixtures. No application implementation or default was changed.

Run the focused controller checks from this source root:

    node --test tests/reference-correction-challenges.test.mjs tests/image-review.test.mjs tests/image-manual-review.test.mjs tests/image-optical-review.test.mjs tests/edit-commands.test.mjs tests/bond-edit.test.mjs tests/reference-structure-preservation.test.mjs

Consult the matching evidence packet for actual runs and independent review;
the command and test definitions alone are not passing evidence.

### Private pixel-evaluation boundary

Private reference filenames, hashes and image-specific observations are omitted
from this public tree. Local comparison of preview/crop pixels with a full-source
decode does not show correct molecular recognition or drawing scales. Decoder
comparisons sharing Pillow are not entirely independent implementations and do
not establish isolated-worker containment, GUI decoding, browser color management,
native Windows behavior or installation.

## Continuing from this foundation

- Add a fresh structural challenge only for a demonstrated gap or a meaningful
  new supported chemistry feature. Keep targets independent of application output.
- Treat supplied private images as exposed chemical-reference/development material,
  retaining known repeated structural families and ambiguous montage contents.
- For actual recognition accuracy, obtain a new independently labeled evaluation
  set with recorded provenance and structural-family grouping before using it for
  tuning. Never create a purported unseen set merely by splitting these exposed
  images or counting crop/scale variants as independent compounds.
- Record chemical identity, charge, isotope, stereochemistry, omissions and
  ambiguity separately. Do not infer a graph from a filename or pixel dimensions.
- Browser and Windows testing remain deferred. Native chemistry/OCR execution and
  empirical recognition accuracy remain separate, unfinished gates; these source
  checks do not close them or the exact Marvin JS compatibility requirement.
