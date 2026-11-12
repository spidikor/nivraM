# Observation envelope, v008 — pure source-level codec

This newly authored increment closes v007's demonstrated serialization gap without changing its adapter or the existing terminal receipt `/1` API/bytes. It adds `nivram/observation_envelope.py`, a dedicated offline test file and this document. All 455 v007 source files are preserved. No production caller, file writer, native path or admission decision is connected.

## Exact boundary and APIs

`encode_observation(projection: ParentIoProjection) -> bytes` validates a caller-supplied projection and emits deterministic compact, sorted-key UTF-8 JSON with one trailing LF. The encoder does not trust a frozen dataclass: it checks the exact record types and exact builtin instance dictionaries of both projection and nested `TerminalEvidence`. Missing or extra/private attributes reject before serialization. It does not invoke supplied instance validators, custom mappings, `asdict` or deep-copy hooks. This is not protection against arbitrary hostile Python code mutating classes or concurrently changing inputs; callers must supply stable records.

`decode_observation(data: bytes, *, expected_attempt_id: str, expected_source_sha256: str, expected_origin: str, expected_boundary: str) -> ParentIoProjection` validates and returns labeled partial evidence. All expected bindings are explicit. It supplies no generic receipt-success result, write acknowledgement, host-event authentication, cleanup qualification or execution admission.

The new schema is `nivram-parent-io-observation-envelope/1`. Its six exact top-level fields are `schema`, `origin`, `boundary`, `parent_io_status`, `mapped_fields` and `evidence`. Nested `evidence` contains all 16 exact fields of the inspected `TerminalEvidence` contract, including attempt ID, source SHA-256 and declared outcome. It has no nested old receipt schema marker. Unsupported versions, bare terminal records, old `/1` receipt bytes, omitted provenance and unknown/private fields reject; none are silently migrated or stripped.

## Preserved meaning and consistency rules

- Origin remains exactly `synthetic` or caller-declared `host_reported`. Neither encoding nor decoding can authenticate a host report. Fixture cases using either label are synthetic test evidence.
- Boundary remains exactly `primary_pre_cleanup`. This is a declaration, not proof that capture was frozen, fresh or atomic.
- Packet status remains `valid`, `busy` or `unavailable`.
- The only possible affirmative terminal facts are `stdin_write_completed`, `stdin_closed`, `stdout_eof` and `stderr_eof`. Each is exactly `True` or `None`; `False` is rejected because the v007 projection represents unobserved marks as unknown.
- Every other terminal fact, including return code, ownership, root exit, output drain/closure, reader exit, cleanup, descendants and forced-cleanup history, must remain `None`. Supplying even a zero return code or descendant count is unsupported evidence.
- `mapped_fields` must match the affirmative facts exactly, in the canonical order shown above. In-memory input uses an exact tuple; JSON uses an exact list. Duplicate, reordered, unknown, missing or contradictory mappings reject. Busy/unavailable status permits no affirmative facts.
- Declared outcomes round-trip unchanged. Even `returned` with every pipe fact affirmative remains incomplete and cannot describe a normal exit under the existing assessment.

The envelope preserves the projection, not the original parent-I/O packet. Reader-entry marks, writer expectation and retained-byte counts were not retained by v007's projection and are not fabricated here. No source or runtime authenticity, global identity uniqueness, or corroborating host observation is supplied by these bindings.

## Parsing, consumption and finite tests

Input must be exact `bytes`, nonempty and at most 8,192 bytes. The JSON parser rejects malformed UTF-8/JSON, duplicate keys at every object level, nonfinite constants and all numeric values. Exact schema/scalar checks reject nested structures where scalars belong. Excessive nesting is rejected as an invalid document when the parser reports recursion failure. Encoding normalizes valid JSON whitespace/object-key order on round trip; it does not reorder inconsistent mapping lists. Diagnostics contain fixed messages without private input text.

The returned value keeps the provenance wrapper. Passing it directly to the old terminal encoder still rejects. Deliberately extracting `.evidence` and using the old serializer still loses provenance; that unsupported path is not repaired by changing the old API. Consumers needing labeled observations must use this codec and retain the returned wrapper. There is no filesystem persistence bridge in this increment, and successful decoding cannot reconstruct a previous writer acknowledgement.

Offline tests must cover valid partial projections and labels, deterministic encoding, strict expected bindings, public/forged dataclasses, extra attributes and custom hooks, impossible mappings and evidence upgrades, parser size/shape/duplicate/version limits, stripped provenance, unchanged old receipt encoding and non-normal assessments after round trip. Only synthetic, in-memory operations are required. No native application, worker, subprocess, Windows call, installation, provider or filesystem writer is exercised.

## Remaining host dependency

Actual production use still needs a separately reviewed consumer and genuine retained-handle/runtime bindings with independent observations for ownership, interruption, process/owner completion, output drain/closure, descendants and cleanup. This codec cannot manufacture those observations or release their approvals. Historical staging recovery and all native, security, reference, uncertain-replay and publication holds remain unchanged. Parent source-level acceptance and runtime release remain separate.
