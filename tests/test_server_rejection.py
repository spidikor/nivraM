"""Real socket checks for bounded early-rejection teardown, not a reset reproducer."""
import http.client
import socket
import threading
import time
import unittest
from unittest.mock import patch

from nivram.server import Handler, LocalServer, REJECTION_DRAIN_BYTES


class CountedReader:
    def __init__(self, stream, server):
        self.stream, self.server = stream, server

    def read1(self, size):
        self.server.drain_requests.append((self.server.discarded_bytes, size))
        data = self.stream.read1(size)
        self.server.discarded_bytes += len(data)
        return data

    def __getattr__(self, name):
        return getattr(self.stream, name)


class ObservedHandler(Handler):
    def setup(self):
        super().setup()
        self.server.handler_started = time.monotonic()
        self.rfile = CountedReader(self.rfile, self.server)

    def finish(self):
        try:
            super().finish()
        finally:
            self.server.handler_seconds = time.monotonic() - self.server.handler_started
            self.server.handler_finished.set()


class RejectedPostTests(unittest.TestCase):
    def setUp(self):
        self.server = LocalServer(('127.0.0.1', 0), ObservedHandler, bind_and_activate=False)
        # Advertise room for the cap plus headers before the TCP handshake.
        # Otherwise Windows can exhaust its initial receive window just below
        # 64KiB after the response half-close, obscuring the byte-cap check.
        self.server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, REJECTION_DRAIN_BYTES * 2)
        self.server.server_bind()
        self.server.server_activate()
        self.server.api_token = 'synthetic-rejection-test-token'
        self.server.handler_finished = threading.Event()
        self.server.discarded_bytes = 0
        self.server.drain_requests = []
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.parser = self.enterContext(patch('nivram.server.json.loads',
            side_effect=AssertionError('Rejected data must never be parsed.')))
        self.importer = self.enterContext(patch.object(self.server.import_preparation, 'prepare_import',
            side_effect=AssertionError('Rejected data must never be dispatched.')))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
        self.assertFalse(self.thread.is_alive(), 'The owned server must stop.')

    def tearDown(self):
        self.parser.assert_not_called()
        self.importer.assert_not_called()

    def connect(self, fields=None):
        port = self.server.server_port
        headers = {'Host': f'127.0.0.1:{port}', 'Origin': 'https://example.invalid',
                   'X-Nivram-Token': self.server.api_token,
                   'Content-Type': 'application/json', 'Content-Length': '17'}
        headers.update(fields or {})
        client = socket.create_connection(('127.0.0.1', port), timeout=1)
        self.addCleanup(client.close)
        message = 'POST /api/import HTTP/1.1\r\n' + ''.join(
            f'{name}: {value}\r\n' for name, value in headers.items()) + '\r\n'
        client.sendall(message.encode('ascii'))
        response = http.client.HTTPResponse(client)
        self.addCleanup(response.close)
        response.begin()
        payload = response.read()
        self.assertIn(b'error', payload)
        return client, response

    def test_split_body_receives_403_before_body_and_keeps_receive_side_open(self):
        client, response = self.connect()
        self.assertEqual(response.status, 403)
        # This checks the missing staged-close behavior deterministically. It
        # does not claim to reproduce the original Windows connection reset.
        self.assertFalse(self.server.handler_finished.wait(.025),
                         'Rejection closed before the declared body could arrive.')
        client.sendall(b'x' * 17)
        self.assertTrue(self.server.handler_finished.wait(1))
        self.assertEqual(self.server.discarded_bytes, 17)
        self.assertEqual(response.getheader('Connection'), 'close')

    def test_truncated_body_has_short_total_deadline(self):
        client, response = self.connect()
        self.assertEqual(response.status, 403)
        client.sendall(b'xxx')
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertLess(self.server.handler_seconds, .75)
        self.assertEqual(self.server.discarded_bytes, 3)

    def test_trickling_does_not_restart_total_deadline(self):
        client, response = self.connect({'Content-Length': '10000'})
        self.assertEqual(response.status, 403)
        stop = time.monotonic() + 1
        while not self.server.handler_finished.wait(.025) and time.monotonic() < stop:
            try:
                client.sendall(b'x')
            except (ConnectionResetError, BrokenPipeError):
                # The bounded close may occur between the event check and send.
                # The lifecycle, deadline and received-byte assertions remain.
                break
        self.assertTrue(self.server.handler_finished.is_set())
        self.assertLess(self.server.handler_seconds, .75)
        self.assertGreater(self.server.discarded_bytes, 0)
        self.assertLess(self.server.discarded_bytes, 10000)

    def test_oversized_declared_body_discards_at_most_cap(self):
        client, response = self.connect({'Origin': f'http://127.0.0.1:{self.server.server_port}',
                                         'Content-Length': '4000001'})
        self.assertEqual(response.status, 413)
        # Read the response first, then send only the permitted drain allowance.
        # Oversized unread tails are deliberately outside the delivery promise.
        client.sendall(b'x' * REJECTION_DRAIN_BYTES)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertTrue(all(before + size <= REJECTION_DRAIN_BYTES
                            for before, size in self.server.drain_requests))
        self.assertEqual(self.server.discarded_bytes, REJECTION_DRAIN_BYTES)
        self.assertLess(self.server.handler_seconds, .75)

    def test_missing_body_with_oversized_length_still_has_deadline(self):
        _, response = self.connect({'Origin': f'http://127.0.0.1:{self.server.server_port}',
                                    'Content-Length': '4000001'})
        self.assertEqual(response.status, 413)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertLess(self.server.handler_seconds, .75)
        self.assertEqual(self.server.discarded_bytes, 0)

    def test_post_host_rejection_preserves_403_and_drains(self):
        client, response = self.connect({'Host': 'attacker.invalid'})
        self.assertEqual(response.status, 403)
        client.sendall(b'x' * 17)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertEqual(self.server.discarded_bytes, 17)

    def test_post_missing_token_preserves_403_and_drains(self):
        client, response = self.connect({'Origin': f'http://127.0.0.1:{self.server.server_port}',
                                         'X-Nivram-Token': ''})
        self.assertEqual(response.status, 403)
        client.sendall(b'x' * 17)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertEqual(self.server.discarded_bytes, 17)

    def test_content_type_rejection_preserves_415_and_drains(self):
        client, response = self.connect({'Origin': f'http://127.0.0.1:{self.server.server_port}',
                                         'Content-Type': 'text/plain'})
        self.assertEqual(response.status, 415)
        client.sendall(b'x' * 17)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertEqual(self.server.discarded_bytes, 17)

    def test_expect_continue_receives_final_rejection_without_sending_body(self):
        _, response = self.connect({'Expect': '100-continue'})
        self.assertEqual(response.status, 403)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertLess(self.server.handler_seconds, .75)

    def test_ambiguous_length_is_not_used_to_drain(self):
        _, response = self.connect({'Content-Length': '17\r\nContent-Length: 18'})
        self.assertEqual(response.status, 403)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertEqual(self.server.discarded_bytes, 0)

    def test_transfer_encoding_is_not_interpreted_as_a_framed_body(self):
        _, response = self.connect({'Transfer-Encoding': 'chunked'})
        self.assertEqual(response.status, 403)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertEqual(self.server.discarded_bytes, 0)

    def test_invalid_length_preserves_400_without_trusting_body_size(self):
        _, response = self.connect({'Origin': f'http://127.0.0.1:{self.server.server_port}',
                                    'Content-Length': 'invalid'})
        self.assertEqual(response.status, 400)
        self.assertTrue(self.server.handler_finished.wait(.75))
        self.assertEqual(self.server.discarded_bytes, 0)


if __name__ == '__main__':
    unittest.main()
