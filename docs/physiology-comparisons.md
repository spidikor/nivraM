# Custom physiological pH comparisons

The physiological reference explorer now accepts a session-only custom comparison for each selected context. It does not fill a source evidence gap or change the molecular document. A user must choose a context and enter both endpoints; no numerical defaults are supplied.

The current supported comparison display domain is pH 0–14. This is a UI domain, not a claim that pH outside it is physically impossible. Endpoints are preserved as entered numeric values rather than rounded to the model's separate 0.1-pH sampling grid. Out-of-domain, nonfinite, blank, or reversed inputs are rejected while the preceding accepted comparison remains intact.

Distinct endpoints describe a `custom_band`. Equal endpoints describe a `custom_marker`, explicitly labeled “single pH value”; no interval or uncertainty is invented. Every accepted result identifies itself as user-entered, with unverified applicability to its selected context. It never inherits species, temperature, preparation, measurement method, source citations, or verified evidence status from a source preset. Source context and limitations remain separately visible, including “No verified numerical interval available” for an unsupported compartment.

Each context's accepted comparison is retained only while the window remains open. Switching contexts restores that context's accepted comparison; Remove comparison clears it. No comparison is written into a project, profile, browser storage, server, or external service. An invalid uncommitted entry is discarded when switching contexts. All user text is rendered through `textContent` or input values, never HTML interpolation.

The explorer preserves reported point estimates as points. Where a source supplies a central estimate and confidence interval, its text now includes both the estimate and exact interval endpoints, with the confidence level and statistical meaning.

## Evidence boundary

The pure tests in `tests/physiology-comparison.test.mjs` cover numerical validation, exact off-grid endpoints, explicit point/range identity, per-context and per-window isolation, immutability, retained source gaps, label limits, and source point/interval semantics. Those tests do not establish rendered layout, keyboard focus, screen-reader behavior, or native Windows integration.

This change provides the textual comparison control and a typed data contract for future plot overlays. Actual chart bands/markers, molecular population prediction, and scientific validation of those predictions remain unmet. No numerical source preset or literature claim was added or altered by this change.
