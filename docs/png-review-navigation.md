# Navigate the PNG structure draft

The structure draft has its own view controls during PNG review:

- **Zoom in** and **Zoom out** change the drawing scale around the view center.
- Scrolling over the draft zooms around the pointer position.
- Choose **Pan view** in the Tool list, then drag the draft to move the view.
- **Fit draft** shows the complete rendered structure, including labels and
  disconnected fragments, at the existing comfortable-scale cap.

These actions change only the review view. They do not change atom positions,
make chemistry requests, add correction history, or clear the review
acknowledgement. Use **Select / move** to move atoms instead of the view. The
keyboard **Apply tool to target** action is disabled while Pan is selected.

## Keyboard navigation

Focus the draft canvas to use **+** (or **=**) and **-** for zoom. With **Pan view**
selected, arrow keys move the view and Shift makes a larger step. Escape ends
an active pan. The buttons are also available through ordinary keyboard focus.
Text fields retain their normal editing behavior.

## Keep your place while correcting

After you zoom or pan, ordinary corrections, correction Undo/Redo and window
resizing preserve your chosen molecular-world view. The renderer may change its
internal image origin or scale when a label changes; navigation accounts for
that change so the molecule does not jump solely because it was rendered again.
Moving an atom still moves that atom within the retained view.

**Fit draft** returns to automatic fitting. While automatic fitting is active,
later corrections fit their complete rendered ink. Replacing the entire draft
with pasted structure text starts fitted, as does opening a new review.
Navigation is disabled while a correction is being prepared and after the review
closes. Navigation stays separate from the crop selected in the source image.

Zoom has bounds to avoid unusable scales. If Fit needs an exceptionally wide
view, zooming out at the limit does nothing rather than zooming inward; zooming
in moves gradually toward the ordinary range.

## Verification scope

`tests/image-correction-view.test.mjs` checks pointer anchoring, renderer
transform changes, resize, fitting, limits and independent view state.
`tests/image-correction-view-browser.test.mjs` exercises the actual correction
controls, chemistry parser/renderer, pointer and keyboard navigation, history,
replacement and pending-work guards. Its native PNG selection/storage boundary
is a synthetic fixture. It does not establish full installed clipboard/chooser,
touch-device or assistive-technology acceptance.

See the matching delivery report for the exact source, browser checks and build
identity. This feature does not add optical recognition; the
[supplied-structure workflow](png-manual-review.md) remains available locally.
