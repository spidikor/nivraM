# Bounded MOL isotope and atom-map fidelity

The import boundary prepares a transient native input string before RDKit parsing. Caller input and recognition provenance are unchanged. Ordinary import retains `sanitize=True, removeHs=False`; optical import retains its existing unsanitized parse, support check, sanitization and identity checks. No parsed atom is relabeled and no MOL serialization roundtrip is used.

## Supported scope

- V2000 atom-block mass difference is an ASCII integer from -3 through 4, with blank meaning the default. A validated leading plus is replaced by a space within the original field.
- Actual M ISO records validate count, atom references, complete fixed-width shape and mass integers before use. Absolute masses are bounded to 0–400 by the application. Only proven lossy mass signs are rewritten; already-working signed count/reference fields are preserved.
- A nonempty effective M ISO record supersedes all legacy mass-difference fields in the transient string, including those on atoms not named by the property. D/T symbols are not mass-difference fields and are preserved. There is no inferred periodic-table override.
- V3000 MASS accepts bounded integer isotope labels. Leading plus is removed only at that assignment's physical character offset, including fully quoted assignment tokens and continued records. Fractional, quoted numeric, list, negative and malformed values reject rather than truncate. This integer-only application contract is narrower than the format's general absolute-atomic-weight wording.
- V2000 map fields validate nonnegative integer spelling. Positive signs are replaced by spaces. Validated zero fields, including blank or signed zero, become exactly `  0`, the native spelling for an absent map property. Nonzero values remain mapped. V3000 maps are validated to 0–999999 but already-working signs are left unchanged.

Only actual record/token scopes are interpreted. Headers, A/G payloads, S SKP payloads and text beyond end markers are not isotope declarations. Existing application rejection of trailing records/metadata remains. Physical LF/CRLF endings, coordinates, explicit isotopic H, charge, radical, stereo, bond and component semantics retain their previous treatment. Existing charge/radical/annotation guards are not relaxed.

## Compatibility choices and limits

Zero-entry M ISO records remain no-ops; a later empty record cannot undo a real declaration. Explicit mass zero retains natural-abundance-label compatibility, which is distinct from an explicit principal isotope such as carbon-12. Repeated declarations validate every entry and preserve the pinned native last assignment; distinct records accumulate. Neither zero handling nor duplicate last-wins behavior is claimed as a newly proven normative CTfile rule.

The native legacy mass base uses the most common isotope. This change does not certify its equivalence to BIOVIA's PTABLE.DAT for every element. Independently checked legacy examples use C/N/O where the relevant reference identities agree. The isotope range is an application limit, not proof of physical nuclide existence. Scientific isotope validity, spin multiplicity and all-format CTfile conformance are not established.

## Evidence and verification

Expected chemical identities were selected independently of the MOL parser being repaired. Regression fixtures retain the original inputs with explicit correction overlays for three fixture-authoring mistakes and two existing trailing-record rejection policies. Four additional hydrogen fixtures explicitly supply the MASS attributes missing from the original two test inputs. Original failed expectations are retained in the development evidence, not silently regenerated from repaired output.

Tests cover ordinary import, optical candidate validation and real isolated Linux validator processes; wrong optical identities must reject. Full atom descriptors, topology/order/stereo, signed/unsigned complete graphs, coordinates, exact transient bytes, line endings and preparation idempotence are checked. Transaction/cancellation tests retain the existing document-adoption gates. These finite regressions do not constitute exhaustive property testing, private-corpus coverage, Windows process containment, browser qualification or model inference validation.

Primary references:

- [BIOVIA Chemistry 2026 CTFile Formats](https://www.3ds.com/assets/invest/2026-01/biovia_chemistry_2026_ctfile_formats.pdf), printed pp.7,9–10,65,68,70.
- [ANSI X3.9-1978 via NIST FIPS 69-1](https://nvlpubs.nist.gov/nistpubs/Legacy/FIPS/fipspub69-1.pdf), sections4.3.1 and13.5.9.1, for optional-sign integer input.
- [RDKit 2025.09.4 pinned parser source](https://github.com/rdkit/rdkit/blob/9b69dd2a400b43a2ac5a311fef786628c257f4b1/Code/GraphMol/FileParsers/MolFileParser.cpp), implementation evidence, not normative isotope semantics.
