"""Offline HTTP integration: real handler/naming/comparison; controlled NISPO/OPSIN."""
import copy
import http.client
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from nivram.chemistry import import_structure
from nivram.naming import OpsinConfig, name_structure
from nivram.naming_runtime import NamingRuntime
from nivram.server import make_server

CASES = [('[13CH3]CO', 'ethanol', True), ('[CH3]', 'carbon', True), ('CCO', 'ethanol', False)]


class NamingAdoptionRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipts = []
        cls.temp = tempfile.TemporaryDirectory()
        with patch('nivram.naming_runtime.discover_naming_runtime', return_value=NamingRuntime('unavailable', None, 'Controlled runtime discovery.')):
            cls.server = make_server(0)
            assert cls.server.naming_runtime_ready.wait(2)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(3)
        cls.temp.cleanup()
        if os.environ.get('NIVRAM_NAMING_RECEIPTS'):
            Path(os.environ['NIVRAM_NAMING_RECEIPTS']).write_text(json.dumps(cls.receipts, indent=2))

    def request(self, graph, **extra):
        body = dict(graph=graph, documentRevision=17, clientId=secrets.token_hex(16),
                    jobId=secrets.token_hex(16), expiresAt=int(time.time()*1000)+30000, **extra)
        before = copy.deepcopy(body)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=8)
        try:
            conn.request('POST', '/api/name', json.dumps(body), {'Content-Type': 'application/json',
                         'X-Nivram-Token': self.server.api_token,
                         'Origin': f'http://127.0.0.1:{self.server.server_port}'})
            response = conn.getresponse()
            result = json.loads(response.read())
            status = response.status
        finally:
            conn.close()
        self.assertEqual(body, before)
        self.assertEqual(self.server.naming_jobs.snapshot()['activeJobs'], 0)
        self.assertEqual(self.server.worker_supervisor.active_count, 0)
        if status == 200:
            self.assertEqual(result['inputIdentity']['documentRevision'], 17)
            self.assertEqual(result['clientId'], body['clientId'])
            self.assertEqual(result['jobId'], body['jobId'])
        return status, result

    def test_actual_http_naming_status_matrix(self):
        for smiles, candidate, mandatory in CASES:
            for mode in ['unconfigured', 'missing', 'unavailable', 'warning', 'failed', 'invalid', 'mismatch', 'matched']:
                with self.subTest(smiles=smiles, mode=mode):
                    graph = import_structure(smiles)
                    original = copy.deepcopy(graph)
                    self.server.opsin_config = None if mode == 'unconfigured' else OpsinConfig(
                        Path(self.temp.name)/'missing-java', Path(self.temp.name)/'missing.jar')
                    parser = {'status': mode if mode in ['unavailable', 'warning', 'failed'] else 'parsed',
                              'smiles': 'not a SMILES' if mode == 'invalid' else 'CO' if mode == 'mismatch' else smiles,
                              'warnings': ['Controlled verifier diagnostic.'],
                              'engine': {'name': 'OPSIN', 'version': 'controlled'}}
                    parser_context = nullcontext() if mode in ['unconfigured', 'missing'] else patch('nivram.naming._run_opsin', return_value=parser)
                    with patch('nivram.naming._run_nispo', return_value={'status': 'generated', 'name': candidate,
                               'engine': {'name': 'NISPO', 'version': 'controlled'}}) as generator, parser_context:
                        status, result = self.request(graph, opsin_config={'java_path': 'client-override', 'jar_path': 'client-override'})
                    self.receipts.append(dict(smiles=smiles, mode=mode, mandatory=mandatory, candidate=candidate, graph=graph, result=result))
                    self.assertEqual(status, 200)
                    self.assertEqual(graph, original)
                    self.assertFalse(result['preferredIUPACName'])
                    self.assertEqual(result['transformations'], [])
                    if mode == 'unconfigured' and mandatory:
                        generator.assert_not_called()
                    if mode == 'matched':
                        self.assertEqual(result['status'], 'round_trip_matched')
                        self.assertEqual(result['roundTrip']['status'], 'matched')
                        self.assertEqual(result['name'], candidate)
                    elif mandatory:
                        self.assertEqual(result['status'], 'mismatch' if mode == 'mismatch' else 'unsupported')
                        self.assertIsNone(result['name'])
                        self.assertIsNone(result['generatedName'])
                        self.assertNotIn(candidate, json.dumps(result))
                    else:
                        self.assertEqual(result['status'], 'mismatch' if mode == 'mismatch' else 'generated_unverified')
                        self.assertEqual(result['name'], None if mode == 'mismatch' else candidate)
                        self.assertEqual(result['generatedName'], candidate)
                    if mode == 'missing':
                        self.assertEqual(result['roundTrip']['status'], 'unavailable')
                        self.assertIn('not installed', json.dumps(result['roundTrip']))

    def test_actual_handler_rejects_cancellation_after_real_matching(self):
        self.server.opsin_config = object()
        for smiles, candidate, _mandatory in CASES:
            def match_then_cancel(graph, revision, **kwargs):
                result = name_structure(graph, revision, **kwargs)
                self.assertEqual(result['status'], 'round_trip_matched')
                kwargs['cancellation_token'].cancel()
                return result
            with self.subTest(smiles=smiles), patch('nivram.naming.name_structure', side_effect=match_then_cancel), \
                 patch('nivram.naming._run_nispo', return_value={'status': 'generated', 'name': candidate, 'engine': {'name': 'controlled'}}), \
                 patch('nivram.naming._run_opsin', return_value={'status': 'parsed', 'smiles': smiles, 'warnings': [], 'engine': {'name': 'controlled'}}):
                status, result = self.request(import_structure(smiles))
            self.assertEqual(status, 409)
            self.assertEqual(result['status'], 'cancelled')
            self.assertNotIn(candidate, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
