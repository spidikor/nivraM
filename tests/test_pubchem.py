import copy
import io
import json
import socket
import subprocess
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

from nivram import pubchem
from nivram.chemistry import import_structure
from nivram.worker_processes import CancellationToken, WorkerCancelled, WorkerOutputLimit


def provider_body(smiles='CCO', name='ethanol', cid=702):
    return json.dumps({'PropertyTable': {'Properties': [
        {'CID': cid, 'SMILES': smiles, 'IUPACName': name}]}}).encode()


def worker_result(smiles='CCO', name='ethanol', cid=702):
    return {'status': 'ok', 'records': [{'CID': cid, 'SMILES': smiles, 'IUPACName': name}],
            'retrievedAt': '2026-09-21T12:00:00+00:00', 'errorCode': None}


class Response:
    def __init__(self, raw, *, status=200, url=None):
        self.raw, self.status, self.url = raw, status, url or pubchem.ENDPOINT
        self.read_sizes, self.closed = [], False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def geturl(self):
        return self.url

    def read(self, limit):
        self.read_sizes.append(limit)
        return self.raw[:limit]


class PubChemTests(unittest.TestCase):
    def setUp(self):
        self.guards = ExitStack()
        self.addCleanup(self.guards.close)
        self.guards.enter_context(patch.object(socket, 'socket', side_effect=AssertionError('No network in tests')))
        self.guards.enter_context(patch.object(subprocess, 'Popen', side_effect=AssertionError('No child in tests')))

    def submission(self, smiles='CCO'):
        return pubchem.prepare_submission(import_structure(smiles), 17)

    def lookup(self, original='CCO', returned='CCO', *, data=None, side_effect=None):
        runner = Mock()
        runner.run.side_effect = side_effect
        runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(
            worker_result(returned) if data is None else data), stderr='')
        submission = self.submission(original)
        before = copy.deepcopy(submission)
        result = pubchem.lookup_submission(submission, supervisor=runner, cancellation_token=CancellationToken())
        self.assertEqual(submission, before)
        return result, runner

    def test_prepare_is_local_exact_disclosure_and_detached(self):
        graph = import_structure('C[C@H](O)F')
        before = copy.deepcopy(graph)
        with patch.object(pubchem, '_request_smiles', side_effect=AssertionError('Must remain local')):
            prepared = pubchem.prepare_submission(graph, 'revision-4')
        self.assertEqual(graph, before)
        self.assertIsNot(graph, prepared['graph'])
        self.assertEqual(prepared['disclosure']['submittedSmiles'], prepared['inputIdentity']['smiles'])
        self.assertEqual(prepared['disclosure']['endpoint'], pubchem.ENDPOINT)
        self.assertEqual(prepared['inputIdentity']['documentRevision'], 'revision-4')
        self.assertIn('free of charge', prepared['disclosure']['cost'])
        self.assertTrue(any('standardiz' in x.lower() for x in prepared['disclosure']['warnings']))

    def test_empty_graph_rejected_locally(self):
        with self.assertRaises(ValueError):
            pubchem.prepare_submission({'atoms': [], 'bonds': []}, 1)

    def test_lookup_worker_gets_only_disclosed_smiles_and_limits(self):
        result, runner = self.lookup()
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['name'], 'ethanol')
        self.assertEqual(result['nameKind'], 'retrieved_database_iupac')
        self.assertIs(result['preferredIUPACName'], False)
        args, kw = runner.run.call_args
        self.assertEqual(args[0][-3:], ['-m', 'nivram.pubchem', '--worker'])
        self.assertEqual(json.loads(kw['input_text']), {'smiles': 'CCO'})
        self.assertLessEqual(kw['timeout_seconds'], 20)
        self.assertLessEqual(kw['max_input_bytes'], 65536)
        self.assertLessEqual(kw['max_stdout_bytes'], 131072)
        self.assertEqual(runner.run.call_count, 1)
        record = result['records'][0]
        self.assertEqual((record['CID'], record['submittedSmiles'], record['returnedSmiles']), (702, 'CCO', 'CCO'))
        self.assertEqual(record['retrievedAt'], '2026-09-21T12:00:00+00:00')

    def test_supported_identity_dimensions_must_match(self):
        for original, returned in [('F/C=C/F', 'F/C=C\\F'), ('C[C@H](O)F', 'C[C@@H](O)F'),
                                   ('C[C@H](O)F', 'CC(O)F'), ('[13CH3]CO', 'CCO'),
                                   ('C[NH3+]', 'CN'), ('CC=O', 'C=CO'), ('CCO.O', 'CCO'),
                                   ('[CH3]', 'C')]:
            with self.subTest(original=original):
                result, _ = self.lookup(original, returned)
                self.assertEqual(result['status'], 'mismatch')
                self.assertIsNone(result['name'])

    def test_fragment_order_matches_without_dropping_fragments(self):
        result, _ = self.lookup('[Na+].[Cl-]', '[Cl-].[Na+]')
        self.assertEqual(result['status'], 'matched')

    def test_maps_remain_annotation_and_are_disclosed(self):
        result, _ = self.lookup('[CH3:7]CO', 'CCO')
        self.assertEqual(result['status'], 'matched')
        self.assertTrue(any('map' in x.lower() for x in result['records'][0]['match']['warnings']))
        self.assertIn(':7', result['inputIdentity']['smiles'])

    def test_ambiguous_records_never_promote_name(self):
        data = worker_result()
        data['records'].append({'CID': 123, 'SMILES': 'CO', 'IUPACName': 'methanol'})
        result, _ = self.lookup(data=data)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertIsNone(result['name'])

    def test_invalid_returned_chemistry_cannot_match(self):
        result, _ = self.lookup(returned='not-a-SMILES')
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['name'])

    def test_submission_disclosure_tamper_rejected_before_worker(self):
        prepared = self.submission()
        prepared['disclosure']['submittedSmiles'] = 'CO'
        runner = Mock()
        with self.assertRaises(ValueError):
            pubchem.lookup_submission(prepared, supervisor=runner, cancellation_token=CancellationToken())
        runner.run.assert_not_called()

    def test_cancel_propagates_and_is_checked_after_worker(self):
        for when in ('before', 'after'):
            runner = Mock()
            runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(worker_result()), stderr='')
            runner.check_cancelled.side_effect = [WorkerCancelled('cancel')] if when == 'before' else [None, WorkerCancelled('cancel')]
            with self.subTest(when=when), self.assertRaises(WorkerCancelled):
                pubchem.lookup_submission(self.submission(), supervisor=runner, cancellation_token=CancellationToken())
            self.assertEqual(runner.run.call_count, 0 if when == 'before' else 1)

    def test_timeout_and_output_failure_never_leak_exception_or_retry(self):
        for error in (subprocess.TimeoutExpired(['PRIVATE-COMMAND'], 15, output='PRIVATE-SMILES'),
                      WorkerOutputLimit('PRIVATE-SMILES'), OSError('PRIVATE-PATH')):
            with self.subTest(error=type(error).__name__):
                result, runner = self.lookup(side_effect=error)
                self.assertIn(result['status'], ('unavailable', 'failed'))
                self.assertIsNone(result['name'])
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertEqual(runner.run.call_count, 1)

    def test_untrusted_worker_output_is_bounded_and_not_echoed(self):
        for output in ('PRIVATE' * 25000, 'PRIVATE-invalid-json', json.dumps({'status': 'PRIVATE'})):
            runner = Mock()
            runner.run.return_value = SimpleNamespace(returncode=0, stdout=output, stderr='PRIVATE-STDERR')
            result = pubchem.lookup_submission(self.submission(), supervisor=runner, cancellation_token=CancellationToken())
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('PRIVATE', json.dumps(result))

    def request(self, response=None, error=None):
        opener = Mock()
        opener.open.side_effect = error
        opener.open.return_value = response or Response(provider_body())
        with patch.object(pubchem, '_make_opener', return_value=opener):
            result = pubchem._request_smiles('CCO')
        return result, opener

    def test_fixed_https_post_exact_form_and_bounded_read(self):
        response = Response(provider_body())
        result, opener = self.request(response)
        self.assertEqual(result['status'], 'ok')
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, pubchem.ENDPOINT)
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(parse_qs(req.data.decode()), {'smiles': ['CCO']})
        self.assertNotIn('Cookie', req.headers)
        self.assertNotIn('Authorization', req.headers)
        self.assertEqual(opener.open.call_count, 1)
        self.assertLessEqual(opener.open.call_args.kwargs['timeout'], 15)
        self.assertEqual(response.read_sizes, [131073])
        self.assertTrue(response.closed)

    def test_redirect_handler_forbids_following(self):
        response_stream = io.BytesIO(b'PRIVATE-redirect-body')
        with self.assertRaises(pubchem._RedirectRejected):
            pubchem._NoRedirect().redirect_request(None, response_stream, 302, 'PRIVATE', {}, 'https://other.test/')
        self.assertTrue(response_stream.closed)

    def test_transport_uses_default_tls_without_ambient_proxy_or_cookie_handlers(self):
        context = object()
        with patch.object(pubchem.ssl, 'create_default_context', return_value=context) as tls, \
                patch.object(pubchem, 'build_opener', return_value='opener') as build:
            self.assertEqual(pubchem._make_opener(), 'opener')
        tls.assert_called_once_with()
        handlers = build.call_args.args
        self.assertEqual(len(handlers), 3)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], pubchem._NoRedirect)
        self.assertIs(handlers[2]._context, context)

    def test_timeout_tls_failure_and_unavailable_responses_are_private(self):
        for error in (TimeoutError('PRIVATE'), URLError('PRIVATE'), pubchem.ssl.SSLError('PRIVATE')):
            result, opener = self.request(error=error)
            self.assertEqual(result['status'], 'unavailable')
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertEqual(opener.open.call_count, 1)

    def test_empty_many_records_and_untrusted_timestamp(self):
        for records, expected in ([], 'not_found'), ([{'CID': i + 1, 'SMILES': 'CCO', 'IUPACName': 'ethanol'} for i in range(9)], 'ambiguous'):
            result, _ = self.request(Response(json.dumps({'PropertyTable': {'Properties': records}}).encode()))
            self.assertEqual(result['status'], expected)
        for timestamp in ('PRIVATE', '2026-09-21T12:00:00', '2026-09-21T12:00:00+06:00'):
            data = worker_result()
            data['retrievedAt'] = timestamp
            result, _ = self.lookup(data=data)
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_frozen_worker_uses_explicit_packaged_route_or_reports_unavailable(self):
        runner = Mock()
        runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(worker_result()), stderr='')
        with patch.object(pubchem.sys, 'frozen', True, create=True), \
                patch.object(pubchem.sys, 'executable', 'C:/synthetic/nivraM.exe'), \
                patch.object(pubchem.Path, 'is_file', return_value=True):
            result = pubchem.lookup_submission(self.submission(), supervisor=runner, cancellation_token=CancellationToken())
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(runner.run.call_args.args[0], ['C:\\synthetic\\nivram-worker.exe', '--pubchem'])
        runner.reset_mock()
        with patch.object(pubchem.sys, 'frozen', True, create=True), patch.object(pubchem.Path, 'is_file', return_value=False):
            result = pubchem.lookup_submission(self.submission(), supervisor=runner, cancellation_token=CancellationToken())
        self.assertEqual(result['status'], 'unavailable')
        runner.run.assert_not_called()

    def test_url_change_202_oversize_and_invalid_body_never_accepted(self):
        for response in (Response(provider_body(), url='https://other.test/'),
                         Response(provider_body(), status=202), Response(b'x' * 131073),
                         Response(b'PRIVATE HTML'), Response(b'[]')):
            with self.subTest(status=response.status, size=len(response.raw)):
                result, opener = self.request(response)
                self.assertNotEqual(result['status'], 'ok')
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertTrue(response.closed)
                self.assertEqual(opener.open.call_count, 1)

    def test_http_errors_have_fixed_status_without_body_read_or_retry(self):
        for status, expected in [(404, 'not_found'), (429, 'unavailable'), (503, 'unavailable'), (400, 'failed'), (302, 'failed')]:
            body = io.BytesIO(b'PRIVATE-provider-error')
            error = HTTPError(pubchem.ENDPOINT, status, 'PRIVATE', {}, body)
            with self.subTest(status=status):
                result, opener = self.request(error=error)
                self.assertEqual(result['status'], expected)
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertTrue(body.closed)
                self.assertEqual(opener.open.call_count, 1)

    def test_malformed_records_rejected(self):
        for value in (True, 0, -1, '702', None):
            result, _ = self.request(Response(provider_body(cid=value)))
            self.assertEqual(result['status'], 'failed')
        for name in ('', ' ', 'bad\nname', 'x' * 16385, {'nested': 'private'}):
            result, _ = self.request(Response(provider_body(name=name)))
            self.assertEqual(result['status'], 'failed')
        for smiles in ('', 'CCO hidden-name', 'x' * 16385, {'nested': 'private'}):
            result, _ = self.request(Response(provider_body(smiles=smiles)))
            self.assertEqual(result['status'], 'failed')

    def test_worker_rejects_nested_empty_oversized_or_extra_inputs_without_request(self):
        for payload in ({'smiles': {}}, {'smiles': ''}, {'smiles': 'x' * 16385},
                        {'smiles': 'CCO', 'endpoint': 'https://other.test/'}, []):
            stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))
            stdout = io.StringIO()
            with patch.object(pubchem.sys, 'stdin', stdin), patch.object(pubchem.sys, 'stdout', stdout), \
                    patch.object(pubchem, '_request_smiles') as request:
                self.assertNotEqual(pubchem.worker_main(), 0)
            request.assert_not_called()
            self.assertEqual(json.loads(stdout.getvalue())['status'], 'failed')

    def test_worker_sends_only_valid_smiles_and_fixed_json(self):
        with patch.object(pubchem.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{"smiles":"CCO"}'))), \
                patch.object(pubchem.sys, 'stdout', io.StringIO()) as out, \
                patch.object(pubchem, '_request_smiles', return_value=worker_result()) as request:
            self.assertEqual(pubchem.worker_main(), 0)
        request.assert_called_once_with('CCO')
        self.assertEqual(json.loads(out.getvalue()), worker_result())


if __name__ == '__main__':
    unittest.main()
