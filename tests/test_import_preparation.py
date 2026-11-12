import copy
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from nivram import import_preparation as preparation
from nivram import import_protocol as protocol
from nivram.chemistry import import_structure, graph_to_mol, export_mol
from nivram.scene import render_scene
from nivram.worker_processes import CancellationToken, WorkerCancelled, WorkerProcessError


def example():
    graph = import_structure('CCO')
    return {'graph': graph, 'scene': render_scene(graph)}


def limit_receipt():
    return {'installed': True, 'platform': 'windows', 'mechanism': 'job_object',
            'scope': 'current_worker_process', 'memory_bytes': 1024**3,
            'cpu_user_time_100ns': 100_000_000, 'cpu_seconds': 10.0,
            'memory_metric': 'process_commit_bytes', 'cpu_metric': 'process_user_time',
            'active_process_limit': 1, 'kill_on_job_close': True,
            'handle_inheritable': False, 'assigned': True, 'verified': True, 'limit_flags': 0x210A}


def response(result=None):
    return {'schema': preparation.SCHEMA, 'status': 'ok', 'limits': limit_receipt(),
            'result': result or example()}


class FakeSupervisor:
    def __init__(self, reply=None):
        self.reply = reply or response()
        self.calls = []
        self.closed = False

    def check_cancelled(self, token=None):
        if self.closed or token is not None and token.cancelled:
            raise WorkerCancelled()

    def run(self, command, **kwargs):
        self.calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps(self.reply), '')

    def shutdown(self, **kwargs):
        self.closed = True
        return True


class ImportProtocolTests(unittest.TestCase):
    def test_normal_result_and_batch_metadata_preserved(self):
        value = example()
        self.assertIs(protocol.validate_prepared_result(value, mode='import'), value)
        project = {'schema': 'nivram/1', 'version': 'test', 'graph': value['graph'],
                   'style': value['scene']['style'], 'export': {'width': 32, 'marginMode': 'pixels', 'margin': 2},
                   'analysis': {'note': 'retained metadata'}}
        value['project'] = project
        self.assertIs(protocol.validate_prepared_result(value, mode='batch'), value)
        self.assertEqual(value['project']['analysis']['note'], 'retained metadata')

    def test_malformed_worker_data_rejected(self):
        mutations = (
            lambda r: r['graph']['atoms'][0].update(x=float('inf')),
            lambda r: r['graph']['atoms'][0].update(charge=True),
            lambda r: r['graph']['bonds'][0].update(begin='missing'),
            lambda r: r['scene'].update(width=6001),
            lambda r: r['scene'].update(svg='<svg><script>alert(1)</script></svg>'),
            lambda r: r['scene']['positions'].pop(),
            lambda r: r['scene']['properties']['inputIdentity'].update(graphHash='0'*64),
            lambda r: r['scene']['properties']['inputIdentity'].update(structureHash='0'*64),
            lambda r: r['scene']['properties'].update(smiles='CCN'),
            lambda r: r.update(unexpected=True),
        )
        baseline = example()
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = copy.deepcopy(baseline)
                mutate(value)
                with self.assertRaises(ValueError):
                    protocol.validate_prepared_result(value, mode='import')

    def test_batch_project_graph_must_match_scene(self):
        value = example()
        value['project'] = {'schema': 'nivram/1', 'graph': import_structure('CCN'),
                            'style': value['scene']['style']}
        with self.assertRaises(ValueError):
            protocol.validate_prepared_result(value, mode='batch')

    def test_settings_path_is_pure_even_with_profile(self):
        with patch('nivram.persistence.validate_project', side_effect=AssertionError('native validation')), \
             patch('nivram.scene.checked_style', side_effect=AssertionError('native module helper')):
            settings = protocol.checked_batch_settings({'profile': {'id': 'a'*32, 'revision': 1, 'name': ' Test '}})
        self.assertEqual(settings['profile']['name'], 'Test')
        self.assertEqual(settings['style'], protocol.DEFAULT_STYLE)

    def test_strict_json_rejects_duplicates_nonfinite_and_depth(self):
        for value in ('{"a":1,"a":2}', '[NaN]', '[Infinity]', '1e999', '['*66 + '0' + ']'*66):
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                protocol.strict_json_loads(value)

    def test_parent_modules_import_without_rdkit(self):
        root = Path(__file__).resolve().parents[1]
        code = ("import sys;sys.path.insert(0," + repr(str(root)) + ");"
                "import nivram.import_preparation,nivram.import_protocol;"
                "assert not any(x=='rdkit' or x.startswith('rdkit.') for x in sys.modules)")
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code], capture_output=True,
                                timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


