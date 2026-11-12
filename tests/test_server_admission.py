"""Bounded loopback socket regressions for HTTP admission, without provider I/O."""
import http.client
import io
import json
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nivram.server import Handler, LocalServer, REJECTION_DRAIN_BYTES


class ObservedServer(LocalServer):
    def handle_error(self, request, client_address):
        # Record uncaught handler errors without dumping attacker input/tracebacks.
        self.handler_errors.append(True)


class ObservedHandler(Handler):
    def finish(self):
        try:
            return super().finish()
        finally:
            self.server.handler_finished.set()

    def _send(self, status, payload, *args, **kwargs):
        self.server.sent_responses.append((status, self.requestline))
        return super()._send(status, payload, *args, **kwargs)

    def send_error(self, code, message=None, explain=None):
        self.server.protocol_errors.append((code, self.requestline))
        return super().send_error(code, message, explain)

    def do_POST(self):
        self.server.posts_seen += 1
        return super().do_POST()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.server = ObservedServer(('127.0.0.1', 0), ObservedHandler)
        self.server.api_token = 'synthetic-admission-token'
        self.server.handler_errors = []
        self.server.protocol_errors = []
        self.server.sent_responses = []
        self.server.posts_seen = 0
        self.server.handler_finished = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.clients = []
        self.addCleanup(self.stop_server)
        # create=True also permits running the new regression against the base.
        self.enterContext(patch('nivram.server.REQUEST_BODY_SECONDS', .2, create=True))

    def stop_server(self):
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
        self.assertFalse(self.thread.is_alive(), 'Owned loopback server must exit.')

    def connect(self, *, body_length=2, changes=None, extras=(), method='POST', body=b''):
        self.posts_before = self.server.posts_seen
        port = self.server.server_port
        fields = {'Host': f'127.0.0.1:{port}',
                  'Origin': f'http://127.0.0.1:{port}',
                  'X-Nivram-Token': self.server.api_token,
                  'Content-Type': 'application/json',
                  'Content-Length': str(body_length)}
        fields.update(changes or {})
        rows = [(key, value) for key, value in fields.items() if value is not None]
        rows.extend(extras)
        client = socket.create_connection(('127.0.0.1', port), timeout=1.25)
        self.clients.append(client)
        route = '/api/unknown' if method == 'POST' else '/api/status'
        message = f'{method} {route} HTTP/1.1\r\n' + ''.join(
            f'{key}: {value}\r\n' for key, value in rows) + '\r\n'
        client.sendall(message.encode('latin-1') + body)
        return client

    def read_response(self, client):
        response = http.client.HTTPResponse(client)
        try:
            response.begin()
            raw = response.read()
            if b'blocking-pages - Blocking pages for AdGuard projects' in raw[:160]:
                self.skipTest('AdGuard replaced this malformed-request response; '
                              'the same case is covered at the in-process handler boundary.')
            try:
                payload = json.loads(raw)
            except ValueError:
                self.fail(f'Expected JSON at status {response.status}, got {raw[:800]!r}; '
                          f'protocol errors: {self.server.protocol_errors!r}.')
            self.assertIn('error', payload)
            return response.status, response.getheader('Connection'), payload
        except (OSError, http.client.HTTPException) as error:
            self.fail(f'Expected a controlled JSON response, got {type(error).__name__}; '
                      f'handler entered: {self.server.posts_seen > self.posts_before}, '
                      f'handler errors: {len(self.server.handler_errors)}, '
                      f'protocol errors: {self.server.protocol_errors!r}, '
                      f'app responses: {self.server.sent_responses!r}.')
        finally:
            response.close()

    def request(self, *, body=b'{}', changes=None, extras=(), half_close=False, send_body=True):
        client = self.connect(body_length=len(body), changes=changes, extras=extras,
                              body=body if send_body else b'')
        try:
            if half_close:
                client.shutdown(socket.SHUT_WR)
            return self.read_response(client)
        finally:
            client.close()

    def test_valid_json_with_optional_origin_and_content_type_parameters_dispatches(self):
        for changes in ({}, {'Origin': None}, {'Content-Type': 'application/json; charset=utf-8'}):
            with self.subTest(changes=changes):
                status, _, payload = self.request(changes=changes)
                self.assertEqual(status, 404)
                self.assertEqual(payload['error'], 'Unknown API action.')
        self.assertEqual(self.server.handler_errors, [])

    def test_duplicate_or_transfer_coded_framing_is_rejected(self):
        for changes, extras in [
            ({}, [('Content-Length', '2')]),
            ({}, [('Content-Length', '3')]),
            ({'Transfer-Encoding': 'chunked'}, []),
            ({'Transfer-Encoding': 'identity'}, []),
            ({'Transfer-Encoding': 'chunked', 'Content-Length': None}, []),
        ]:
            with self.subTest(changes=changes, extras=extras):
                # Ambiguous framing is rejected before any body is needed.
                # The server does not promise to drain an ambiguous body.
                status, connection, _ = self.request(changes=changes, extras=extras, send_body=False)
                self.assertEqual(status, 400)
                self.assertEqual(connection, 'close')

    def test_singular_security_headers_reject_duplicate_values(self):
        for header, first in [
            ('Host', f'127.0.0.1:{self.server.server_port}'),
            ('Origin', f'http://127.0.0.1:{self.server.server_port}'),
            ('X-Nivram-Token', self.server.api_token),
            ('Content-Type', 'application/json'),
        ]:
            for second in (first, 'different-value'):
                with self.subTest(header=header, second=second):
                    status, connection, _ = self.request(extras=[(header, second)])
                    self.assertEqual(status, 415 if header == 'Content-Type' else 403)
                    self.assertEqual(connection, 'close')

    def test_get_rejects_duplicate_host(self):
        client = self.connect(method='GET', extras=[('Host', 'different-value')])
        try:
            self.assertEqual(self.read_response(client)[0], 403)
        finally:
            client.close()

    def test_non_ascii_token_and_empty_origin_return_forbidden(self):
        for changes in ({'X-Nivram-Token': '\u00e9'}, {'Origin': ''}):
            with self.subTest(changes=changes):
                self.assertEqual(self.request(changes=changes)[0], 403)
        self.assertEqual(self.server.handler_errors, [])

    def test_length_must_be_one_bounded_positive_decimal(self):
        for length in (None, '', '+2', '-2', '2, 2', '2x', '9' * 21):
            with self.subTest(length=length):
                self.assertEqual(self.request(changes={'Content-Length': length}, send_body=False)[0], 400)
        for length in ('0', '4000001'):
            with self.subTest(length=length):
                self.assertEqual(self.request(changes={'Content-Length': length}, send_body=False)[0], 413)

    def test_truncated_and_invalid_json_return_controlled_errors(self):
        for body, length in [(b'{', '5'), (b'\xff', '1'), (b'[]', '2'), (b'{bad}', '5')]:
            with self.subTest(body=body):
                self.assertEqual(self.request(body=body, changes={'Content-Length': length},
                                              half_close=int(length) > len(body))[0], 400)
        self.assertEqual(self.server.handler_errors, [])

    def test_deep_json_returns_controlled_error(self):
        body = b'{"x":' * 4000 + b'0' + b'}' * 4000
        self.assertEqual(self.request(body=body)[0], 400)
        self.assertEqual(self.server.handler_errors, [])

    def test_missing_body_has_total_deadline(self):
        client = self.connect(body_length=2)
        started = time.monotonic()
        try:
            status, connection, _ = self.read_response(client)
            self.assertEqual(status, 408)
            self.assertEqual(connection, 'close')
            self.assertLess(time.monotonic() - started, .65)
        finally:
            client.close()

    def test_timeout_sends_response_before_bounded_receive_cleanup(self):
        client = self.connect(body_length=17)
        try:
            self.assertEqual(self.read_response(client)[0], 408)
            self.assertFalse(self.server.handler_finished.wait(.025),
                             'Timeout closed before in-flight body bytes could arrive.')
            client.sendall(b'x' * 17)
            self.assertTrue(self.server.handler_finished.wait(.75))
            self.assertEqual(self.server.handler_errors, [])
        finally:
            client.close()

    def test_trickled_body_cannot_restart_total_deadline(self):
        client = self.connect(body_length=200)
        stop = threading.Event()
        def trickle():
            deadline = time.monotonic() + .9
            try:
                while not stop.wait(.03) and time.monotonic() < deadline:
                    client.sendall(b' ')
                client.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        sender = threading.Thread(target=trickle, daemon=True)
        started = time.monotonic()
        sender.start()
        try:
            status, connection, _ = self.read_response(client)
            self.assertEqual(status, 408)
            self.assertEqual(connection, 'close')
            self.assertLess(time.monotonic() - started, .65)
        finally:
            stop.set()
            sender.join(1)
            client.close()
            self.assertFalse(sender.is_alive(), 'Owned trickle sender must exit.')


