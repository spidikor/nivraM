# Read-only stereochemistry inspector

The Structure properties sidebar now has an explicit **Inspect R/S and E/Z**
action for the current 2D document. It sends only to the local authenticated API,
with the existing Host/Origin/token admission rules. No external service, cleanup,
3D generation, stereoisomer enumeration, history entry, save or molecular edit is
performed. It does not alter image exports or embed inspector results in files.

The backend copies the graph and applies its existing chemical validation,
including explicit wedge/hash interpretation and double-bond geometry checks.
Original IDs, coordinates, atom order and bond order remain untouched in the
document. IDs and 1-based graph positions identify the result rows. Selecting a
row highlights that atom or bond using ordinary editor selection only; during an
editor operation or after a document revision change, selection is not applied.

RDKit `FindPotentialStereo(cleanIt=False, flagPossible=True)` identifies potential
sites. `rdCIPLabeler.AssignCIPLabels` labels already specified configurations, with
a 100,000-recursion-iteration budget. Atom-map labels are removed only from this
analysis copy because they are not chemical or isotope distinctions. Legacy cached
CIP labels are removed before the CIP calculation. Tetrahedral **R/S** and ordinary
double-bond **E/Z** labels are displayed. Unspecified or explicitly unknown sites
receive **R/S?** or **E/Z?**, never a chosen isomer. This does not assert a racemate.

Unsupported perceived types are counted separately. Cumulated double bonds are
listed as unsupported, not ordinary E/Z sites. Pseudoasymmetric lowercase r/s and
other unhandled descriptors stay unsupported without conversion to uppercase.
Axial, cumulene, non-tetrahedral and enhanced stereochemistry are not comprehensively
supported by this inspector or the underlying document model. No detected sites
means only that none were found within this engine's supported scope, not that a
molecule is achiral or fully validated. The response identifies the actual RDKit
version. This is engine-derived interpretation, not experimental identity evidence.

Existing graph normalization can discard inapplicable declarations. The inspector
warns if a supplied atom chiral tag or bond stereo flag is cleared by validation;
it does not adopt a cleaned graph. Invalid graphs and incomplete engine calculations
fail without returning an empty successful report. A single unsupported descriptor
does not fabricate a descriptor for that site or suppress supported other sites.

Results are bound to one random request ID and document revision. The browser
validates the closed response schema, graph IDs/indexes, kind/label/status/reason
combinations and bounds. Text uses textContent, not HTML. A subsequent document
revision clears all rows and cancels waiting work. Replace, cancel, timeout, close
review and page exit discard stale responses. Cancellation stops the client's wait;
it does not claim to interrupt a synchronous RDKit call already running on the
local server. Existing graph size limits and the CIP iteration cap bound input
and that recursion, not a proven wall-time/memory budget for all engine operations.

## Qualification

Focused backend chemistry and actual local HTTP tests run with pinned Linux RDKit
2025.09.4, NumPy 2.3.5 and Pillow 12.3.0. They do not invoke the drawing renderer and
therefore do not require Arial. JavaScript transport, validation and lifecycle tests
use controlled EventTarget/DOM objects, not a real browser. Independent review found
and reproduced the cumulene distinction above; it is covered by a regression test.
Consult the exact checkpoint receipts for commands, versions, counts and failures.

Real browser layout, keyboard/focus/accessibility, Windows WebView2/installed-app
acceptance, renderer qualification requiring trusted-system Arial, and broader
chemical-corpus validation remain separate and unperformed here. Source changes do
not update a previous installer. The full project Goal is not complete.
