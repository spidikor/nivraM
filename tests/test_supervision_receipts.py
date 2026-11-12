"""Offline synthetic contracts; no application or child process runs."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import uuid
from nivram import supervision_receipts as receipts

SOURCE = 'a' * 64
TRUE_FACTS = ('ownership_confirmed', 'root_exit_confirmed', 'stdin_write_completed',
              'stdin_closed', 'stdout_eof', 'stderr_eof', 'output_drained',
              'output_closed', 'reader_threads_exited', 'cleanup_confirmed')

def complete(**changes):
    data = dict(attempt_id=str(uuid.uuid4()), source_sha256=SOURCE, outcome='returned',
                returncode=0, remaining_descendants=0, forced_cleanup=False)
    data.update({name: True for name in TRUE_FACTS})
    data.update(changes)
    return receipts.TerminalEvidence(**data)

def decode(raw, evidence):
    return receipts.decode_terminal(raw, expected_attempt_id=evidence.attempt_id,
                                    expected_source_sha256=evidence.source_sha256)

class TerminalEvidenceTests(unittest.TestCase):
    def test_complete_recorded_normal_exit_round_trips(self):
        evidence = complete()
        raw = receipts.encode_terminal(evidence)
        self.assertEqual(decode(raw, evidence), evidence)
        self.assertEqual(json.loads(raw)['schema'], 'nivram-fresh-supervision-terminal/1')
        result = receipts.assess_recorded_evidence(evidence)
        self.assertTrue(result.complete_recorded_evidence)
        self.assertTrue(result.normal_exit_recorded)
        self.assertEqual(result.incomplete, ())

    def test_unknown_facts_are_not_completed_by_a_returned_label(self):
        evidence = receipts.TerminalEvidence(str(uuid.uuid4()), SOURCE, 'returned')
        result = receipts.assess_recorded_evidence(evidence)
        self.assertFalse(result.complete_recorded_evidence)
        self.assertFalse(result.normal_exit_recorded)
        self.assertIn('returncode', result.incomplete)
        self.assertIn('ownership_confirmed', result.incomplete)
        self.assertEqual(decode(receipts.encode_terminal(evidence), evidence), evidence)

    def test_each_required_fact_must_be_affirmatively_observed(self):
        evidence = complete()
        for name in TRUE_FACTS:
            for value in (None, False):
                with self.subTest(fact=name, value=value):
                    changes = {name: value}
                    if name == 'root_exit_confirmed' and value is False:
                        changes['returncode'] = None
                    result = receipts.assess_recorded_evidence(replace(evidence, **changes))
                    self.assertFalse(result.normal_exit_recorded)
                    self.assertFalse(result.complete_recorded_evidence)
                    self.assertIn(name, result.incomplete)

    def test_stdin_close_does_not_establish_input_write_completion(self):
        result = receipts.assess_recorded_evidence(
            complete(stdin_closed=True, stdin_write_completed=False))
        self.assertFalse(result.normal_exit_recorded)
        self.assertIn('stdin_write_completed', result.incomplete)

    def test_nonzero_exit_preserves_complete_but_unsuccessful_evidence(self):
        evidence = complete(returncode=7)
        result = receipts.assess_recorded_evidence(evidence)
        self.assertTrue(result.complete_recorded_evidence)
        self.assertFalse(result.normal_exit_recorded)
        self.assertEqual(decode(receipts.encode_terminal(evidence), evidence).returncode, 7)

    def test_primary_failure_is_not_rewritten_by_successful_cleanup(self):
        for outcome in ('timeout', 'cancelled', 'interrupted', 'error'):
            with self.subTest(outcome=outcome):
                evidence = complete(outcome=outcome)
                result = receipts.assess_recorded_evidence(evidence)
                self.assertTrue(result.complete_recorded_evidence)
                self.assertFalse(result.normal_exit_recorded)
                self.assertEqual(decode(receipts.encode_terminal(evidence), evidence).outcome,
                                 outcome)

    def test_forced_cleanup_does_not_establish_normal_or_descendant_exit(self):
        evidence = complete(forced_cleanup=True)
        result = receipts.assess_recorded_evidence(evidence)
        self.assertTrue(result.complete_recorded_evidence)
        self.assertFalse(result.normal_exit_recorded)
        result = receipts.assess_recorded_evidence(replace(evidence, remaining_descendants=None))
        self.assertFalse(result.complete_recorded_evidence)
        self.assertIn('remaining_descendants', result.incomplete)

    def test_nonempty_descendants_and_unknown_cleanup_stay_incomplete(self):
        for changes in ({'remaining_descendants': 1}, {'forced_cleanup': None}):
            with self.subTest(changes=changes):
                result = receipts.assess_recorded_evidence(complete(**changes))
                self.assertFalse(result.complete_recorded_evidence)
                self.assertFalse(result.normal_exit_recorded)

    def test_running_root_with_return_code_is_rejected(self):
        with self.assertRaises(receipts.TerminalFormatError):
            complete(root_exit_confirmed=False, returncode=0)

    def test_bool_integer_and_range_fields_do_not_coerce(self):
        invalid = [{'returncode': True}, {'returncode': 0.0},
                   {'remaining_descendants': False}, {'remaining_descendants': '0'},
                   {'forced_cleanup': 0}, {'ownership_confirmed': 1},
                   {'stdin_closed': 'true'}, {'stdout_eof': []},
                   {'reader_threads_exited': {}}, {'returncode': -(2**31) - 1},
                   {'returncode': 2**32}, {'remaining_descendants': -1},
                   {'remaining_descendants': 65536}]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(receipts.TerminalFormatError):
                    complete(**changes)

    def test_identity_rejects_noncanonical_uuid_and_private_text(self):
        invalid = [{'attempt_id': 'PRIVATE-PATH-/example'},
                   {'attempt_id': 'ABCDEFAB-1234-4234-8234-123456789ABC'},
                   {'attempt_id': '00000000-0000-0000-0000-000000000000'},
                   {'source_sha256': 'F' * 64}, {'source_sha256': 'x' * 64}]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(receipts.TerminalFormatError) as caught:
                    complete(**changes)
                self.assertNotIn('PRIVATE-PATH', str(caught.exception))

    def test_evidence_is_frozen_and_caller_mapping_is_not_retained(self):
        evidence = complete()
        with self.assertRaises(FrozenInstanceError):
            evidence.outcome = 'error'
        value = json.loads(receipts.encode_terminal(evidence))
        copied = decode(json.dumps(value).encode(), evidence)
        value['outcome'] = 'error'
        self.assertEqual(copied.outcome, 'returned')

    def test_duplicate_json_fields_are_rejected_even_when_values_match(self):
        evidence = complete()
        raw = receipts.encode_terminal(evidence)
        with self.assertRaises(receipts.TerminalFormatError):
            decode(b'{"outcome":"returned",' + raw[1:], evidence)

    def test_unexpected_private_fields_and_missing_fields_are_rejected(self):
        evidence = complete()
        for key in ('command', 'pid', 'path', 'exception', 'molecule'):
            with self.subTest(key=key):
                value = json.loads(receipts.encode_terminal(evidence))
                value[key] = 'PRIVATE-MARKER'
                with self.assertRaises(receipts.TerminalFormatError) as caught:
                    decode(json.dumps(value).encode(), evidence)
                self.assertNotIn('PRIVATE-MARKER', str(caught.exception))
        value = json.loads(receipts.encode_terminal(evidence))
        del value['cleanup_confirmed']
        with self.assertRaises(receipts.TerminalFormatError):
            decode(json.dumps(value).encode(), evidence)

    def test_old_schema_is_not_accepted_as_new_protocol(self):
        evidence = complete()
        value = json.loads(receipts.encode_terminal(evidence))
        value['schema'] = 'nivram-durable-terminal/1'
        with self.assertRaises(receipts.TerminalFormatError):
            decode(json.dumps(value).encode(), evidence)

    def test_nonfinite_constants_invalid_utf8_and_nonobjects_are_rejected(self):
        evidence = complete()
        raw = receipts.encode_terminal(evidence)
        invalid = [b'\xff', b'[]', b'null', b'{}', b'', raw[:-2]]
        invalid.extend(raw.replace(b'"returncode":0', b'"returncode":' + value)
                       for value in (b'NaN', b'Infinity', b'-Infinity'))
        for data in invalid:
            with self.subTest(data=data[:30]):
                with self.assertRaises(receipts.TerminalFormatError):
                    decode(data, evidence)

    def test_oversized_and_nonbyte_input_is_rejected(self):
        evidence = complete()
        for raw in (b' ' * (receipts.MAX_RECEIPT_BYTES + 1), '{}', bytearray(b'{}')):
            with self.subTest(kind=type(raw).__name__):
                with self.assertRaises(receipts.TerminalFormatError):
                    decode(raw, evidence)

    def test_attempt_and_source_binding_are_both_required(self):
        evidence = complete()
        raw = receipts.encode_terminal(evidence)
        for attempt, source in ((str(uuid.uuid4()), SOURCE), (evidence.attempt_id, 'b' * 64)):
            with self.subTest(attempt=attempt, source=source):
                with self.assertRaises(receipts.TerminalFormatError):
                    receipts.decode_terminal(raw, expected_attempt_id=attempt,
                                             expected_source_sha256=source)


class FaultyStream:
    """Real owned file with one controlled filesystem-boundary failure."""
    def __init__(self, stream, fault):
        self.stream, self.fault = stream, fault

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, kind, error, traceback):
        result = self.stream.__exit__(kind, error, traceback)
        if self.fault == 'close':
            raise OSError('PRIVATE-MARKER-close')
        return result

    def write(self, data):
        if self.fault == 'short':
            return self.stream.write(data[:17])
        if self.fault == 'write':
            self.stream.write(data[:17])
            raise OSError('PRIVATE-MARKER-write')
        return self.stream.write(data)

    def flush(self):
        if self.fault == 'flush':
            raise OSError('PRIVATE-MARKER-flush')
        return self.stream.flush()

    def fileno(self):
        return self.stream.fileno()


class ReceiptWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nivram-receipt-test-')
        self.directory = Path(self.temp.name)
        self.writers = []

    def tearDown(self):
        try:
            for writer in self.writers:
                snapshot = writer.wait(2)
                if snapshot.writer_started:
                    self.assertTrue(snapshot.writer_exited, 'Test-owned writer did not finish.')
        finally:
            self.temp.cleanup()

    def writer(self, evidence=None):
        writer = receipts.ReceiptWrite(self.directory, evidence or complete())
        self.writers.append(writer)
        return writer

    def read(self, writer, evidence):
        return receipts.read_terminal(writer.path, expected_attempt_id=evidence.attempt_id,
                                      expected_source_sha256=evidence.source_sha256)

    def test_construction_and_poll_do_not_start_or_write(self):
        writer = self.writer()
        observation = writer.wait(0)
        self.assertEqual(observation.state, 'not_started')
        self.assertFalse(observation.writer_started)
        self.assertFalse(observation.writer_exited)
        self.assertFalse(observation.receipt_acknowledged)
        self.assertFalse(writer.path.exists())

    def test_success_requires_sync_close_readback_and_writer_exit(self):
        evidence = complete()
        writer = self.writer(evidence).start()
        observation = writer.wait(2)
        self.assertEqual(observation.state, 'acknowledged')
        self.assertTrue(observation.receipt_acknowledged)
        self.assertTrue(observation.file_created)
        self.assertTrue(observation.file_synced)
        self.assertTrue(observation.file_closed)
        self.assertTrue(observation.readback_verified)
        self.assertTrue(observation.writer_exited)
        self.assertEqual(observation.expected_sha256,
                         hashlib.sha256(writer.path.read_bytes()).hexdigest())
        self.assertEqual(self.read(writer, evidence), evidence)

    def test_existing_receipt_is_preserved_and_not_deleted(self):
        writer = self.writer()
        old_bytes = b'existing original evidence\n'
        writer.path.write_bytes(old_bytes)
        observation = writer.start().wait(2)
        self.assertEqual(observation.state, 'failed')
        self.assertEqual(observation.failure_stage, 'open')
        self.assertFalse(observation.file_created)
        self.assertFalse(observation.receipt_acknowledged)
        self.assertEqual(writer.path.read_bytes(), old_bytes)

    def test_repeat_start_is_rejected_without_rewriting(self):
        writer = self.writer().start()
        self.assertTrue(writer.wait(2).receipt_acknowledged)
        before = writer.path.read_bytes()
        with self.assertRaises(RuntimeError):
            writer.start()
        self.assertEqual(writer.path.read_bytes(), before)

    def test_wait_validation_does_not_start_or_write(self):
        writer = self.writer()
        for budget in (True, -1, 30.1, float('nan'), float('inf'), '1', 10**400, -(10**400)):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    writer.wait(budget)
        self.assertEqual(writer.snapshot().state, 'not_started')
        self.assertFalse(writer.path.exists())

    def stream_failure(self, fault, expected_stage):
        original_open = Path.open
        def opening(path, mode='r', *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return FaultyStream(stream, fault) if mode == 'xb' else stream
        writer = self.writer()
        with mock.patch.object(Path, 'open', new=opening):
            observation = writer.start().wait(2)
        self.assertTrue(observation.writer_exited)
        self.assertEqual(observation.state, 'failed')
        self.assertEqual(observation.failure_stage, expected_stage)
        self.assertFalse(observation.receipt_acknowledged)
        self.assertFalse(observation.readback_verified)
        self.assertTrue(writer.path.exists(), 'Partial evidence must not be removed.')
        self.assertNotIn('PRIVATE-MARKER', repr(observation))

    def test_short_write_is_not_acknowledged(self):
        self.stream_failure('short', 'write')

    def test_partial_write_exception_is_not_acknowledged(self):
        self.stream_failure('write', 'write')

    def test_flush_failure_is_not_acknowledged(self):
        self.stream_failure('flush', 'flush')

    def test_close_failure_is_not_acknowledged(self):
        self.stream_failure('close', 'close')

    def test_fsync_failure_leaves_readable_bytes_without_persistence_ack(self):
        evidence = complete()
        writer = self.writer(evidence)
        with mock.patch.object(receipts.os, 'fsync', side_effect=OSError('PRIVATE-MARKER-sync')):
            observation = writer.start().wait(2)
        self.assertEqual(observation.failure_stage, 'fsync')
        self.assertFalse(observation.file_synced)
        self.assertFalse(observation.receipt_acknowledged)
        self.assertTrue(observation.writer_exited)
        self.assertEqual(self.read(writer, evidence), evidence)
        self.assertNotIn('PRIVATE-MARKER', repr(observation))

    def test_readback_mismatch_and_exception_never_acknowledge(self):
        for result in (b'different bytes', OSError('PRIVATE-MARKER-readback')):
            with self.subTest(result=type(result).__name__):
                writer = self.writer()
                options = ({'side_effect': result} if isinstance(result, BaseException)
                           else {'return_value': result})
                with mock.patch.object(receipts, '_read_bytes', **options):
                    observation = writer.start().wait(2)
                self.assertTrue(observation.file_synced)
                self.assertTrue(observation.file_closed)
                self.assertEqual(observation.failure_stage, 'readback')
                self.assertFalse(observation.readback_verified)
                self.assertFalse(observation.receipt_acknowledged)
                self.assertTrue(writer.path.exists())
                self.assertNotIn('PRIVATE-MARKER', repr(observation))

    def test_timeout_snapshot_stays_pending_after_late_acknowledgement(self):
        entered, release = threading.Event(), threading.Event()
        original_fsync = receipts.os.fsync
        def delayed_sync(fd):
            entered.set()
            if not release.wait(2):
                raise OSError('Bounded test release did not arrive.')
            return original_fsync(fd)
        writer = self.writer()
        with mock.patch.object(receipts.os, 'fsync', new=delayed_sync):
            try:
                writer.start()
                self.assertTrue(entered.wait(1))
                before = writer.wait(0.01)
                self.assertEqual(before.state, 'pending')
                self.assertTrue(before.writer_started)
                self.assertFalse(before.writer_exited)
                self.assertFalse(before.receipt_acknowledged)
                with self.assertRaises(FrozenInstanceError):
                    before.state = 'acknowledged'
            finally:
                release.set()
                after = writer.wait(2)
        self.assertTrue(after.receipt_acknowledged)
        self.assertTrue(after.writer_exited)
        self.assertEqual(before.state, 'pending')
        self.assertFalse(before.writer_exited)
        self.assertFalse(before.receipt_acknowledged)

    def test_start_interruption_is_not_erased_by_late_successful_write(self):
        entered, release = threading.Event(), threading.Event()
        original_read = receipts._read_bytes
        original_start = threading.Thread.start
        def delayed_read(path):
            entered.set()
            if not release.wait(2):
                raise OSError('Bounded test release did not arrive.')
            return original_read(path)
        def interrupted_start(thread):
            original_start(thread)
            raise KeyboardInterrupt('PRIVATE-MARKER-start')
        writer = self.writer()
        with mock.patch.object(receipts, '_read_bytes', new=delayed_read):
            try:
                with mock.patch.object(threading.Thread, 'start', new=interrupted_start):
                    with self.assertRaises(KeyboardInterrupt):
                        writer.start()
                self.assertTrue(entered.wait(1))
            finally:
                release.set()
                after = writer.wait(2)
        self.assertTrue(after.file_synced)
        self.assertTrue(after.readback_verified)
        self.assertTrue(after.writer_exited)
        self.assertEqual(after.state, 'failed')
        self.assertEqual(after.failure_stage, 'start')
        self.assertFalse(after.receipt_acknowledged)
        self.assertNotIn('PRIVATE-MARKER', repr(after))

    def test_acknowledgement_does_not_imply_writer_thread_exit(self):
        entered, release = threading.Event(), threading.Event()
        original_run = receipts.ReceiptWrite._run
        def delayed_return(writer):
            original_run(writer)
            entered.set()
            release.wait(2)
        with mock.patch.object(receipts.ReceiptWrite, '_run', new=delayed_return):
            writer = self.writer()
            try:
                writer.start()
                self.assertTrue(entered.wait(1))
                before = writer.wait(0)
                self.assertTrue(before.receipt_acknowledged)
                self.assertFalse(before.writer_exited)
            finally:
                release.set()
                after = writer.wait(2)
        self.assertTrue(after.writer_exited)
        self.assertFalse(before.writer_exited)

    def test_concurrent_same_attempt_has_one_winner_without_overwrite(self):
        evidence = complete()
        other = replace(evidence, outcome='interrupted', returncode=5)
        writers = [self.writer(evidence), self.writer(other)]
        rendezvous = threading.Barrier(2, timeout=2)
        original_open = Path.open
        def opening(path, mode='r', *args, **kwargs):
            if mode == 'xb':
                rendezvous.wait()
            return original_open(path, mode, *args, **kwargs)
        with mock.patch.object(Path, 'open', new=opening):
            try:
                for writer in writers:
                    writer.start()
                observations = [writer.wait(2) for writer in writers]
            finally:
                rendezvous.abort()
                for writer in writers:
                    writer.wait(2)
        self.assertEqual(sum(x.receipt_acknowledged for x in observations), 1)
        self.assertEqual(sum(x.failure_stage == 'open' for x in observations), 1)
        winner = next(i for i, x in enumerate(observations) if x.receipt_acknowledged)
        expected = (evidence, other)[winner]
        self.assertEqual(writers[0].path.read_bytes(), receipts.encode_terminal(expected))

    def test_concurrent_distinct_attempts_have_separate_records(self):
        evidences = [complete(), complete(outcome='timeout', returncode=3)]
        writers = [self.writer(evidence).start() for evidence in evidences]
        for writer, evidence in zip(writers, evidences):
            self.assertTrue(writer.wait(2).receipt_acknowledged)
            self.assertEqual(self.read(writer, evidence), evidence)
        self.assertNotEqual(writers[0].path, writers[1].path)

    def test_reader_rejects_oversized_file_and_wrong_binding(self):
        evidence = complete()
        writer = self.writer(evidence)
        writer.path.write_bytes(b'x' * (receipts.MAX_RECEIPT_BYTES + 1))
        with self.assertRaises(receipts.TerminalFormatError):
            self.read(writer, evidence)
        writer.path.write_bytes(receipts.encode_terminal(evidence))
        with self.assertRaises(receipts.TerminalFormatError):
            receipts.read_terminal(writer.path, expected_attempt_id=str(uuid.uuid4()),
                                   expected_source_sha256=SOURCE)

    def test_invalid_expected_identity_is_rejected_before_file_access(self):
        with mock.patch.object(receipts, '_read_bytes') as reader:
            with self.assertRaises(receipts.TerminalFormatError):
                receipts.read_terminal(self.directory / 'not-read',
                                       expected_attempt_id='invalid',
                                       expected_source_sha256=SOURCE)
            reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
