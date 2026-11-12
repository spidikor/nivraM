# Restore the main-editor view after PNG import

Accepting a reviewed PNG structure is one main-editor Undo step. That history
step can also carry temporary view information:

- **Undo** restores the preceding drawing, its pan and zoom, and its
  automatic-fit choice.
- Before Undo leaves the imported drawing, its current view is remembered.
  **Redo** restores that view of the imported drawing, including navigation
  performed after import.
- Ordinary edits after import keep the current view. Their history entries do
  not repeatedly apply the PNG import's earlier view information.
- If the window size has changed, a restored manual view keeps its molecular
  center and displayed scale. A restored automatic view fits the drawing to
  the current viewport and continues fitting on resize.

Reaccepting an unchanged document preserves the current view and does not add a
molecular history entry merely to store a view. View restoration belongs to an
actual PNG import history step.

These controls are the main editor's Undo and Redo. The PNG review dialog has
separate [draft navigation and correction history](png-review-navigation.md).
The source crop and explicit review acknowledgement remain separate from view
navigation; see the [manual review workflow](png-manual-review.md).

## Temporary view information

The optional view context exists only in the current editor's history. It is
not part of a saved project, document dirty-state identity, recovery data or
exports. It does not provide persistent view restoration after closing and
reopening the application. This increment changes no molecular model, 3D
behavior or resource limits.

## Verification status

The missing pre-import view restoration was reproduced before this increment.
Focused tests exercise entry isolation, failed/stale commits, grouped edits,
history limits, renderer and viewport changes, and PNG acceptance followed by
ordinary editing, Undo and Redo. Browser tests use the real parser and editor;
the native PNG selection and source-storage boundary is a fixture. They do not
certify installed clipboard/chooser interaction or clean-machine acceptance.
Consult the matching delivery report for exact results, packaged startup
verification and the source identity. No full optical-model inference is claimed.
