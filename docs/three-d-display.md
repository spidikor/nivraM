# Optional 3D display controls

Default: ball-and-stick, Explicit hydrogen unchecked (ordinary bonded H condensed), slow rotation unchecked, direction left-to-right.
The 3D dialog offers a native labelled representation select and adjacent native labelled Explicit hydrogen and Slow rotate checkboxes. Rotation
is horizontal and has an adjacent native labelled Rotation direction select. Left
to right uses positive yaw (the same direction as Rotate right); Right to left uses
negative yaw. A viewer-facing positive-z atom moves in the selected screen direction
initially; points on the rear hemisphere move
oppositely, as expected for rotation rather than translation. Automatic motion never
changes pitch. Changing direction while rotating keeps the current camera and
reverses the next animation step without starting another loop. Choosing a direction
while stopped does not start motion. One revolution takes approximately 60 seconds of active animation
at normal frame cadence. Long frame gaps are capped at 100 ms per update. Closing,
destroying, hiding the document, or enabling reduced motion stops and unchecks it.
Showing the document or reopening never restarts it automatically.

## Hydrogen representation is view-only

Explicit displays existing H coordinates, not newly calculated H coordinates.
Implicit condenses ordinary neutral, non-isotopic, unmapped H with one single bond
to a non-H parent into an H-count label on that parent. Only existing coordinate
atoms are counted. Before condensation, a separate bounded syntax-only read of the
exact MOL supplies declarations omitted by historical generated/recovered scene
schemas. Atom IDs, elements, coordinates, charge and isotope, any supplied scene
map values, and all bond IDs/endpoints/orders must match. This does not replace or
mutate the accepted scene, MOL or provenance and grants no new chemical validation
status. Unsupported source syntax, absent source or any mismatch retains H with an
explicit unresolved-annotation count. Thus mapped source H remains visible even
when a worker scene omits its atomMap field. H with special declarations, charge, isotope, mapping or radical
fields, unbound H and H–H remain explicit. Current supported generation excludes
radicals; no new parser chemistry capability is implied. No-H source structures do
not gain H atoms or inferred H counts. The on-screen notice states this limitation.
This is deliberately partial implicit-H support, not a new valence engine or a
coordinate generation service. All source atoms/bonds stay in the accessible tables.

## Radius model and limits

Space-filling uses the following conventional van der Waals radii in angstroms,
read directly from the installed RDKit 2025.09.4 Chem.GetPeriodicTable().GetRvdw API:
H 1.20, He 1.40, C 1.70, N 1.60, O 1.55, F 1.50, Ne 1.54, Si 2.10,
P 1.95, S 1.80, Cl 1.80, Ar 1.88, Br 1.90, Kr 2.02, I 2.10, Xe 2.16.
The source/version/values are recorded in the companion radii-source.json evidence.
This is an RDKit-version-bound conventional rendering table, not an exact Bondi
attribution or a new experimental data compilation.
These conventional reference values are fixed display parameters, not fitted or
measured properties of the imported structure. No ionic/charge-specific radius
correction is applied. The source coordinate unit is assumed to be angstroms, as
for conventional MOL coordinates; no experimental or unit validation is performed.
Any element outside this finite table causes an explicit, visible fallback to
ball-and-stick, not a guessed radius. No independent primary-literature review of these provider values is claimed.

Atoms are orthographic SVG disks drawn in center-depth order. Radii and centers
share a spatial scale, with sphere padding included in default fitting. No bonds
are drawn in space-filling mode. This is a sphere schematic, not a per-pixel sphere
intersection renderer, solvent-accessible surface, experimentally determined
surface or chemistry validation. Ball-and-stick uses fixed illustrative circles.

## Preservation and export

The controls do not alter source MOL, atom/bond arrays, declarations, provenance,
native validation or the 2D graph. PNG pixels freeze the selected display and camera
at invocation, alongside the unchanged exact-coordinate metadata. Display choices
and camera are not new metadata fields. Opening a PNG retains the viewer's current
controls (a newly mounted viewer starts with defaults); it does not recover the
exported display choices. Image pixels and embedded full chemistry can therefore
show different H visibility. All receivers can recover all embedded atoms.

## Qualification

Pure functions and a controlled DOM cover display behavior, special H retention,
both screen directions, smooth reversal, yaw-only automatic motion, cleanup, reduced motion and source
invariance. The named existing PNG/validation/chemistry suites are retained. No
real-browser rendering, focus/accessibility visual audit, actual Arial rendering,
installed Windows lifecycle or exact sphere surface qualification is claimed.
