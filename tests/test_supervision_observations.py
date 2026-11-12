"""Synthetic offline contracts; no process, worker or native imports.

ParentIoCapture supplies observations, never terminal completion authority.
"""
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
import tempfile
import unittest
import uuid

from nivram.parent_io import ParentIoCapture
from nivram import supervision_observations as observations
from nivram import supervision_receipts as receipts


SOURCE = 'b' * 64
BOUNDARY = 'primary_pre_cleanup'
MARKS = ('stdout_reader_entered', 'stderr_reader_entered', 'stdout_eof',
         'stderr_eof', 'stdin_writer_entered', 'stdin_write_completed',
         'stdin_close_completed')
FACTS = ('writer_expected', 'stdout_limit', 'stdout_retained_bytes', *MARKS)
TARGETS = ('stdin_write_completed', 'stdin_closed', 'stdout_eof', 'stderr_eof')


def packet(**changes):
    value = {'available': True, 'status': 'valid', 'writer_expected': True,
             'stdout_limit': 4096, 'stdout_retained_bytes': 0,
             **{name: False for name in MARKS}}
    value.update(changes)
    return value


def all_true():
    return packet(**{name: True for name in MARKS}, stdout_retained_bytes=128)


def unavailable(status):
    return {'available': False, 'status': status, **dict.fromkeys(FACTS)}


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.attempt = str(uuid.uuid4())

    def bind(self, value, **changes):
        args = dict(attempt_id=self.attempt, source_sha256=SOURCE,
                    origin='synthetic', boundary=BOUNDARY)
        args.update(changes)
        return observations.bind_parent_io(value, **args)

    def project(self, observation, **changes):
        args = dict(expected_attempt_id=self.attempt, expected_source_sha256=SOURCE,
                    outcome='returned')
        args.update(changes)
        return observations.project_parent_io(observation, **args)

    def assert_only_facts(self, projection, expected):
        self.assertIs(type(projection.evidence), receipts.TerminalEvidence)
        self.assertIs(type(projection.mapped_fields), tuple)
        self.assertEqual(set(projection.mapped_fields), set(expected))
        self.assertEqual(len(projection.mapped_fields), len(expected))
        for field in fields(receipts.TerminalEvidence):
            if field.name in ('attempt_id', 'source_sha256', 'outcome'):
                continue
            with self.subTest(field=field.name):
                self.assertIs(getattr(projection.evidence, field.name),
                              True if field.name in expected else None)

    def test_four_affirmative_marks_remain_incomplete_and_non_normal(self):
        self.assertEqual(observations.PRIMARY_BOUNDARY, BOUNDARY)
        bound = self.bind(all_true())
        result = self.project(bound)
        self.assert_only_facts(result, TARGETS)
        self.assertEqual(result.evidence.attempt_id, self.attempt)
        self.assertEqual(result.evidence.source_sha256, SOURCE)
        self.assertEqual(result.origin, 'synthetic')
        self.assertEqual(result.boundary, BOUNDARY)
        self.assertEqual(result.parent_io_status, 'valid')
        assessment = receipts.assess_recorded_evidence(result.evidence)
        self.assertFalse(assessment.complete_recorded_evidence)
        self.assertFalse(assessment.normal_exit_recorded)

    def test_false_marks_and_entered_readers_are_unknown_terminal_facts(self):
        for value in (packet(), packet(stdout_reader_entered=True,
                                       stderr_reader_entered=True,
                                       stdin_writer_entered=True,
                                       stdout_retained_bytes=4096)):
            with self.subTest(packet=value):
                self.assert_only_facts(self.project(self.bind(value)), ())

    def test_each_affirmative_mark_maps_only_its_own_fact(self):
        cases = (
            ({'stdin_writer_entered': True, 'stdin_write_completed': True},
             'stdin_write_completed'),
            ({'stdin_writer_entered': True, 'stdin_close_completed': True},
             'stdin_closed'),
            ({'stdout_reader_entered': True, 'stdout_eof': True}, 'stdout_eof'),
            ({'stderr_reader_entered': True, 'stderr_eof': True}, 'stderr_eof'),
        )
        for change, expected in cases:
            with self.subTest(target=expected):
                self.assert_only_facts(self.project(self.bind(packet(**change))),
                                       (expected,))

    def test_closed_stdin_without_completed_write_preserves_missing_input(self):
        result = self.project(self.bind(packet(stdin_writer_entered=True,
                                               stdin_close_completed=True)))
        self.assert_only_facts(result, ('stdin_closed',))
        self.assertIn('stdin_write_completed',
                      receipts.assess_recorded_evidence(result.evidence).incomplete)

    def test_no_expected_writer_does_not_infer_input_completion(self):
        result = self.project(self.bind(packet(writer_expected=False,
            stdout_reader_entered=True, stdout_eof=True,
            stderr_reader_entered=True, stderr_eof=True)))
        self.assert_only_facts(result, ('stdout_eof', 'stderr_eof'))

    def test_busy_and_unavailable_keep_status_and_unknown_facts(self):
        for status in ('busy', 'unavailable'):
            with self.subTest(status=status):
                result = self.project(self.bind(unavailable(status)))
                self.assert_only_facts(result, ())
                self.assertEqual(result.parent_io_status, status)

    def test_each_outcome_and_origin_is_preserved_without_completion(self):
        for origin in ('synthetic', 'host_reported'):
            for outcome in ('returned', 'timeout', 'cancelled', 'interrupted', 'error'):
                with self.subTest(origin=origin, outcome=outcome):
                    result = self.project(self.bind(all_true(), origin=origin),
                                          outcome=outcome)
                    self.assertEqual(result.evidence.outcome, outcome)
                    self.assertEqual(result.origin, origin)
                    self.assertEqual(result.boundary, BOUNDARY)
                    self.assertFalse(receipts.assess_recorded_evidence(
                        result.evidence).normal_exit_recorded)

    def test_caller_mutation_cannot_change_bound_observation(self):
        value = all_true()
        bound = self.bind(value)
        before = self.project(bound)
        value.clear()
        value.update({'private_payload': 'secret-marker', 'available': False})
        self.assertEqual(self.project(bound), before)

    def test_fresh_projection_never_reuses_or_merges_terminal_evidence(self):
        bound = self.bind(all_true())
        first = self.project(bound)
        second = self.project(bound, outcome='cancelled')
        self.assertIsNot(first, second)
        self.assertIsNot(first.evidence, second.evidence)
        self.assertEqual(first.evidence.outcome, 'returned')
        self.assertEqual(second.evidence.outcome, 'cancelled')
        self.assert_only_facts(second, TARGETS)
        third = self.project(self.bind(packet()))
        self.assert_only_facts(third, ())

    def test_factory_outputs_and_labels_are_frozen(self):
        bound = self.bind(all_true())
        result = self.project(bound)
        for target, name, replacement in (
                (bound, 'origin', 'host_reported'),
                (bound, 'boundary', 'post_cleanup'),
                (bound, 'attempt_id', str(uuid.uuid4())),
                (result, 'origin', 'host_reported'),
                (result, 'boundary', 'post_cleanup'),
                (result, 'parent_io_status', 'busy'),
                (result, 'mapped_fields', ()),
                (result.evidence, 'returncode', 0)):
            with self.subTest(field=name, type=type(target).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(target, name, replacement)

    def test_real_capture_freeze_preserves_synthetic_primary_boundary(self):
        capture = ParentIoCapture()
        capture.configure(writer_expected=True, stdout_limit=4096)
        capture.mark('stdin_writer_entered')
        capture.mark('stdin_close_completed')
        capture.mark('stdout_reader_entered')
        capture.retained(31)
        capture.mark('stdout_eof')
        earlier = capture.snapshot()
        bound = self.bind(capture.freeze())
        capture.mark('stdin_write_completed')
        capture.mark('stderr_reader_entered')
        capture.mark('stderr_eof')
        earlier['stdin_write_completed'] = True
        earlier['stdout_eof'] = False
        result = self.project(bound)
        self.assert_only_facts(result, ('stdin_closed', 'stdout_eof'))
        self.assertEqual(result.origin, 'synthetic')
        self.assertEqual(result.boundary, BOUNDARY)
        self.assertEqual(self.project(self.bind(capture.snapshot())), result)

    def test_real_contended_freeze_remains_busy_without_starting_threads(self):
        capture = ParentIoCapture()
        capture.configure(writer_expected=True, stdout_limit=4096)
        self.assertTrue(capture._lock.acquire(blocking=False))
        try:
            frozen = capture.freeze()
        finally:
            capture._lock.release()
        capture.mark('stdin_writer_entered')
        capture.mark('stdin_write_completed')
        self.assertEqual(frozen, unavailable('busy'))
        self.assertEqual(capture.snapshot(), unavailable('busy'))
        result = self.project(self.bind(frozen))
        self.assertEqual(result.parent_io_status, 'busy')
        self.assert_only_facts(result, ())

    def test_unconfigured_capture_is_explicitly_unavailable(self):
        result = self.project(self.bind(ParentIoCapture().freeze()))
        self.assertEqual(result.parent_io_status, 'unavailable')
        self.assert_only_facts(result, ())

    def test_wrapper_cannot_be_sent_directly_to_existing_receipt_persistence(self):
        bound = self.bind(all_true())
        result = self.project(bound)
        with tempfile.TemporaryDirectory(prefix='nivram-observation-test-') as directory:
            for wrapper in (bound, result):
                with self.subTest(type=type(wrapper).__name__):
                    with self.assertRaises(receipts.TerminalFormatError):
                        receipts.encode_terminal(wrapper)
                    with self.assertRaises(receipts.TerminalFormatError):
                        receipts.ReceiptWrite(directory, wrapper)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_attempt_or_source_mismatch_is_rejected(self):
        bound = self.bind(all_true())
        for change in ({'expected_attempt_id': str(uuid.uuid4())},
                       {'expected_source_sha256': 'a' * 64}):
            with self.subTest(field=next(iter(change))):
                with self.assertRaises(observations.ObservationFormatError):
                    self.project(bound, **change)

    def test_non_observation_inputs_cannot_be_projected(self):
        bound = self.bind(all_true())
        for value in (None, all_true(), (), self.project(bound),
                      receipts.TerminalEvidence(self.attempt, SOURCE, 'returned')):
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(observations.ObservationFormatError):
                    self.project(value)

    def test_invalid_labels_use_fixed_private_safe_diagnostics(self):
        private = 'private-value-must-not-appear'
        for field, changes, message in (
                ('origin', {'origin': private}, 'Invalid observation origin.'),
                ('origin', {'origin': True}, 'Invalid observation origin.'),
                ('origin', {'origin': None}, 'Invalid observation origin.'),
                ('boundary', {'boundary': private}, 'Invalid observation boundary.'),
                ('boundary', {'boundary': True}, 'Invalid observation boundary.'),
                ('attempt', {'attempt_id': private}, 'Invalid observation identity.'),
                ('source', {'source_sha256': private}, 'Invalid observation identity.')):
            with self.subTest(field=field, type=type(next(iter(changes.values()))).__name__):
                with self.assertRaises(observations.ObservationFormatError) as raised:
                    self.bind(packet(), **changes)
                self.assertEqual(str(raised.exception), message)
                self.assertNotIn(private, str(raised.exception))

    def test_malformed_packet_shapes_and_private_fields_are_rejected(self):
        missing = packet()
        del missing['stdin_close_completed']
        extra = packet(private_payload='private-body-must-not-appear')
        for name, value in (
                ('missing', missing), ('extra', extra), ('empty', {}),
                ('none', None), ('items', list(packet().items())),
                ('status', packet(status='private-body-must-not-appear')),
                ('valid_unavailable', packet(available=False)),
                ('unknown_status', packet(status='success'))):
            with self.subTest(case=name):
                with self.assertRaises(observations.ObservationFormatError) as raised:
                    self.bind(value)
                self.assertEqual(str(raised.exception), 'Invalid parent-I/O packet.')

    def test_truthy_and_numeric_type_substitutions_are_rejected(self):
        changes = [dict(available=1), dict(available='true'),
                   dict(writer_expected=1), dict(writer_expected='false'),
                   dict(stdout_limit=True), dict(stdout_limit=4096.0),
                   dict(stdout_retained_bytes=False), dict(stdout_retained_bytes=0.0)]
        for field in MARKS:
            changes.extend(({field: 1}, {field: 'true'}, {field: None}))
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                with self.assertRaises(observations.ObservationFormatError):
                    self.bind(packet(**change))

    def test_packet_counts_have_bounded_integer_semantics(self):
        for change in ({'stdout_limit': 0}, {'stdout_limit': -1},
                       {'stdout_limit': 16 * 1024 * 1024 + 1},
                       {'stdout_limit': 10 ** 400},
                       {'stdout_retained_bytes': -1},
                       {'stdout_reader_entered': True, 'stdout_retained_bytes': 4097}):
            with self.subTest(change=change):
                with self.assertRaises(observations.ObservationFormatError):
                    self.bind(packet(**change))
        for limit in (1, 16 * 1024 * 1024):
            with self.subTest(valid_limit=limit):
                self.assert_only_facts(self.project(self.bind(packet(
                    stdout_limit=limit, stdout_retained_bytes=limit,
                    stdout_reader_entered=True))), ())

    def test_contradictory_marks_are_not_downgraded_to_unavailable(self):
        for change in ({'stdout_eof': True}, {'stderr_eof': True},
                       {'stdout_retained_bytes': 1},
                       {'stdin_write_completed': True},
                       {'stdin_close_completed': True},
                       {'writer_expected': False, 'stdin_writer_entered': True},
                       {'writer_expected': False, 'stdin_writer_entered': True,
                        'stdin_write_completed': True, 'stdin_close_completed': True}):
            with self.subTest(change=change):
                with self.assertRaises(observations.ObservationFormatError):
                    self.bind(packet(**change))

    def test_invalid_outcomes_use_fixed_private_safe_diagnostics(self):
        private = 'private-value-must-not-appear'
        bound = self.bind(packet())
        for value in (private, None, True, 1, [], {}):
            with self.subTest(outcome_type=type(value).__name__):
                with self.assertRaises(observations.ObservationFormatError) as raised:
                    self.project(bound, outcome=value)
                self.assertEqual(str(raised.exception), 'Invalid declared terminal outcome.')
                self.assertNotIn(private, str(raised.exception))

    def test_busy_and_unavailable_must_be_canonical_not_truthy_or_partial(self):
        for status in ('busy', 'unavailable'):
            mutations = ({'available': 0}, {'available': True},
                         {'writer_expected': False}, {'stdout_limit': 0},
                         {'stdin_write_completed': False}, {'stdout_eof': True})
            for change in mutations:
                value = unavailable(status)
                value.update(change)
                with self.subTest(status=status, change=change):
                    with self.assertRaises(observations.ObservationFormatError):
                        self.bind(value)

    def test_identity_types_and_noncanonical_values_fail_binding_and_projection(self):
        class TextSubclass(str):
            pass

        cases = (
            ('attempt_id', None), ('attempt_id', True),
            ('attempt_id', '00000000-0000-1000-8000-000000000000'),
            ('attempt_id', TextSubclass(self.attempt)),
            ('source_sha256', 'B' * 64), ('source_sha256', 'g' * 64),
            ('source_sha256', SOURCE + '0'), ('source_sha256', b'b' * 64),
            ('source_sha256', TextSubclass(SOURCE)),
        )
        bound = self.bind(packet())
        for field, value in cases:
            with self.subTest(field=field, value_type=type(value).__name__):
                with self.assertRaises(observations.ObservationFormatError):
                    self.bind(packet(), **{field: value})
                with self.assertRaises(observations.ObservationFormatError):
                    self.project(bound, **{'expected_' + field: value})

    def test_custom_mapping_value_and_key_hooks_are_never_invoked(self):
        calls = []

        def trap(*args, **kwargs):
            calls.append('invoked')
            raise AssertionError('private-hook-body-must-not-appear')

        class HookValue:
            __bool__ = __str__ = __repr__ = __eq__ = __int__ = __index__ = trap

        class HookDict(dict):
            __len__ = __iter__ = __getitem__ = keys = items = copy = trap

        class HookText(str):
            __str__ = __repr__ = __bool__ = trap

        key_packet = packet()
        del key_packet['available']
        key_packet[HookText('available')] = True
        inputs = [HookDict(packet()), key_packet]
        for field in packet():
            inputs.append(packet(**{field: HookValue()}))
        for index, value in enumerate(inputs):
            with self.subTest(case=index):
                with self.assertRaises(observations.ObservationFormatError) as raised:
                    self.bind(value)
                self.assertEqual(str(raised.exception), 'Invalid parent-I/O packet.')
        for field in ('attempt_id', 'source_sha256', 'origin', 'boundary'):
            with self.subTest(identity_field=field):
                with self.assertRaises(observations.ObservationFormatError):
                    self.bind(packet(), **{field: HookValue()})
        bound = self.bind(packet())
        with self.assertRaises(observations.ObservationFormatError):
            self.project(bound, outcome=HookValue())
        with self.assertRaises(observations.ObservationFormatError):
            self.project(HookValue())
        self.assertEqual(calls, [])


