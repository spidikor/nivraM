# Explicit isotope mass availability

This source increment prevents a missing RDKit isotope-table entry from being
reported as a valid molar or exact mass. The approved Linux runtime is RDKit
2025.09.4 (distribution 2025.9.4). Its explicit-isotope atom-mass fallback uses the
integer isotope label when the table returns zero; that is not a measured or
supported isotope mass.

For each **explicit nonzero** isotope label, the property calculation checks
`GetMassForIsotope(atomicNumber, isotope)`. A nonpositive or nonfinite entry makes
both `molecularWeight.value` and `exactMass.value` null for the whole submitted
graph. No substitution, partial sum or approximation is returned. Isotope zero
retains the existing unlabeled engine path, even if the engine's default isotope
has no separate explicit-table entry. Supported isotope labels and empty-graph
behavior are otherwise unchanged.

Missing table coverage does **not** establish that an isotope is nonexistent,
unstable or physically impossible. Graph annotations, coordinates, stereo,
charge, formula, canonical isomeric SMILES, hashes and document revision are
preserved. The existing LogP calculation, applicability and its own warnings are
unchanged. A single fixed-size mass explanation is appended to general warnings,
not to `logP.warnings`.

## Contract and consumers

Only an unavailable nonempty result gains `massUnavailable`:

- `reason`: `missing_isotope_mass`.
- `message`: the fixed bounded explanation defined in `nivram/mass_contract.py`
  and `public/modules/mass-contract.mjs`.
- `isotopes`: deduplicated `{symbol, isotope}` entries identifying missing explicit
  labels, sorted by symbol then isotope by the producer. Each must appear in the
  submitted graph; list length cannot exceed the graph's atom count. Isotopes
  remain in the existing 1–400 annotation range. No separate nuclide validator
  or new dependency is introduced.

Both masses must be null when that metadata is present. Nonempty mass nulls
without it, malformed reasons, empty/duplicate/mismatched isotope lists, and
numeric mass mixed with unavailability are rejected at updated strict boundaries.
The data-only Python preparation validator and JavaScript validators check the
transport and graph relationship, not the scientific truth of a table lookup.
RDKit remains the source of table coverage; the browser does not carry a duplicate
table. Existing hash/request/revision/stale-job and sanitizer guards are retained.

Scene `mass` is null with the same explanation in `scene.properties` and both
nested descriptor values null. Supported scenes retain their existing shape and
rounded numeric mass; empty scenes retain mass zero. Import/batch preparation
admit only the constrained unavailable form. The main header shows “Molar mass
unavailable” and the reason in its title; the existing analysis sidebar shows
“Unavailable” for both values and the reason in provenance. Comparison shows
unavailable values and no numeric mass delta if either side is unavailable.

Comparison responses now use **`nivram-comparison/2`** because v1 required finite
mass for every nonempty graph. Its request shape is unchanged; /2 retains the old
numeric fields and optionally carries `massUnavailable` per side. The shipped
client requires /2, so mixed old/new comparison server/client versions fail closed
rather than silently accepting broadened null semantics. Property and scene
results have no standalone version field: the optional metadata is a coordinated
source API extension, and older strict preparation/scene consumers will reject it.
Projects are unchanged (`nivram/1`); no persisted schema migration is needed.

## Verification and limits

Focused tests include independently queried missing-table sentinels, C1/C400,
Na400, explicit Db268 versus unlabeled Db and other isotope-zero controls, supported
C13/H2/H3/Tc99, charged/stereo/radical/fragments, many missing labels, nonmutation,
JSON safety, strict preparation data, real local HTTP property/comparison routes,
controlled DOM unavailable output, obsolete replies and existing 3D declaration
regressions. Source-bound receipts contain actual commands, counts and failures.
Controlled scene fixtures are not rendered images. Nonempty real rendering remains
blocked here by the existing trusted Arial requirement; no replacement font or
renderer weakening was used. Declaration-only V2000/V3000 3D is unchanged. No
browser, Windows/native, installed application or installer acceptance is claimed;
existing installers are unchanged. The full project Goal remains incomplete.