class BoundarySocket:
    """No I/O: only the socket timing/close seam used around a supplied body."""
    timeout = None
    def gettimeout(self):
        return self.timeout
    def settimeout(self, value):
        self.timeout = value
    def shutdown(self, _direction):
        pass
    def recv(self, _size):
        return b''


class TimedOutReader:
    """Model a buffered reader that cannot be reused after its first timeout."""
    def __init__(self, prefix=b''):
        self.prefix, self.calls, self.poisoned = prefix, 0, False
    def read1(self, _size):
        self.calls += 1
        if self.poisoned:
            raise AssertionError('Timed-out buffered reader was reused.')
        if self.prefix:
            prefix, self.prefix = self.prefix, b''
            return prefix
        self.poisoned = True
        raise TimeoutError('Synthetic body timeout.')


class DiscardSocket(BoundarySocket):
    def __init__(self, events, clock=None):
        self.events, self.clock, self.discarded = events, clock, 0
    def recv(self, size):
        if not self.events or self.events[0] != ('send', 408):
            raise AssertionError('Body discarded before the timeout response.')
        self.events.append(('recv', size))
        self.discarded += size
        if self.clock is not None:
            self.clock[0] += .1
        return b'x' * size


class HandlerBoundaryTests(unittest.TestCase):
    """App-owned decisions independent of host network-filter interception."""
    def request(self, *, changes=None, extras=(), body=b'{}', reader=None, connection=None, events=None):
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(server_port=12345, api_token='synthetic-token')
        handler.command, handler.path = 'POST', '/api/unknown'
        fields = {'Host': '127.0.0.1:12345', 'Origin': 'http://127.0.0.1:12345',
                  'X-Nivram-Token': 'synthetic-token', 'Content-Type': 'application/json',
                  'Content-Length': str(len(body))}
        fields.update(changes or {})
        handler.headers = http.client.HTTPMessage()
        for key, value in [*fields.items(), *extras]:
            if value is not None:
                handler.headers[key] = value
        handler.rfile, handler.wfile = reader or io.BytesIO(body), io.BytesIO()
        handler.connection = connection or BoundarySocket()
        receipts = []
        def send(status, payload, **options):
            receipts.append((status, payload))
            if events is not None:
                events.append(('send', status))
        handler._send = send
        handler.do_POST()
        self.assertEqual(len(receipts), 1)
        self.assertIn('error', receipts[0][1])
        return receipts[0][0]

    def test_transfer_encoding_and_duplicate_lengths_rejected_before_json(self):
        for changes, extras in [
            ({'Transfer-Encoding': 'chunked'}, []),
            ({'Transfer-Encoding': 'identity'}, []),
            ({'Transfer-Encoding': 'chunked', 'Content-Length': None}, []),
            ({}, [('Content-Length', '2')]),
            ({}, [('Content-Length', '3')]),
        ]:
            with self.subTest(changes=changes, extras=extras):
                with patch('nivram.server.json.loads', side_effect=AssertionError('Ambiguous body parsed')):
                    self.assertEqual(self.request(changes=changes, extras=extras), 400)

    def test_invalid_and_oversized_lengths_rejected_before_json(self):
        for length, expected in [(None, 400), ('', 400), ('+2', 400), ('-2', 400),
                                 ('2, 2', 400), ('2x', 400), ('9' * 21, 400),
                                 ('0', 413), ('4000001', 413)]:
            with self.subTest(length=length):
                with patch('nivram.server.json.loads', side_effect=AssertionError('Invalid body parsed')):
                    self.assertEqual(self.request(changes={'Content-Length': length}), expected)

    def test_security_header_cardinality_and_non_ascii_token(self):
        for name, value in [('Host', '127.0.0.1:12345'), ('Origin', 'http://127.0.0.1:12345'),
                            ('X-Nivram-Token', 'synthetic-token'), ('Content-Type', 'application/json')]:
            with self.subTest(name=name):
                self.assertEqual(self.request(extras=[(name, value)]), 415 if name == 'Content-Type' else 403)
        self.assertEqual(self.request(changes={'X-Nivram-Token': '\u00e9'}), 403)

    def test_deep_json_and_truncation_are_controlled_without_filter(self):
        body = b'{"x":' * 4000 + b'0' + b'}' * 4000
        self.assertEqual(self.request(body=body), 400)
        self.assertEqual(self.request(changes={'Content-Length': '20'}), 400)

    def test_timeout_discards_raw_bytes_after_response_without_reusing_reader(self):
        events, reader = [], TimedOutReader(prefix=b'abc')
        connection = DiscardSocket(events)
        self.assertEqual(self.request(changes={'Content-Length': '20'}, reader=reader,
                                      connection=connection, events=events), 408)
        self.assertEqual(reader.calls, 2)
        self.assertEqual(connection.discarded, 17)

    def test_timeout_raw_discard_has_byte_cap(self):
        events, reader = [], TimedOutReader()
        connection = DiscardSocket(events)
        self.assertEqual(self.request(changes={'Content-Length': '4000000'}, reader=reader,
                                      connection=connection, events=events), 408)
        self.assertEqual(reader.calls, 1)
        self.assertEqual(connection.discarded, REJECTION_DRAIN_BYTES)

    def test_timeout_raw_discard_has_total_deadline(self):
        events, reader, clock = [], TimedOutReader(), [0.0]
        connection = DiscardSocket(events, clock)
        with patch('nivram.server.time.monotonic', side_effect=lambda: clock[0]):
            self.assertEqual(self.request(changes={'Content-Length': '4000000'}, reader=reader,
                                          connection=connection, events=events), 408)
        self.assertEqual(reader.calls, 1)
        self.assertEqual(sum(event[0] == 'recv' for event in events), 3)
        self.assertLess(connection.discarded, REJECTION_DRAIN_BYTES)


if __name__ == '__main__':
    unittest.main()
