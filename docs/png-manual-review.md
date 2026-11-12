# Review an ordinary PNG with a structure you supply

A PNG does not need embedded molecular data or an optical-recognition model to
enter structure review. You can supply SMILES or a supported 2D MOL structure,
compare it with a crop of the image, correct it, and explicitly accept it.
This imports your structure text; it does not recognize the image or verify
that the structure matches its pixels.

## Use the feature

1. Choose **Import PNG**, drop a PNG onto the drawing, or paste an image.
2. Choose a crop containing the complete structure you want to review. Use the
   crop coordinates, or load the overview and drag a region. The initial crop
   covers the whole image when it fits the displayed default dimensions.
3. Under **Supply a structure for this PNG**, paste SMILES or supported 2D MOL
   text and choose **Start review from structure text**.
4. Compare the supplied structure with the source detail shown at 100%. Review
   atoms, bonds, charges, isotopes, stereochemistry and any omissions. The
   supplied coordinates or generated text-import layout do not trace the PNG.
   Use **Zoom in**, **Zoom out**, **Pan view** and **Fit draft** to inspect detail;
   [your chosen view stays steady during corrections and Undo](png-review-navigation.md).
5. Use the correction controls as needed. You can also [replace the draft with
   different structure text](png-text-correction.md), with **Undo correction**
   and **Redo correction**. **Reset corrections** restores the supplied draft.
6. Acknowledge the reviewed result, then choose **Import reviewed components**.
   One main-editor **Undo** restores the complete preceding drawing and its
   pan, zoom and automatic-fit choice. **Redo** restores the imported drawing's
   view as it was when Undo left it. See [PNG import view history](png-import-undo-view.md)
   for temporary-view scope and the pending verification status of this increment.

During review, the open drawing and its save destination remain unchanged.
Acceptance creates an unsaved project, preserves the original PNG in the local
profile, and retains source/crop identity and the supplied baseline structure.
No image or structure is uploaded. No optical model is required for this path.

## Input, crop and cancellation limits

- Parsing uses the protected local import worker and the existing 2D chemistry
  limits. Text is limited to 1,000,000 UTF-8 bytes. MOL headers are retained.
- A detail crop is required, including for small PNGs. It is bounded to one
  million pixels and 2048 pixels per side. The source must support the existing
  bounded PNG crop decoder. Grayscale and indexed 1/2/4/8-bit PNGs, grayscale-alpha
  8-bit PNGs, and RGB/RGBA 8-bit PNGs are supported without interlacing, including
  supported transparency. See [PNG formats and limits](png-encodings.md).
  The overview alone cannot authorize import.
- A supplied draft is reviewed separately from embedded metadata and optical
  candidates. It is labeled as supplied by you, with no recognition score or
  model attribution.
- Invalid text stays available for correction. Successful preparation clears
  the initial text field. Closing the dialog clears it as well.
- Changing the crop revokes the prepared draft. Supply text again for the new
  crop. Closing the review, choosing another PNG or editing the open drawing
  cancels pending work; a late result cannot replace the current drawing.
- Later replacements and atom/bond corrections use the existing private draft
  history. Failed replacements retain the previous valid draft. They do not
  affect the main editor until acceptance.

## Evidence scope

The focused controller and browser cases are in
`tests/image-manual-review.test.mjs` and
`tests/image-manual-browser.test.mjs`. Native session and bridge cases are in
`tests/test_image_manual_session.py` and `tests/test_image_manual_native.py`.
Consult the matching delivery report for the exact source, check results and
packaged status.

The browser cases use the actual parser, renderer, controls and history, with
the native image boundary supplied by a fixture. A separate local workflow
check exercises the real PNG session and protected parsing worker. Neither
establishes a full installed Windows clipboard/chooser workflow or recognition
accuracy. Automatic PNG recognition remains a separate
[unfinished capability](optical-recognition.md).