class PreparationServiceTests(unittest.TestCase):
    def service(self, reply=None):
        supervisor = FakeSupervisor(reply)
        return preparation.ImportPreparationService(supervisor=supervisor, command_factory=lambda: ['owned-test-worker']), supervisor

    def test_valid_reply_requires_exact_verified_limits(self):
        service, supervisor = self.service()
        result = service.prepare_import('CCO')
        self.assertEqual(result['scene']['smiles'], 'CCO')
        self.assertEqual(supervisor.calls[0]['max_stdout_bytes'], 8*1024*1024)
        self.assertEqual(service.snapshot()['lastResourceLimits'], limit_receipt())

    def test_missing_wrong_or_boolean_numeric_limits_rejected(self):
        for field, value in (('verified', False), ('memory_bytes', 2*1024**3), ('active_process_limit', True),
                             ('installed', 1), ('handle_inheritable', True), ('limit_flags', 0x2000)):
            with self.subTest(field=field):
                reply = response()
                reply['limits'][field] = value
                service, _ = self.service(reply)
                with self.assertRaises(preparation.ImportPreparationError) as caught:
                    service.prepare_import('CCO')
                self.assertEqual(caught.exception.code, 'worker_failed')

    def test_capacity_rejected_without_queue_and_late_cancel_not_adopted(self):
        service, supervisor = self.service()
        entered, release = threading.Event(), threading.Event()
        original = supervisor.run
        def blocked(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(*args, **kwargs)
        supervisor.run = blocked
        token, outcomes = CancellationToken(), []
        def invoke():
            try:
                outcomes.append(service.prepare_import('CCO', cancellation_token=token))
            except WorkerCancelled:
                outcomes.append('cancelled')
        thread = threading.Thread(target=invoke)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(preparation.ImportPreparationError) as caught:
                service.prepare_import('CCO')
            self.assertEqual(caught.exception.code, 'busy')
            token.cancel()
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, ['cancelled'])
        self.assertEqual(len(supervisor.calls), 1)

    def test_shutdown_rejects_work_and_uncertain_cleanup_disables_service(self):
        service, supervisor = self.service()
        supervisor.run = Mock(side_effect=WorkerProcessError('private command and input'))
        with self.assertRaises(preparation.ImportPreparationError) as caught:
            service.prepare_import('CCO')
        self.assertNotIn('private', str(caught.exception))
        self.assertFalse(service.snapshot()['acceptingJobs'])
        with self.assertRaises(WorkerCancelled):
            service.prepare_import('CCO')
        self.assertTrue(service.shutdown())

    def test_source_and_style_reject_before_spawn(self):
        service, supervisor = self.service()
        for source, style in (('C'*4_000_001, None), ('C', {'bondWidth': True}), ('\ud800', None)):
            with self.assertRaises(preparation.ImportPreparationError):
                service.prepare_import(source, style)
        self.assertEqual(supervisor.calls, [])

    def test_worker_failure_messages_are_fixed_and_no_fallback(self):
        for code in ('invalid_input', 'empty_drawing', 'render_failed', 'unavailable', 'worker_failed'):
            service, _ = self.service({'schema': preparation.SCHEMA, 'status': 'error', 'code': code})
            with self.subTest(code=code), self.assertRaises(preparation.ImportPreparationError) as caught:
                service.prepare_import('CCO')
            self.assertEqual(caught.exception.code, code)

    def test_quota_failure_prevents_input_read_and_native_operation(self):
        from nivram.worker_resource_limits import ResourceLimitsUnavailable
        source = Mock()
        output = io.BytesIO()
        with patch('nivram.worker_resource_limits.install_worker_limits', side_effect=ResourceLimitsUnavailable()), \
             patch.object(preparation.sys, 'stdin', types.SimpleNamespace(buffer=source)), \
             patch.object(preparation.sys, 'stdout', types.SimpleNamespace(buffer=output)), \
             patch.object(preparation, '_prepare', side_effect=AssertionError('native operation')) as native:
            self.assertEqual(preparation.worker_main(), 0)
        source.read.assert_not_called()
        native.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['code'], 'unavailable')

    def test_verified_limits_precede_input_and_native_operation(self):
        events, output = [], io.BytesIO()
        class Input(io.BytesIO):
            def read(self, *args):
                events.append('input')
                return super().read(*args)
        def limits(**kwargs):
            events.append('limits')
            return limit_receipt()
        value = example()
        def native(request):
            events.append('native')
            return value
        request = {'schema': preparation.SCHEMA, 'mode': 'import', 'source': 'CCO', 'style': None}
        with patch('nivram.worker_resource_limits.install_worker_limits', side_effect=limits), \
             patch.object(preparation.sys, 'stdin', types.SimpleNamespace(buffer=Input(json.dumps(request).encode()))), \
             patch.object(preparation.sys, 'stdout', types.SimpleNamespace(buffer=output)), \
             patch.object(preparation, '_prepare', side_effect=native):
            self.assertEqual(preparation.worker_main(), 0)
        self.assertEqual(events, ['limits', 'input', 'native'])
        self.assertEqual(json.loads(output.getvalue())['status'], 'ok')

    def test_controlled_worker_faults_exit_and_allow_subsequent_work(self):
        programs = (
            "import time;time.sleep(10)",
            "print('not JSON')",
            "raise SystemExit(7)",
            "import sys;sys.stdout.buffer.write(bytes([255]))",
            "import sys;sys.stderr.buffer.write(b'x'*131072)",
        )
        valid = "print(" + repr(json.dumps(response())) + ")"
        for index, program in enumerate(programs):
            with self.subTest(index=index):
                selected = [program]
                service = preparation.ImportPreparationService(command_factory=lambda:
                    [sys.executable, '-I', '-S', '-B', '-c', selected[0]])
                try:
                    with patch.object(preparation, 'WALL_SECONDS', .5), self.assertRaises(preparation.ImportPreparationError):
                        service.prepare_import('CCO')
                    self.assertEqual(service._supervisor.active_count, 0)
                    selected[0] = valid
                    self.assertEqual(service.prepare_import('CCO')['scene']['smiles'], 'CCO')
                finally:
                    self.assertTrue(service.shutdown())


