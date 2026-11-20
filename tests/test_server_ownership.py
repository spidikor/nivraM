"""The trusted loopback origin must have one owning Windows process."""
import socket
import sys
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from nivram.server import Handler, LocalServer


class LoopbackOwnershipTests(unittest.TestCase):
    def test_address_reuse_is_disabled(self):
        self.assertIs(LocalServer.allow_reuse_address, False)

    def test_exclusive_option_failure_stops_windows_bind(self):
        server = LocalServer.__new__(LocalServer)
        server.socket = Mock()
        server.socket.setsockopt.side_effect = OSError('exclusive ownership unavailable')
        with patch.object(sys, 'platform', 'win32'), \
                patch.object(socket, 'SO_EXCLUSIVEADDRUSE', 1, create=True), \
                patch.object(ThreadingHTTPServer, 'server_bind') as inherited_bind:
            with self.assertRaisesRegex(OSError, 'exclusive ownership unavailable'):
                server.server_bind()
        inherited_bind.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows socket ownership contract')
    def test_second_reuse_bind_to_live_endpoint_fails(self):
        server = LocalServer(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        competitor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(competitor.close)
        competitor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        with self.assertRaises(OSError):
            competitor.bind(server.server_address)


if __name__ == '__main__':
    unittest.main()
