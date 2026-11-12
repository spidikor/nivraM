# PNG recognition structure consistency

Recognition now offers a proposal only when its MOL depiction and its separate
SMILES describe the same supported structure. A matching result retains the
MOL's 2D coordinates. A contradictory result is rejected instead of choosing
the SMILES and generating a replacement layout.

Unresolved atom aliases are also rejected, including aliases on ordinary atoms.
This validator has no verified abbreviation-expansion dictionary. It therefore
cannot establish that an abbreviated depiction matches an expanded SMILES.
Queries, unsupported stereochemistry, invalid valence and existing size limits
continue to reject unsupported input.

The comparison uses canonical isomeric SMILES after normalizing ordinary
explicit hydrogens. Equivalent atom ordering and aromatic/Kekule notation can
match; charges, isotopes, atom maps, radicals and supported specified
stereochemistry remain part of identity. Matching the two outputs checks their
consistency. It does not establish that either correctly reads the image.

## After a rejection

The message identifies a conflict or an unresolved abbreviation and offers a
clearer crop, a fully drawn group, or the intended SMILES/2D MOL text. No new
optical proposal is published and the main drawing and original PNG stay
unchanged. Starting a new recognition attempt clears the review draft and
revokes its previous acknowledgement; this increment does not promise to
restore a draft after that attempt fails.

The existing supplied-structure workflow remains available. Prepare the intended
structure, compare it with the crop, correct it, then acknowledge and accept it.
Its provenance identifies user-supplied text rather than optical recognition.
The existing draft and main-editor Undo behavior is unchanged.

## Validation boundary

Native chemistry remains inside the resource-bounded validation child. Only
two fixed actionable failure codes are accepted by the host, in an exact JSON
envelope bound to the retained crop hash and the expected failure exit code.
The host uses its own fixed messages. Unknown codes, malformed messages, stale
hashes and malformed envelopes produce the existing generic error. Cancellation
and the shared deadline are rechecked before an actionable result is returned.

New live optical proposals must report preserved MOL coordinates. Previously
saved project records are not migrated or rewritten; their historical provenance
remains readable under the existing project schema.

## Evidence and limits

The delivery receipts identify the exact source, focused Python results and
packaged-worker checks. Fixtures cover contradictory chemistry, equivalent
representations, alias rejection, crop-bound messages, cancellation/deadline
handling and preparing a supplied structure after rejection. The packaged
worker checks use synthetic recognition results, not image inference.

The MolScribe checkpoint is still unavailable. Actual model loading, image
recognition accuracy and model latency remain unverified. The installer is
unsigned; building and launching its bundled application does not install it
or constitute a new antivirus scan. AdGuard settings are unchanged.

The preceding resource-limit delivery recorded one broader-suite CPU-accounting
assertion failure, also reproduced on its preceding source. Those historical
receipts retain their original source identities. This focused increment does
not claim to resolve that assertion or explain the historical memory-peak
counter discrepancy.