class HostImportBoundaryTests(unittest.TestCase):
    def test_http_import_does_not_call_parent_parser_or_renderer_or_take_chemistry_lock(self):
        from nivram import server as module
        server = module.make_server(0)
        real_service = getattr(server, 'import_preparation', None)
        expected = example()
        fake = Mock()
        fake.prepare_import.return_value = expected
        fake.shutdown.return_value = True
        fake.snapshot.return_value = {}
        server.import_preparation = fake
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            with patch.object(module, 'import_structure', side_effect=AssertionError('host parser'), create=True), \
                 patch.object(module, 'render_scene', side_effect=AssertionError('host render')), \
                 patch.object(module, 'LOCK', new=Mock(__enter__=Mock(side_effect=AssertionError('chemistry lock')),
                                                      __exit__=Mock(return_value=False))):
                connection.request('POST', '/api/import', json.dumps({'source': 'CCO'}),
                    {'Content-Type': 'application/json', 'X-Nivram-Token': server.api_token})
                received = connection.getresponse()
                status, data = received.status, json.loads(received.read())
            self.assertEqual(status, 200, data)
            self.assertEqual(data, expected)
            fake.prepare_import.assert_called_once_with('CCO', None)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(3)
            if real_service is not None:
                real_service.shutdown()


@unittest.skipUnless(os.name == 'nt', 'Hard preparation quotas require Windows.')
class RealPreparationTests(unittest.TestCase):
    def setUp(self):
        self.service = preparation.ImportPreparationService()

    def tearDown(self):
        self.assertTrue(self.service.shutdown())

    def test_real_worker_preserves_identity_stereo_and_mol_coordinates(self):
        from rdkit import Chem
        for source in ('CCO', '[13CH3][C@@H](O)C(=O)[O-]', 'F/C=C/F', 'F/C=C\\F', '[Na+].[Cl-]', '[CH3]'):
            with self.subTest(source=source):
                result = self.service.prepare_import(source)
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(result['graph']), True),
                                 Chem.MolToSmiles(Chem.MolFromSmiles(source), True))
        graph = import_structure('CCCO')
        graph['atoms'][0]['x'] += .22
        block = export_mol(graph)
        expected = import_structure(block)
        actual = self.service.prepare_import(block)['graph']
        self.assertEqual(actual, expected)
        self.assertTrue(self.service.snapshot()['lastResourceLimits']['verified'])

    def test_rejection_and_subsequent_valid_work(self):
        for source in ('CO(C)C', 'C'*513, '*', 'not a molecule'):
            with self.subTest(source=source[:40]), self.assertRaises(preparation.ImportPreparationError) as caught:
                self.service.prepare_import(source)
            self.assertEqual(caught.exception.code, 'invalid_input')
        self.assertEqual(self.service.prepare_import('CCO')['scene']['smiles'], 'CCO')

    def test_batch_preserves_supported_large_and_unicode_metadata(self):
        graph = import_structure('O')
        # Unknown graph metadata is retained by project storage and included in
        # graph identity. UTF-8 transport must not inflate it into ASCII escapes.
        graph['note'] = '\U0001f9ea' * 400_000
        analysis = {'k' + str(i): 0 for i in range(10_001)}
        analysis['long-key-' + 'x' * 1001] = 'retained'
        document = {'schema': 'nivram/1', 'graph': graph, 'analysis': analysis}
        source = json.dumps(document, ensure_ascii=False)
        self.assertLess(len(source.encode()), protocol.MAX_SOURCE_BYTES)
        result = self.service.prepare_batch(source, '.nivram', {})
        self.assertEqual(result['project']['analysis'], analysis)
        self.assertEqual(result['graph'], graph)
        self.assertEqual(result['project']['graph'], graph)

    def test_batch_validates_original_settings_and_rejects_disguised_mol(self):
        graph = import_structure('CCO')
        for text, extension in (('CCO', '.mol'), (export_mol(graph)+'$$$$\n', '.mol'),
                (json.dumps({'schema': 'nivram/1', 'graph': graph, 'style': {'bondWidth': 999}}), '.nivram')):
            with self.subTest(extension=extension), self.assertRaises(preparation.ImportPreparationError) as caught:
                self.service.prepare_batch(text, extension, {})
            self.assertEqual(caught.exception.code, 'invalid_input')

    def test_executable_dispatch_entry_uses_the_same_protected_operation(self):
        root = Path(__file__).resolve().parents[1]
        service = preparation.ImportPreparationService(command_factory=lambda:
            [sys.executable, '-B', '-s', str(root / 'run_worker.py'), '--prepare-import'])
        try:
            self.assertEqual(service.prepare_import('CCO')['scene']['smiles'], 'CCO')
            self.assertTrue(service.snapshot()['lastResourceLimits']['verified'])
        finally:
            self.assertTrue(service.shutdown())


if __name__ == '__main__':
    unittest.main()
