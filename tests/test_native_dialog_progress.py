import io
import threading
import unittest

from nivram.native_dialog_progress import (
    MAX_LINE_BYTES, NORMAL_EVENTS, ProgressCapture, ProgressEmitter,
    progress_diagnostic,
)


NONCE = '0123456789abcdef' * 2


def record(sequence, event='backend_ready', nonce=NONCE):
    return f'NDP1|{nonce}|{sequence}|{event}\n'.encode('ascii')


def unavailable(status='unavailable'):
    return {'available': False, 'status': status, 'last_event': None,
            'sequence': None, 'partial_record': None}


class NativeDialogProgressTests(unittest.TestCase):
    def test_emit_and_capture_round_trip_without_private_receipt_values(self):
        stream = io.BytesIO()
        emitter = ProgressEmitter(NONCE, stream)
        emitter.emit('request_validate_enter')
        emitter.emit('backend_ready')
        capture = ProgressCapture(NONCE)
        capture.feed(stream.getvalue())
        self.assertEqual(capture.freeze(), {
            'available': True, 'status': 'valid', 'last_event': 'backend_ready',
            'sequence': 2, 'partial_record': False,
        })
        self.assertNotIn(NONCE, repr(capture.snapshot()))
        self.assertEqual(progress_diagnostic(capture.snapshot()), capture.snapshot())

    def test_every_event_round_trips(self):
        for event in NORMAL_EVENTS:
            with self.subTest(event=event):
                capture = ProgressCapture(NONCE)
                capture.feed(record(1, event))
                self.assertEqual(capture.freeze()['last_event'], event)

    def test_every_two_chunk_split_and_one_byte_chunks(self):
        data = record(1, 'request_validated') + record(2)
        for position in range(len(data) + 1):
            capture = ProgressCapture(NONCE)
            capture.feed(data[:position])
            capture.feed(data[position:])
            self.assertEqual(capture.freeze()['sequence'], 2)
        capture = ProgressCapture(NONCE)
        for byte in data:
            capture.feed(bytes([byte]))
        self.assertEqual(capture.freeze()['sequence'], 2)

    def test_empty_and_partial_record_are_not_complete_checkpoints(self):
        capture = ProgressCapture(NONCE)
        self.assertEqual(capture.snapshot(), {
            'available': False, 'status': 'empty', 'last_event': None,
            'sequence': 0, 'partial_record': False,
        })
        capture.feed(record(1)[:-1])
        self.assertFalse(capture.snapshot()['available'])
        self.assertTrue(capture.snapshot()['partial_record'])
        capture.feed(b'\n')
        capture.feed(record(2, 'invoke_prepare_enter')[:-1])
        packet = capture.freeze()
        self.assertEqual(packet['last_event'], 'backend_ready')
        self.assertEqual(packet['sequence'], 1)
        self.assertTrue(packet['partial_record'])
        self.assertEqual(progress_diagnostic(packet), packet)

    def test_freeze_prevents_late_bytes_and_returns_independent_copies(self):
        capture = ProgressCapture(NONCE)
        capture.feed(record(1))
        frozen = capture.freeze()
        expected = dict(frozen)
        frozen['last_event'] = 'private replacement'
        capture.feed(record(2, 'cancel_enter'))
        capture.feed(b'raw secret')
        self.assertEqual(capture.snapshot(), expected)
        self.assertEqual(capture.freeze(), expected)

    def test_snapshot_never_waits_for_feed_lock(self):
        capture = ProgressCapture(NONCE)
        capture.feed(record(1))
        finished = threading.Event()
        packets = []

        def snapshot():
            packets.append(capture.snapshot())
            finished.set()

        with capture._lock:
            thread = threading.Thread(target=snapshot, daemon=True)
            thread.start()
            self.assertTrue(finished.wait(1), 'Snapshot waited for the parser lock.')
        thread.join(1)
        self.assertEqual(packets[0], unavailable('busy'))
        self.assertEqual(capture.snapshot()['last_event'], 'backend_ready')

    def test_late_feed_from_other_thread_cannot_change_frozen_packet(self):
        capture = ProgressCapture(NONCE)
        capture.feed(record(1))
        ready = threading.Event()
        release = threading.Event()

        def late_feed():
            ready.set()
            release.wait(2)
            capture.feed(record(2, 'backend_closed'))

        thread = threading.Thread(target=late_feed, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(1))
        expected = capture.freeze()
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(capture.snapshot(), expected)

    def test_emitter_caps_at_reserved_marker_and_capture_preserves_last_event(self):
        stream = io.BytesIO()
        emitter = ProgressEmitter(NONCE, stream)
        for _ in range(100):
            emitter.emit('identify_field_enter')
        lines = stream.getvalue().splitlines(keepends=True)
        self.assertEqual(len(lines), 32)
        self.assertEqual(lines[-1], record(32, 'diagnostic_limit'))
        self.assertTrue(all(len(line) <= MAX_LINE_BYTES for line in lines))
        self.assertLessEqual(len(stream.getvalue()), 3072)
        capture = ProgressCapture(NONCE)
        capture.feed(stream.getvalue())
        packet = capture.freeze()
        self.assertEqual(packet, {
            'available': True, 'status': 'capped', 'last_event': 'identify_field_enter',
            'sequence': 32, 'partial_record': False,
        })
        self.assertEqual(progress_diagnostic(packet), packet)

    def test_cap_requires_all_prior_records_and_no_following_bytes(self):
        for data in (record(1, 'diagnostic_limit'), record(32, 'diagnostic_limit'),
                     b''.join(record(i) for i in range(1, 33)),
                     b''.join(record(i) for i in range(1, 32)) + record(32, 'diagnostic_limit') + b'x'):
            with self.subTest(data_length=len(data)):
                capture = ProgressCapture(NONCE)
                capture.feed(data)
                self.assertEqual(capture.freeze()['status'], 'invalid')

    def test_malformed_and_private_bytes_are_rejected_without_export(self):
        invalid = (
            b'', b'\xff', b'\r', b'\x00', b'private C:\\Users\\person\\secret.txt',
            b'NDP1|wrong|1|backend_ready',
            record(1, nonce='f' * 32)[:-1],
            record(3)[:-1], record(1, 'unknown_event')[:-1],
            record(1)[:-1] + b'|extra',
            record(1)[:-1].replace(b'|1|', b'|01|'),
            record(1)[:-1].replace(b'|1|', b'|+1|'),
            record(1)[:-1].replace(b'|1|', b'|1.0|'),
        )
        for line in invalid:
            with self.subTest(length=len(line)):
                capture = ProgressCapture(NONCE)
                capture.feed(record(1))
                capture.feed(line + b'\n')
                packet = capture.freeze()
                self.assertEqual(packet, unavailable('invalid'))
                self.assertNotIn('secret', repr(packet))
                self.assertNotIn(NONCE, repr(packet))

    def test_duplicate_record_invalidates_prior_progress(self):
        capture = ProgressCapture(NONCE)
        capture.feed(record(1) * 2)
        self.assertEqual(capture.freeze()['status'], 'invalid')

    def test_line_and_total_input_bounds(self):
        capture = ProgressCapture(NONCE)
        capture.feed(b'x' * (MAX_LINE_BYTES - 1))
        self.assertEqual(capture.snapshot()['status'], 'empty')
        capture.feed(b'x')
        self.assertEqual(capture.freeze()['status'], 'invalid')
        for data in (b'x' * MAX_LINE_BYTES + b'\n', b'x' * 4096, b'x' * 4097):
            capture = ProgressCapture(NONCE)
            capture.feed(data)
            self.assertEqual(capture.freeze()['status'], 'invalid')

    def test_non_bytes_feed_rejects_without_custom_protocol_hooks(self):
        class Hostile:
            def __len__(self):
                raise AssertionError('length hook')

        class BytesSubclass(bytes):
            def __iter__(self):
                raise AssertionError('iteration hook')

        for value in (Hostile(), bytearray(record(1)), BytesSubclass(record(1))):
            capture = ProgressCapture(NONCE)
            capture.feed(value)
            self.assertEqual(capture.freeze()['status'], 'invalid')

    def test_invalid_nonces_reject_without_custom_hooks(self):
        class StringSubclass(str):
            def __len__(self):
                raise AssertionError('length hook')

        for nonce in ('a' * 31, 'A' * 32, 'g' * 32, b'a' * 32, StringSubclass(NONCE)):
            with self.assertRaises(ValueError):
                ProgressCapture(nonce)
            with self.assertRaises(ValueError):
                ProgressEmitter(nonce, io.BytesIO())

    def test_emitter_handles_partial_writes_and_flushes(self):
        class PartialStream:
            def __init__(self):
                self.output = bytearray()
                self.flushes = 0

            def write(self, data):
                self.output.extend(data[:1])
                return 1

            def flush(self):
                self.flushes += 1

        stream = PartialStream()
        ProgressEmitter(NONCE, stream).emit('backend_ready')
        self.assertEqual(bytes(stream.output), record(1))
        self.assertEqual(stream.flushes, 1)

    def test_emitter_disables_after_write_or_flush_failure(self):
        for failure in ('write', 'flush', 'zero', 'none', 'bool', 'oversized'):
            class FailingStream:
                calls = 0

                def write(self, data):
                    self.calls += 1
                    if failure == 'write':
                        raise OSError('private error text')
                    return {'zero': 0, 'none': None, 'bool': True,
                            'oversized': len(data) + 1}.get(failure, len(data))

                def flush(self):
                    if failure == 'flush':
                        raise OSError('private flush text')

            stream = FailingStream()
            emitter = ProgressEmitter(NONCE, stream)
            emitter.emit('backend_ready')
            emitter.emit('result_emit_enter')
            self.assertEqual(stream.calls, 1)

    def test_emitter_rejects_unknown_or_reserved_events_without_output(self):
        for event in ('private/path', 'diagnostic_limit', None, b'backend_ready'):
            stream = io.BytesIO()
            emitter = ProgressEmitter(NONCE, stream)
            emitter.emit(event)
            emitter.emit('backend_ready')
            self.assertEqual(stream.getvalue(), b'')

    def test_sanitizer_rejects_hostile_mappings_keys_values_and_extra_fields(self):
        class HostileDict(dict):
            def get(self, *args):
                raise AssertionError('get hook')

            def __iter__(self):
                raise AssertionError('iteration hook')

        class HostileKey:
            def __hash__(self):
                return hash('available')

            def __eq__(self, other):
                raise AssertionError('equality hook')

        class HostileString(str):
            def __eq__(self, other):
                raise AssertionError('string equality hook')

        valid = {'available': True, 'status': 'valid', 'last_event': 'backend_ready',
                 'sequence': 1, 'partial_record': False}
        bad_key = {HostileKey(): True, 'status': 'valid', 'last_event': 'backend_ready',
                   'sequence': 1, 'partial_record': False}
        values = [HostileDict(valid), bad_key, {**valid, 'private': 'secret'},
                  {**valid, 'status': HostileString('valid')},
                  {**valid, 'last_event': HostileString('backend_ready')}]
        for value in values:
            self.assertEqual(progress_diagnostic(value), progress_diagnostic(None))

    def test_sanitizer_rejects_incoherent_packets_and_returns_fresh_copy(self):
        valid = {'available': True, 'status': 'valid', 'last_event': 'backend_ready',
                 'sequence': 1, 'partial_record': False}
        for changes in ({'available': False}, {'sequence': True}, {'sequence': 0},
                        {'sequence': 32}, {'partial_record': None},
                        {'status': 'capped'}, {'last_event': 'diagnostic_limit'},
                        {'status': 'busy'}, {'status': 'empty'}):
            self.assertEqual(progress_diagnostic({**valid, **changes}), progress_diagnostic(None))
        copy = progress_diagnostic(valid)
        copy['last_event'] = 'changed'
        self.assertEqual(valid['last_event'], 'backend_ready')


if __name__ == '__main__':
    unittest.main()
