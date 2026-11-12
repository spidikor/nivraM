"""Native setup transactions; verifier is mocked, selected code never executes."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from nivram import conditional_save
from nivram.image_session import ImageImportSession
from nivram.optical_jobs import MODEL_SHA256
from nivram.optical_setup import OpticalSetupStore
from nivram.persistence import atomic_write
from nivram.worker_processes import WorkerCancelled, WorkerProcessError


class OpticalSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.python = self.root / 'runtime' / 'python.exe'
        self.python.parent.mkdir()
        self.python.write_bytes(b'not executable; native-selected unit-test file')
        self.model = self.root / 'model.pth'
        self.model.write_bytes(b'unit-test model; verifier mocked')
        self.images = ImageImportSession(self.root / 'profile' / 'import-sources')
        self.store = OpticalSetupStore(self.root / 'profile', self.images)
        self.path = self.root / 'profile' / 'optical-runtime' / 'runtime.json'
        self.verify_patch = patch('nivram.optical_setup.verify_optical_model')
        self.verify = self.verify_patch.start()
        self.process_patch = patch('subprocess.Popen', side_effect=AssertionError('Setup must not execute selected code'))
        self.process_patch.start()

    def tearDown(self):
        self.store.close()
        self.images.close()
        self.process_patch.stop()
        self.verify_patch.stop()
        self.temp.cleanup()

    def stage(self):
        state = self.store.begin()
        self.store.choose(state['setupId'], 'python', self.python)
        self.store.choose(state['setupId'], 'model', self.model)
        return state['setupId']

    def saved(self):
        result = self.store.save(self.stage())
        self.assertEqual(result['status'], 'saved')
        return self.path.read_bytes()

    def test_native_choices_verify_and_save_exact_schema_without_executing_runtime(self):
        setup = self.stage()
        result = self.store.save(setup)
        self.assertEqual(set(result), {'status', 'setupId', 'opticalRecognition', 'modelVerified', 'runtimeCompatibility'})
        self.assertTrue(result['modelVerified'])
        self.assertEqual(result['runtimeCompatibility'], 'checked-on-recognition')
        self.verify.assert_called_once()
        self.assertEqual(self.verify.call_args.args, (self.model,))
        self.assertIs(self.verify.call_args.kwargs['supervisor'], self.store._supervisor)
        self.assertEqual(json.loads(self.path.read_bytes()), {
            'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': MODEL_SHA256,
            'python': str(self.python), 'model': str(self.model)})
        self.assertEqual(self.images._optical_jobs._python, self.python)
        self.assertNotIn(str(self.root), json.dumps(result))
        with self.assertRaises(ValueError):
            self.store.save(setup)

    def test_cancelled_chooser_preserves_staged_choices_and_explicit_cancel_never_writes(self):
        setup = self.stage()
        state = self.store.choose(setup, 'python', None)
        self.assertEqual((state['status'], state['runtimeName'], state['modelName']),
                         ('cancelled', 'python.exe', 'model.pth'))
        self.store.cancel(setup)
        with self.assertRaises(ValueError):
            self.store.choose(setup, 'model', self.model)
        self.assertFalse(self.path.exists())
        self.verify.assert_not_called()

    def test_invalid_kind_or_stale_dialog_result_cannot_change_choices(self):
        setup = self.stage()
        for kind in ('command', '', None, [], '../python'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.store.ensure_choice(setup, kind)
        self.store.begin_page()
        with self.assertRaises(ValueError):
            self.store.choose(setup, 'python', self.python)
        self.assertFalse(self.path.exists())

    def test_local_regular_python_requirement_rejects_remote_relative_and_wrong_names(self):
        setup = self.store.begin()['setupId']
        wrong = self.root / 'not-python.exe'
        wrong.write_bytes(b'bad name')
        for selected in ('python.exe', '\\\\server\\share\\python.exe', '\\\\?\\C:\\python.exe',
                         'https://host/python.exe', wrong, self.python.parent, str(self.python) + '\x00'):
            with self.subTest(selected=str(selected)), self.assertRaises(ValueError):
                self.store.choose(setup, 'python', selected)
        self.assertEqual(self.store.choose(setup, 'python', None)['runtimeName'], '')
        self.verify.assert_not_called()

    def test_invalid_model_and_timeout_keep_previous_settings_and_owner(self):
        baseline = self.saved()
        owner = self.images._optical_jobs
        for failure in (ValueError('Pinned model mismatch.'), subprocess.TimeoutExpired('verifier', 90)):
            setup = self.stage()
            self.verify.side_effect = failure
            with self.subTest(failure=type(failure).__name__), self.assertRaises(ValueError):
                self.store.save(setup)
            self.assertEqual(self.path.read_bytes(), baseline)
            self.assertIs(self.images._optical_jobs, owner)
            self.assertFalse(owner._closed)

    def test_runtime_is_rechecked_after_verification_without_running_it(self):
        setup = self.stage()
        self.verify.side_effect = lambda *args, **kwargs: self.python.unlink()
        with self.assertRaises(ValueError):
            self.store.save(setup)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.images._optical_jobs)

    def test_external_settings_change_before_or_during_verification_is_not_overwritten(self):
        baseline = self.saved()
        for during in (False, True):
            self.path.write_bytes(baseline)
            setup = self.stage()
            other = b'{"external":true}'
            self.verify.reset_mock(side_effect=True)
            if during:
                self.verify.side_effect = lambda *a, **kw: self.path.write_bytes(other)
            else:
                self.path.write_bytes(other)
            with self.subTest(during=during), self.assertRaisesRegex(ValueError, 'changed elsewhere'):
                self.store.save(setup)
            self.assertEqual(self.path.read_bytes(), other)
            if not during:
                self.verify.assert_not_called()

    def test_write_failure_keeps_existing_owner_and_exact_disk_bytes(self):
        baseline = self.saved()
        owner = self.images._optical_jobs
        setup = self.stage()
        with patch('nivram.optical_setup.atomic_write', side_effect=OSError('private path')):
            with self.assertRaisesRegex(ValueError, 'Previous settings were retained'):
                self.store.save(setup)
        self.assertEqual(self.path.read_bytes(), baseline)
        self.assertIs(self.images._optical_jobs, owner)
        self.assertFalse(owner._closed)

    def test_success_reconfigures_same_healthy_owner_for_future_work(self):
        self.saved()
        owner = self.images._optical_jobs
        replacement = self.root / 'replacement.pth'
        replacement.write_bytes(b'new mocked model')
        setup = self.stage()
        self.store.choose(setup, 'model', replacement)
        result = self.store.save(setup)
        self.assertEqual(result['status'], 'saved')
        self.assertIs(self.images._optical_jobs, owner)
        self.assertFalse(owner._closed)
        self.assertEqual(owner._model, replacement)
        self.assertEqual(owner._command()[-1], str(replacement))

    def test_closed_optical_owner_is_not_resurrected(self):
        baseline = self.saved()
        owner = self.images._optical_jobs
        setup = self.stage()
        owner.close()
        self.verify.reset_mock()
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.store.save(setup)
        self.verify.assert_not_called()
        self.assertEqual(self.path.read_bytes(), baseline)
        self.assertIs(self.images._optical_jobs, owner)

    def test_post_replace_failure_restores_old_bytes_before_reporting_failure(self):
        baseline = self.saved()
        new_model = self.root / 'new-model.pth'
        new_model.write_bytes(b'unit-test replacement')
        setup = self.stage()
        self.store.choose(setup, 'model', new_model)
        calls = []
        replace = conditional_save.conditional_write_existing
        def failing_write(path, data, expected, backup):
            result = replace(path, data, expected, backup)
            calls.append(data)
            if len(calls) == 1:
                raise OSError('Failure after publication')
            return result
        with patch('nivram.optical_setup._conditional.conditional_write_existing', side_effect=failing_write):
            with self.assertRaisesRegex(ValueError, 'Previous settings were retained'):
                self.store.save(setup)
        self.assertEqual(self.path.read_bytes(), baseline)
        self.assertEqual(self.images._optical_jobs._model, self.model)

    def test_external_change_after_uncertain_write_is_not_rolled_back_or_claimed_unchanged(self):
        self.saved()
        setup = self.stage()
        other = b'{"external":"newer"}'
        def failing_write(path, data, expected, backup):
            path.write_bytes(other)
            raise OSError('External replacement')
        with patch('nivram.optical_setup._conditional.conditional_write_existing', side_effect=failing_write):
            with self.assertRaisesRegex(ValueError, 'could not be confirmed'):
                self.store.save(setup)
        self.assertEqual(self.path.read_bytes(), other)

    def test_new_settings_publication_refuses_a_concurrent_creator(self):
        setup = self.stage()
        other = b'{"external":true}'
        def create_before_write(path, data, **kwargs):
            path.write_bytes(other)
            return atomic_write(path, data, **kwargs)
        with patch('nivram.optical_setup.atomic_write', side_effect=create_before_write):
            with self.assertRaisesRegex(ValueError, 'could not be confirmed'):
                self.store.save(setup)
        self.assertEqual(self.path.read_bytes(), other)
        self.assertIsNone(self.images._optical_jobs)

    def test_cancel_page_change_and_close_fence_inflight_verification(self):
        for action in ('cancel', 'begin_page', 'close'):
            with self.subTest(action=action):
                setup = self.stage()
                started, release = threading.Event(), threading.Event()
                failures = []
                def verify(model, *, token, supervisor):
                    started.set()
                    if not release.wait(2):
                        raise AssertionError('Test verifier release timed out')
                    if token.cancelled:
                        raise WorkerCancelled('cancelled')
                self.verify.side_effect = verify
                def save():
                    try:
                        self.store.save(setup)
                    except Exception as error:
                        failures.append(error)
                thread = threading.Thread(target=save)
                thread.start()
                try:
                    self.assertTrue(started.wait(2))
                    if action == 'cancel':
                        self.store.cancel(setup)
                    else:
                        getattr(self.store, action)()
                finally:
                    release.set()
                    thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(failures), 1)
                self.assertIsInstance(failures[0], ValueError)
                self.assertFalse(self.path.exists())
                self.assertIsNone(self.images._optical_jobs)

    def test_cancel_after_new_publication_retains_file_and_reports_uncertain_outcome(self):
        setup = self.stage()
        def cancel_after_write(path, data, **kwargs):
            atomic_write(path, data, **kwargs)
            self.store.begin_page()
        with patch('nivram.optical_setup.atomic_write', side_effect=cancel_after_write):
            with self.assertRaisesRegex(ValueError, 'could not be confirmed'):
                self.store.save(setup)
        self.assertEqual(json.loads(self.path.read_bytes())['model'], str(self.model))
        self.assertIsNone(self.images._optical_jobs)

    def test_existing_publication_rechecks_expected_bytes_under_conditional_writer(self):
        baseline = self.saved()
        owner = self.images._optical_jobs
        setup = self.stage()
        other = b'{"external":"at publication"}'
        replace = conditional_save.conditional_write_existing
        def competing_write(path, data, expected, backup):
            self.assertEqual(path.read_bytes(), baseline)
            path.write_bytes(other)
            return replace(path, data, expected, backup)
        with patch('nivram.optical_setup._conditional.conditional_write_existing', side_effect=competing_write):
            with self.assertRaisesRegex(ValueError, 'changed elsewhere'):
                self.store.save(setup)
        self.assertEqual(self.path.read_bytes(), other)
        self.assertIs(self.images._optical_jobs, owner)
        self.assertFalse(self.path.with_suffix('.json.bak').exists())

    def test_rollback_cannot_overwrite_a_change_after_its_precheck(self):
        baseline = self.saved()
        replacement = self.root / 'replacement.pth'
        replacement.write_bytes(b'new mocked model')
        setup = self.stage()
        self.store.choose(setup, 'model', replacement)
        other = b'{"external":"during rollback"}'
        replace = conditional_save.conditional_write_existing
        calls = []
        def competing_write(path, data, expected, backup):
            calls.append(data)
            if len(calls) == 1:
                replace(path, data, expected, backup)
                raise OSError('Failure after publication')
            path.write_bytes(other)
            return replace(path, data, expected, backup)
        with patch('nivram.optical_setup._conditional.conditional_write_existing', side_effect=competing_write):
            with self.assertRaisesRegex(ValueError, 'could not be confirmed'):
                self.store.save(setup)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.path.read_bytes(), other)
        self.assertEqual(self.path.with_suffix('.json.bak').read_bytes(), baseline)
        self.assertEqual(self.images._optical_jobs._model, self.model)

    def test_existing_settings_have_exact_durable_backup_before_publication(self):
        baseline = self.saved()
        replacement = self.root / 'replacement.pth'
        replacement.write_bytes(b'new mocked model')
        setup = self.stage()
        self.store.choose(setup, 'model', replacement)
        self.store.save(setup)
        self.assertEqual(self.path.with_suffix('.json.bak').read_bytes(), baseline)
        self.assertEqual(json.loads(self.path.read_bytes())['model'], str(replacement))

    def test_unsupported_conditional_filesystem_has_no_unconditional_fallback(self):
        baseline = self.saved()
        setup = self.stage()
        with patch('nivram.optical_setup._conditional.conditional_write_existing',
                   side_effect=conditional_save.SaveUnsupported('Unsupported test location')), \
                patch('nivram.optical_setup.atomic_write') as unconditional:
            with self.assertRaisesRegex(ValueError, 'does not support safe replacement'):
                self.store.save(setup)
        unconditional.assert_not_called()
        self.assertEqual(self.path.read_bytes(), baseline)

    def test_uncertain_verifier_owner_is_retired_and_never_reused(self):
        baseline = self.saved()
        setup = self.stage()
        supervisor = self.store._supervisor
        self.verify.reset_mock()
        self.verify.side_effect = WorkerProcessError('Private process details must not escape')
        with patch.object(supervisor, 'shutdown', wraps=supervisor.shutdown) as shutdown:
            with self.assertRaisesRegex(ValueError, '^Model verification could not complete safely\\.') as raised:
                self.store.save(setup)
            shutdown.assert_called_once_with()
        self.assertNotIn('Private', str(raised.exception))
        self.assertTrue(self.store._closed)
        self.assertTrue(supervisor.closed)
        self.assertIsNone(self.store._active)
        self.assertIsNone(self.store._state)
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.store.begin()
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.store.save(setup)
        self.verify.assert_called_once()
        self.assertEqual(self.path.read_bytes(), baseline)

    def test_closed_image_service_rejects_setup_without_verification_or_write(self):
        setup = self.stage()
        self.images.close()
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.store.save(setup)
        self.verify.assert_not_called()
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
