# Pinned structure comparison

The Structure properties sidebar opens a read-only comparison dialog. **Pin current
drawing** captures an immutable copy of the current project; **Pin saved project**
reads one local `.nivram` or compatible JSON project, up to 4 MB, without opening
it into the editor. Existing project validation rejects unsupported fields,
nonfinite values, excessive nesting and unsupported graph/style/export fields.
Candidate pins are admitted only after local chemical/property validation succeeds.
A bad file, failed request or timeout retains the previous accepted pin. Neither source file nor current document is
written. The pin lasts only for this app/page session, not across restarts and is
not included in saved projects or recovery. **Unpin** forgets only that session copy.

Both panes show the explicitly pinned structure and current drawing at the reported
revision. The dialog is modal: close it to edit the drawing, then reopen to compare
against the retained pin. Refresh also captures the current revision explicitly.
Each drawing uses its own original coordinates and appearance, with independent
fit to the pane. No cleanup, alignment, matched image scale, atom mapping,
atom-by-atom highlighting, isomer enumeration or similarity score is implemented.

## Properties and identity

The authenticated local `/api/comparison` route computes both sides from copied
graphs using the existing RDKit property implementation. Nothing is sent to an
external service. It preserves isotope, charge, radicals and supported stereo
through the existing chemical validation; it does not neutralize, select a main
fragment, protonate or change tautomer. The comparison shows:

- Formula and canonical isomeric SMILES for each side.
- Molar mass in g/mol and exact mass in Da.
- Formal charge and number of disconnected fragments.
- Calculated RDKit Wildman–Crippen LogP with each side's applicability warnings.

Numeric differences are **current minus pinned**. Formula has no numeric difference.
Mass differences are unavailable if either side has no value. A LogP difference is
shown only if both statuses are `calculated`; charged, radical, mixture and other
applicability warnings prevent a displayed difference. The underlying descriptor
values and warnings remain visible where available. This is not experimental LogP,
pH-dependent LogD, potency, similarity or a causal explanation of a structural change.
Values are rounded only for display; deltas use the full returned numbers.

Equal or unequal canonical isomeric SMILES is described as exactly that RDKit result,
not experimental identity or an atom correspondence. Atom-map labels can affect
these strings. Request ID, pin ID and captured document revision bind the response
to one comparison. Hash fields are format checked, not independently recomputed
in JavaScript; this is not an integrity claim against a malicious local server.

## Preview and failure behavior

Each preview is generated on a copy and passed through the existing strict SVG
sanitizer and locally generated bond-paint path. Source atom IDs, position count,
scene metadata and appearance are checked before mounting a separate SVG.
The main drawing's scene, history, dirty state, destination and exports are untouched.
SVGs remain light-theme ink on white; independent fits are explicitly labeled.

A chemistry validation failure rejects the whole comparison. An expected renderer
failure, such as unavailable trusted-system Arial, is returned as **Preview
unavailable** for that side, while independently successful property calculations
remain usable. No substitute font, cleaned coordinates or fabricated drawing is
adopted. Unsafe or inconsistent returned SVG is also withheld, with an explicit
preview error. Successful properties do not qualify the missing drawing.

Changing the live document clears the displayed pair immediately. Changing/removing
the pin, cancelling, closing the dialog, closing the app, starting a newer comparison
or timing out invalidates old responses. Pending file reads have the same admission
invalidation; closing or replacing a pin prevents a late file read from installing it.
Closing leaves the pin for this session but clears displayed results. There are no
background comparisons or saved comparison records. Text is rendered as text, not HTML.

The client waits at most 20 seconds, and input/output sizes are bounded. Cancelling
stops its wait; it does not claim to interrupt synchronous RDKit work already running
on the local server. Existing 512-atom/1024-bond limits apply to each graph. A general
server hard wall-time or memory bound for all chemistry operations is not established.

## Qualification

Focused real Linux RDKit property, identity, nonmutation and local HTTP tests run
with the approved pinned environment. Independent checks include stereo/isotope/
charge distinctions and source/file/response races. JavaScript preview checks use
controlled XML/DOM fixtures and the real sanitizer logic, not a browser renderer.

Actual nonempty previews in this Linux environment remain unavailable because the
existing trusted-system Arial requirement is unmet. Empty-scene preview generation
succeeds. No font substitution or renderer-gate change was made. Browser visual,
keyboard/focus/accessibility and Windows/native installed acceptance are deferred.
Existing installers are unchanged; this is a source increment, not full Goal completion.
Consult matching receipts for fresh counts and the retained baseline admission failures.

## Explicit isotope mass availability

The coordinated `/2` response admits paired null mass values only with a bounded,
graph-bound `massUnavailable` explanation for missing explicit isotope-table
entries. Either missing side makes both mass deltas unavailable. This does not
change isotope annotations, LogP applicability or identity/stale-result guards.
See [the source contract](ISOTOPE-MASS.md) for compatibility and limitations.
