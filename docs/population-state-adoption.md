# Preview and use a supplied 2D state

Population states can now be selected independently of curve visibility. With an
explicit matching structure file, the selected state can be previewed and used
as one undoable replacement of the whole drawing. Numerical model files remain
usable without molecular structures.

This does not infer structures from state names, validate supplied energies,
establish the model's applicability to the current drawing, or adopt physical
3D conformer geometry. The preview and action retain these limitations.

## Workflow

1. Open a complete supplied model using **Open population model**.
2. Choose **Inspect** beside a state in the table. This is separate from **Plot**;
   hiding a curve never changes its population or selects another state.
3. Choose **Choose state structures JSON** and open the matching structure file.
   It must provide the selected state and the model's reference state.
4. Review the prepared 2D drawing and selected pH/population. Moving the pH
   selection reads cached values; it does not rerun structure preparation.
5. Select **Use this 2D state** to replace the drawing. **Undo** restores the
   previous drawing, settings and analysis metadata in one operation.

Closing, cancelling, changing state/model, or editing the main document revokes
unfinished preparation. Changing state while adoption awaits a document render
also cancels that pending edit. **Preview selected state** retries a cancelled
or failed preview without reopening the structure file. Adoption requires a pH
sample with computed values; no missing population is substituted.

The project keeps its current appearance and export settings. Adoption is an
edit to the current project and does not reset its save destination. Old
population results are cleared after the drawing changes.

## Structure file

The additive format leaves the existing numerical model schema unchanged. The
structure file is UTF-8 JSON, at most 2 MiB, with exactly these top-level fields:

- `schemaVersion`: `1`.
- `fileKind`: `"nivram-population-state-structures"`.
- `modelInputHash`: SHA-256 of the exact UTF-8 model text accepted by the local
  evaluator. Changing whitespace or line endings changes this binding.
- `structures`: a nonempty list of at most 4096 distinct state records. A partial
  list is allowed; missing states have no structure preview.

Each record has exactly `stateId`, `structureId`, `format`, `source` and
`coreAtomMaps`. The first two must match that model state's opaque identifiers.
`format` is explicitly `smiles` or `mol`; `source` contains one supported molecular
record. No identity label is interpreted as a chemical string or network address.

`coreAtomMaps` contains `{ "atomId": "a0", "mapNum": 1 }` records that link the
model's arbitrary atomic-core labels to positive, unique molecular atom-map
numbers in the source. Every declared core label must resolve once. All
non-hydrogen atoms and isotopic hydrogens belong to the mapped core; mapped
non-exchanged hydrogens are retained. Unmapped ordinary exchanged hydrogens are
handled by the hydrogen-stoichiometry check, rather than silently discarded
from a claimed core mapping.

The selected and reference structures must preserve each mapped core atom's
element and isotope. Their observed charges must equal the model declarations,
and their hydrogen-count difference must agree with `relative_protons`.
Supported chemical identity and stereochemistry must survive parsing, depiction
and graph/MOL round-trip. Unsupported structures and declared 3D MOL files are
rejected explicitly. These checks do not prove that the supplied thermodynamic
values describe those structures.

## Demonstration files

Open [the synthetic pKa-7 model](../examples/population-analytical-pka7.json), then
[its companion structure file](../examples/population-analytical-pka7-structures.json).
The structures use methanol and methoxide to demonstrate mapped 2D adoption.
**The pKa-7 energies are artificial test data, not methanol thermodynamics.**
This example demonstrates the interface, arithmetic and Undo behavior only.

Keep both original files when reusing a supplied model. Population JSON/CSV
exports preserve numerical results and model metadata; they do not embed the
separate structure file.

## Local execution and saved provenance

Preparation uses the existing authenticated local server and isolated import
worker. The worker installs its existing verified limits before processing input:
1 GiB process commit memory, 10 seconds of CPU time, one process and a 20-second
wall deadline. Requests bind the exact model and structure text digests, selected
state, model signature and drawing revision/digest. Drawing context establishes
freshness, not chemical correspondence. Cancellation includes bounded handling
for a request cancelled before worker registration.

The frontend validates the returned graph, scene and receipt before displaying a
sanitized preview. Preview DOM or returned copies cannot alter the private graph
used for adoption. The editor checks the document revision again before commit.

`analysis.populationAdoption` records the model/structure-file digests, selected
state and pH, fraction and normalization, conditions, model provenance and
chemical preparation receipt. It does not copy the full 141-sample result into
the drawing. Other analysis object keys remain; legacy array-valued analysis is
retained under `previousAnalysis`. No external request or AdGuard exception is
required for this workflow.

## Verification boundaries

Focused tests exercise state/file binding, mapped atoms, charges and hydrogen
counts, isotopes, supported stereo and MOL round-trip, 3D rejection, bounded
worker/HTTP behavior, cancellation, independent chart selection and preserved
project metadata. The production browser flow exercises real local chemistry,
unchanged drawing before adoption and one Undo afterward; a cancelled late
preview cannot alter the drawing.

The delivery report records the exact build and packaged checks. Source tests
are not installed-native acceptance. Actual scientific energy-to-structure
correspondence, molecular prediction, physical 3D conformer adoption, maximum
state-count performance, clean-machine installation and antivirus status of a
new installer remain separate boundaries.
