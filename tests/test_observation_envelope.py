"""Synthetic, in-memory envelope contracts; no host or persistence calls."""
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from nivram import observation_envelope as envelope
from nivram import supervision_observations as observations
from nivram import supervision_receipts as receipts


ATTEMPT = '01234567-89ab-4cde-8fab-0123456789ab'
OTHER_ATTEMPT = '11234567-89ab-4cde-8fab-0123456789ab'
SOURCE = 'b' * 64
BOUNDARY = 'primary_pre_cleanup'
SCHEMA = 'nivram-parent-io-observation-envelope/1'
TARGETS = ('stdin_write_completed', 'stdin_closed', 'stdout_eof', 'stderr_eof')
EVIDENCE_FIELDS = (
    'attempt_id', 'source_sha256', 'outcome', 'returncode',
    'ownership_confirmed', 'root_exit_confirmed', 'stdin_write_completed',
    'stdin_closed', 'stdout_eof', 'stderr_eof', 'output_drained', 'output_closed',
    'reader_threads_exited', 'cleanup_confirmed', 'remaining_descendants',
    'forced_cleanup',
)
MARKS = ('stdout_reader_entered', 'stderr_reader_entered', 'stdout_eof',
         'stderr_eof', 'stdin_writer_entered', 'stdin_write_completed',
         'stdin_close_completed')


def projection(mapped=TARGETS, *, origin='synthetic', outcome='returned', status='valid'):
    if status == 'valid':
        packet = {'available': True, 'status': status, 'writer_expected': True,
                  'stdout_limit': 4096, 'stdout_retained_bytes': 0,
                  **dict.fromkeys(MARKS, False)}
        for target in mapped:
            if target == 'stdin_write_completed':
                packet.update(stdin_writer_entered=True, stdin_write_completed=True)
            elif target == 'stdin_closed':
                packet.update(stdin_writer_entered=True, stdin_close_completed=True)
            elif target == 'stdout_eof':
                packet.update(stdout_reader_entered=True, stdout_eof=True)
            elif target == 'stderr_eof':
                packet.update(stderr_reader_entered=True, stderr_eof=True)
    else:
        packet = {'available': False, 'status': status,
                  **dict.fromkeys(('writer_expected', 'stdout_limit',
                                   'stdout_retained_bytes', *MARKS))}
    bound = observations.bind_parent_io(packet, attempt_id=ATTEMPT,
        source_sha256=SOURCE, origin=origin, boundary=BOUNDARY)
    return observations.project_parent_io(bound, expected_attempt_id=ATTEMPT,
        expected_source_sha256=SOURCE, outcome=outcome)


def document(mapped=TARGETS, *, origin='synthetic', outcome='returned', status='valid'):
    facts = dict.fromkeys(EVIDENCE_FIELDS)
    facts.update(attempt_id=ATTEMPT, source_sha256=SOURCE, outcome=outcome)
    facts.update({name: True for name in mapped})
    return {'schema': SCHEMA, 'origin': origin, 'boundary': BOUNDARY,
            'parent_io_status': status, 'mapped_fields': list(mapped),
            'evidence': facts}


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')


