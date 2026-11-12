# Reusable fragments: bounded local shelf

The inspector's **Reusable fragments** panel can duplicate the current drawing or
selected complete disconnected components. In the desktop app it can also save
named local copies and insert them later, including after a restart. Shelf saves
do not change the current project, its saved/dirty state or its destination.

## Supported scope

- Select every atom of the components you want. Selected bond endpoints count as
  selected atoms. With no selection, the entire drawing is used.
- A selection crossed by any bond is rejected. This release does not cut a molecule,
  infer attachment points, add hydrogens at a cut, or create covalent attachments.
- Insertion creates fresh atom and bond identifiers, remaps every endpoint and
  stereo-atom reference, and retains atom order, bond order/orientation, chiral tags,
  bond directions, isotopes, charges, radicals, hydrogen flags and atom-map labels.
- Copied map labels may repeat existing labels. They are deliberately retained,
  not renumbered or cleared. If uniqueness matters, adjust them with a suitable
  mapping editor before relying on the mapped result; this panel adds no map editor.
- A copy is translated without reflection or rotation. Its leftmost atom is three
  drawing units right of the existing rightmost atom, with vertical bounding-box
  centers aligned. An empty drawing starts at x=0, centered vertically. The view
  fits the result; no automatic cleanup, snapping or junction is performed.
- Current drawing appearance and export settings are retained. Selection clears
  after insertion. One Undo restores the preceding document.

## Validation and failure behavior

Every insertion uses the existing local render route and prepared SVG/document
adoption gate, bound to the captured document revision. A cancelled, stale,
invalid or close-suspended operation cannot commit. Before adoption, an additional
fidelity check rejects any returned change to the intended graph, including its
existing prefix, identifiers, order, coordinates, annotations or stereo references.
Returned appearance must also match the current validated style.

The only graph comparison equivalence is omission versus an explicit documented
default: atom charge, isotope, explicitHs, chiralTag, radicals and mapNum default
to 0; noImplicit and aromatic default to false; bond direction and stereo default
to 0, and stereoAtoms defaults to []. No other normalization is accepted. A
real-engine rejection or fidelity mismatch leaves the previous drawing intact.

Shelf records use schema `nivram-fragment/1`, with only a fixed UUID, display name
and graph. Validation of stored records is structural, **not a claim of chemical
validity**. Each insertion must still pass the actual chemistry engine. The shelf
allows at most 128 immutable records, 400,000 bytes each and 4,000,000 bytes in total.
Enumeration also stops after 256 directory entries of any type (including unrelated
files and temporary staging names); excess entries block listing/saving without
deleting anything. A save requires two free entry slots before publication: one
for staging and one for the final name, which briefly coexist during exclusive
POSIX publication. The conservative reservation also applies on Windows. With
254 existing entries, a successful save leaves 255 entries and a readable shelf;
255 or 256 existing entries reject the save before publishing. The editor's existing 512-atom/1024-bond and coordinate limits also apply.

Storage lives in the app's local `fragments` directory beside its existing
`profiles` directory. Web content cannot choose a filesystem path. Publication
uses the existing exclusive atomic writer, followed by byte-for-byte readback.
Existing records are never replaced or deleted. Malformed, oversized, linked or
redirected records are rejected/retained; invalid records block further saves.
An uncertain publication or readback returns **unconfirmed**, retains any written
file and never automatically retries. Reload the shelf before a manual retry.
Closing review discards late UI responses, but does not pretend it can cancel a
native file write already dispatched. Browser-only use supports duplication, not
persistent shelf storage. No graph is stored in localStorage or sent to a third party.

## Qualification limits

Current evidence covers pure graph/order/reference invariants, the production
insertion entry function with a transport fixture, real document/job history
semantics, shelf UI controller behavior and real local filesystem save/fault
operations. Controller fixtures are not browser or chemical validation.

The real-engine regression `tests/test_fragment_chemistry.py` exercises normalized
production-style reconstruction, actual rendering and isomeric identity for
representative tetrahedral, E/Z, mapped and isotopic examples when RDKit is present.
It is explicitly skipped when RDKit is absent. RDKit, pywebview, Windows file and
visual/accessibility acceptance were unavailable for this increment; chemical
parity and target-platform acceptance are **not established**. Static link/reparse
checks and bounded handle reads do not claim resistance to a malicious local
process racing replacement of storage directories. Existing installation and
release qualification holds remain unchanged.
