# Replace a PNG review draft from structure text

Inside an existing PNG component review, you can replace the entire structure
draft by pasting SMILES or a supported 2D MOL file. Parsing happens locally using
the same protected import worker as the main editor. This does not recognize an
image or establish that the pasted structure matches its pixels.

## Use the feature

1. Choose, drop or paste a PNG. Select its supported components and preview them,
   or prepare a local recognition proposal when the optional model is available.
2. In the structure-draft panel, paste SMILES or 2D MOL text into **SMILES or 2D
   MOL**, then choose **Replace review draft**.
3. Compare the replacement to the original image or reviewed crop. Continue
   correcting atoms, bonds, charges, isotopes or stereochemistry if necessary.
4. **Undo correction** and **Redo correction** navigate the draft history.
   **Reset corrections** restores the original prepared proposal.
5. Acknowledge the reviewed structure again, then import it. One main-editor
   Undo restores the drawing that was open before the import.

Replacement affects the whole draft, including every selected component. The
open document, rendering settings and save destination remain unchanged during
review. The normal acceptance operation preserves the original PNG and records
the original and corrected graphs as user-edited provenance.

## Input and cancellation

- Text is limited to 1,000,000 UTF-8 bytes. MOL headers and coordinates are passed
  intact; the input is not trimmed before parsing.
- Existing import limits and chemical restrictions apply. Invalid valence,
  unsupported query structures, reactions and 3D MOL input are rejected.
- Failed parsing retains the previous valid draft and the pasted text for
  correction. Successful replacement clears the text field and creates one
  draft Undo step. New replacement clears the draft Redo branch.
- Starting a replacement revokes the current acknowledgement. Cancelling the
  review, changing its source or editing the open document prevents a delayed
  parse response from becoming an accepted draft.
- Closing a review clears its text field. Pasting into this field does not
  trigger the separate PNG clipboard-intake workflow.
- If preparing the import fails before the main document is changed, a still-current
  review keeps its corrected structure, correction Undo/Redo history and unfinished
  replacement text. Review and acknowledge it again before retrying; every retry
  repeats source preservation and acceptance validation. Cancelling, changing the
  source or crop, or editing the main document invalidates that review. A returned
  receipt that fails validation, or an uncertain document commit, still requires a
  fresh preview rather than reusing the old acceptance state.

## Verification scope

The affected checks are `tests/image-review.test.mjs`,
`tests/image-text-correction-browser.test.mjs`,
`tests/image-correction-chemistry.test.mjs` and
`tests/image-optical-review.test.mjs`. Consult the matching delivery report for
their current outcomes and packaged status.

The production browser scenarios use the real local parser, renderer, review
controls, draft history and editor Undo. Their native image-inspection and source
storage boundary is supplied by a fixture. They do not prove an installed
Windows clipboard/chooser session or general optical-recognition accuracy.

Automatic recognition still needs its optional model provisioned and a real
inference run. See [local optical recognition](optical-recognition.md). This
correction feature does not replace that requirement.