class EnvelopeTests(unittest.TestCase):
    def decode(self, data, **changes):
        args = dict(expected_attempt_id=ATTEMPT, expected_source_sha256=SOURCE,
                    expected_origin='synthetic', expected_boundary=BOUNDARY)
        args.update(changes)
        return envelope.decode_observation(data, **args)

    def test_constants_and_canonical_encoding_match_independent_document(self):
        self.assertEqual(envelope.SCHEMA, SCHEMA)
        self.assertEqual(envelope.MAX_ENVELOPE_BYTES, 8192)
        data = envelope.encode_observation(projection())
        self.assertIs(type(data), bytes)
        self.assertEqual(data, json_bytes(document()))
        self.assertTrue(data.endswith(b'\n'))
        self.assertFalse(data.endswith(b'\n\n'))
        self.assertEqual(set(json.loads(data)['evidence']), set(EVIDENCE_FIELDS))
        self.assertNotIn('schema', json.loads(data)['evidence'])

    def test_all_sixteen_pipe_subsets_roundtrip_without_adding_facts(self):
        for mask in range(16):
            mapped = tuple(name for bit, name in enumerate(TARGETS) if mask & (1 << bit))
            with self.subTest(mask=mask):
                original = projection(mapped)
                decoded = self.decode(envelope.encode_observation(original))
                self.assertEqual(decoded, original)
                self.assertEqual(decoded.mapped_fields, mapped)
                self.assertIs(type(decoded.mapped_fields), tuple)
                self.assertFalse(receipts.assess_recorded_evidence(
                    decoded.evidence).complete_recorded_evidence)
                self.assertFalse(receipts.assess_recorded_evidence(
                    decoded.evidence).normal_exit_recorded)

    def test_outcomes_origins_and_unknown_statuses_are_preserved(self):
        for origin in ('synthetic', 'host_reported'):
            for outcome in ('returned', 'timeout', 'cancelled', 'interrupted', 'error'):
                for status in ('valid', 'busy', 'unavailable'):
                    with self.subTest(origin=origin, outcome=outcome, status=status):
                        original = projection((), origin=origin, outcome=outcome, status=status)
                        decoded = self.decode(envelope.encode_observation(original),
                                              expected_origin=origin)
                        self.assertEqual(decoded, original)
                        self.assertEqual(decoded.origin, origin)
                        self.assertEqual(decoded.boundary, BOUNDARY)
                        self.assertEqual(decoded.parent_io_status, status)
                        self.assertEqual(decoded.evidence.outcome, outcome)

    def test_encoding_is_deterministic_and_decoding_produces_frozen_fresh_records(self):
        original = projection()
        data = envelope.encode_observation(original)
        self.assertEqual(envelope.encode_observation(projection()), data)
        decoded = self.decode(data)
        self.assertIsNot(decoded, original)
        self.assertIsNot(decoded.evidence, original.evidence)
        self.assertEqual(envelope.encode_observation(decoded), data)
        for target, field, value in ((decoded, 'origin', 'host_reported'),
                                     (decoded, 'mapped_fields', ()),
                                     (decoded.evidence, 'returncode', 0)):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(target, field, value)

    def test_old_receipt_encoding_remains_distinct_and_unchanged(self):
        wrapped = projection()
        expected = document()['evidence']
        expected['schema'] = 'nivram-fresh-supervision-terminal/1'
        old_bytes = receipts.encode_terminal(wrapped.evidence)
        self.assertEqual(old_bytes, json_bytes(expected))
        with self.assertRaises(receipts.TerminalFormatError):
            receipts.encode_terminal(wrapped)
        with self.assertRaises(envelope.EnvelopeFormatError):
            envelope.encode_observation(wrapped.evidence)
        with self.assertRaises(envelope.EnvelopeFormatError):
            self.decode(old_bytes)
        with self.assertRaises(receipts.TerminalFormatError):
            receipts.decode_terminal(envelope.encode_observation(wrapped),
                expected_attempt_id=ATTEMPT, expected_source_sha256=SOURCE)

    def test_encoder_requires_exact_projection_and_evidence_types(self):
        class ProjectionSubclass(observations.ParentIoProjection):
            pass

        class EvidenceSubclass(receipts.TerminalEvidence):
            pass

        original = projection()
        inputs = (None, {}, (), document(), json_bytes(document()), original.evidence,
                  ProjectionSubclass(**vars(original)),
                  replace(original, evidence=vars(original.evidence)),
                  replace(original, evidence=EvidenceSubclass(**vars(original.evidence))))
        for value in inputs:
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(value)

    def test_missing_or_extra_projection_instance_attributes_are_rejected(self):
        for name in ('evidence', 'origin', 'boundary', 'parent_io_status', 'mapped_fields'):
            value = projection()
            object.__delattr__(value, name)
            with self.subTest(missing=name):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(value)
        value = projection()
        object.__setattr__(value, 'private_payload', 'private-body-must-not-appear')
        with self.assertRaises(envelope.EnvelopeFormatError) as raised:
            envelope.encode_observation(value)
        self.assertNotIn('private-body-must-not-appear', str(raised.exception))

    def test_nonpipe_terminal_facts_cannot_enter_an_observation_envelope(self):
        excluded = ('returncode', 'ownership_confirmed', 'root_exit_confirmed',
                    'output_drained', 'output_closed', 'reader_threads_exited',
                    'cleanup_confirmed', 'remaining_descendants', 'forced_cleanup')
        for field in excluded:
            for fact in (False, True, 0):
                with self.subTest(field=field, fact=fact):
                    value = projection()
                    object.__setattr__(value.evidence, field, fact)
                    with self.assertRaises(envelope.EnvelopeFormatError):
                        envelope.encode_observation(value)
                    data = document()
                    data['evidence'][field] = fact
                    with self.assertRaises(envelope.EnvelopeFormatError):
                        self.decode(json_bytes(data))

    def test_pipe_facts_require_true_or_none_without_numeric_or_truthy_coercion(self):
        for field in TARGETS:
            for invalid in (False, 0, 1, 'true', [], {}):
                with self.subTest(field=field, invalid_type=type(invalid).__name__):
                    value = projection()
                    object.__setattr__(value.evidence, field, invalid)
                    with self.assertRaises(envelope.EnvelopeFormatError):
                        envelope.encode_observation(value)
                    data = document()
                    data['evidence'][field] = invalid
                    with self.assertRaises(envelope.EnvelopeFormatError):
                        self.decode(json_bytes(data))

    def test_mapping_must_match_true_facts_once_in_canonical_order(self):
        changes = ((), TARGETS[:-1], TARGETS + ('stdout_eof',),
                   tuple(reversed(TARGETS)), TARGETS + ('output_drained',),
                   ('private-field-must-not-appear',))
        for mapped in changes:
            with self.subTest(mapped=mapped):
                value = replace(projection(), mapped_fields=mapped)
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(value)
                data = document()
                data['mapped_fields'] = list(mapped)
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))
        with self.assertRaises(envelope.EnvelopeFormatError):
            envelope.encode_observation(replace(projection(), mapped_fields=list(TARGETS)))
        with self.assertRaises(envelope.EnvelopeFormatError):
            envelope.encode_observation(replace(projection(()), mapped_fields=TARGETS))

    def test_busy_and_unavailable_cannot_claim_mapped_facts(self):
        for status in ('busy', 'unavailable'):
            with self.subTest(status=status):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(replace(projection(), parent_io_status=status))
                data = document(status=status)
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))

    def test_invalid_labels_statuses_and_outcomes_are_rejected_on_both_paths(self):
        for field, invalid in (
                ('origin', 'private-label-must-not-appear'), ('origin', True),
                ('boundary', 'post_cleanup'), ('boundary', None),
                ('parent_io_status', None), ('parent_io_status', True),
                ('parent_io_status', 'complete')):
            with self.subTest(field=field, value_type=type(invalid).__name__):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(replace(projection(), **{field: invalid}))
                data = document()
                data[field] = invalid
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))
        for invalid in ('complete', '', None, True, 0):
            with self.subTest(outcome_type=type(invalid).__name__):
                value = projection()
                object.__setattr__(value.evidence, 'outcome', invalid)
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(value)
                data = document()
                data['evidence']['outcome'] = invalid
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))

    def test_expected_bindings_and_labels_are_mandatory_exact_constraints(self):
        data = json_bytes(document())
        for change in ({'expected_attempt_id': OTHER_ATTEMPT},
                       {'expected_source_sha256': 'a' * 64},
                       {'expected_origin': 'host_reported'},
                       {'expected_boundary': 'post_cleanup'},
                       {'expected_attempt_id': True},
                       {'expected_source_sha256': 'B' * 64},
                       {'expected_origin': None}, {'expected_boundary': None}):
            with self.subTest(field=next(iter(change))):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(data, **change)
        for field, other in (('attempt_id', OTHER_ATTEMPT), ('source_sha256', 'a' * 64)):
            changed = document()
            changed['evidence'][field] = other
            with self.subTest(payload_field=field):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(changed))

    def test_unsupported_schemas_and_incomplete_json_objects_are_rejected(self):
        for schema in ('nivram-parent-io-observation-envelope/2',
                       'nivram-fresh-supervision-terminal/1', None, True, 1):
            data = document()
            data['schema'] = schema
            with self.subTest(schema=schema):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))
        for name in document():
            data = document()
            del data[name]
            with self.subTest(missing_outer=name):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))
        for name in EVIDENCE_FIELDS:
            data = document()
            del data['evidence'][name]
            with self.subTest(missing_nested=name):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))

    def test_unknown_private_fields_and_nested_old_schema_are_rejected(self):
        for level in ('outer', 'evidence'):
            data = document()
            target = data if level == 'outer' else data['evidence']
            target['private_payload'] = 'private-body-must-not-appear'
            with self.subTest(extra_level=level):
                with self.assertRaises(envelope.EnvelopeFormatError) as raised:
                    self.decode(json_bytes(data))
                self.assertNotIn('private-body-must-not-appear', str(raised.exception))
        data = document()
        data['evidence']['schema'] = 'nivram-fresh-supervision-terminal/1'
        with self.assertRaises(envelope.EnvelopeFormatError):
            self.decode(json_bytes(data))

    def test_decoder_enforces_exact_bytes_and_inclusive_size_limit(self):
        class BytesSubclass(bytes):
            pass

        valid = json_bytes(document())
        for value in (None, valid.decode('utf-8'), bytearray(valid), memoryview(valid),
                      BytesSubclass(valid), b'', b' ' * 8193):
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(value)
        padded = valid + b' ' * (8192 - len(valid))
        self.assertEqual(self.decode(padded), projection())
        with self.assertRaises(envelope.EnvelopeFormatError):
            self.decode(padded + b' ')

    def test_invalid_utf8_malformed_json_and_excessive_nesting_are_rejected(self):
        valid = json_bytes(document())
        for value in (b'\xff', b'{"private-body-must-not-appear":',
                      b' \n', valid + valid, valid + b'private-trailer'):
            with self.subTest(length=len(value)):
                with self.assertRaises(envelope.EnvelopeFormatError) as raised:
                    self.decode(value)
                self.assertEqual(str(raised.exception), 'Invalid observation document.')

        # A higher process recursion limit can defer rejection to shape validation.
        with self.assertRaises(envelope.EnvelopeFormatError):
            self.decode(b'[' * 1100 + b'null' + b']' * 1100)

    def test_duplicate_keys_are_rejected_at_outer_and_nested_levels(self):
        valid = json_bytes(document())
        for token in (b'"origin":"synthetic"', b'"outcome":"returned"',
                      b'"stdout_eof":true'):
            duplicate = valid.replace(token, token + b',' + token)
            self.assertNotEqual(duplicate, valid)
            with self.subTest(token=token):
                with self.assertRaises(envelope.EnvelopeFormatError) as raised:
                    self.decode(duplicate)
                self.assertEqual(str(raised.exception), 'Invalid observation document.')

    def test_every_numeric_json_form_and_nonfinite_token_is_rejected(self):
        valid = json_bytes(document())
        for token in (b'0', b'1', b'-1', b'1.0', b'1e2', b'1e999', b'NaN',
                      b'Infinity', b'-Infinity', b'9' * 2000):
            data = valid.replace(b'"returncode":null', b'"returncode":' + token)
            self.assertNotEqual(data, valid)
            with self.subTest(length=len(token), prefix=token[:8]):
                with self.assertRaises(envelope.EnvelopeFormatError) as raised:
                    self.decode(data)
                self.assertEqual(str(raised.exception), 'Invalid observation document.')

    def test_json_container_shapes_are_not_coerced(self):
        for value in (None, True, 'private-body-must-not-appear', [], document()['evidence']):
            with self.subTest(outer_type=type(value).__name__):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(value))
        for field, value in (('evidence', []), ('evidence', None),
                             ('mapped_fields', {}), ('mapped_fields', None),
                             ('mapped_fields', 'stdout_eof')):
            data = document()
            data[field] = value
            with self.subTest(field=field, value_type=type(value).__name__):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))

    def test_forged_evidence_identities_fail_before_serialization(self):
        for field, invalid in (('attempt_id', 'private-identity-must-not-appear'),
                               ('attempt_id', None), ('attempt_id', True),
                               ('source_sha256', 'B' * 64),
                               ('source_sha256', 'g' * 64),
                               ('source_sha256', None)):
            with self.subTest(field=field, value_type=type(invalid).__name__):
                value = projection()
                object.__setattr__(value.evidence, field, invalid)
                with self.assertRaises(envelope.EnvelopeFormatError) as raised:
                    envelope.encode_observation(value)
                self.assertNotIn('private-identity-must-not-appear', str(raised.exception))
                data = document()
                data['evidence'][field] = invalid
                with self.assertRaises(envelope.EnvelopeFormatError):
                    self.decode(json_bytes(data))

    def test_custom_value_and_collection_hooks_are_never_invoked(self):
        calls = []

        def trap(*args, **kwargs):
            calls.append('invoked')
            raise AssertionError('private-hook-body-must-not-appear')

        class HookValue:
            __bool__ = __str__ = __repr__ = __eq__ = __len__ = __iter__ = trap
            __deepcopy__ = trap

        class HookTuple(tuple):
            __iter__ = __len__ = __repr__ = trap

        class HookText(str):
            __str__ = __repr__ = __eq__ = __len__ = trap

        for field in EVIDENCE_FIELDS:
            value = projection()
            object.__setattr__(value.evidence, field, HookValue())
            with self.subTest(evidence_field=field):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(value)
        for field in ('origin', 'boundary', 'parent_io_status', 'mapped_fields'):
            value = replace(projection(), **{field: HookValue()})
            with self.subTest(projection_field=field):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(value)
        for mapped in (HookTuple(TARGETS), (HookValue(),), (HookText('stdout_eof'),)):
            with self.assertRaises(envelope.EnvelopeFormatError):
                envelope.encode_observation(replace(projection(), mapped_fields=mapped))
        with self.assertRaises(envelope.EnvelopeFormatError):
            self.decode(HookValue())
        self.assertEqual(calls, [])

    def test_custom_record_hooks_and_shadowed_validators_are_never_invoked(self):
        calls = []

        def trap(*args, **kwargs):
            calls.append('invoked')
            raise AssertionError('private-record-hook-must-not-appear')

        class HookProjection(observations.ParentIoProjection):
            __getattribute__ = trap

        class HookEvidence(receipts.TerminalEvidence):
            __getattribute__ = trap

        for value in (object.__new__(HookProjection),
                      replace(projection(), evidence=object.__new__(HookEvidence))):
            with self.assertRaises(envelope.EnvelopeFormatError):
                envelope.encode_observation(value)
        for level in ('outer', 'evidence'):
            value = projection()
            target = value if level == 'outer' else value.evidence
            object.__setattr__(target, '__post_init__', trap)
            with self.assertRaises(envelope.EnvelopeFormatError):
                envelope.encode_observation(value)
        self.assertEqual(calls, [])

    def test_missing_or_extra_evidence_instance_attributes_are_rejected(self):
        for name in EVIDENCE_FIELDS:
            value = projection()
            object.__delattr__(value.evidence, name)
            with self.subTest(missing=name):
                with self.assertRaises(envelope.EnvelopeFormatError):
                    envelope.encode_observation(value)
        value = projection()
        object.__setattr__(value.evidence, 'private_payload', 'private-body-must-not-appear')
        with self.assertRaises(envelope.EnvelopeFormatError) as raised:
            envelope.encode_observation(value)
        self.assertNotIn('private-body-must-not-appear', str(raised.exception))
