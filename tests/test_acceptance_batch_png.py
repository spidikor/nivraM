import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from nivram.acceptance_batch_png import (
    NativeDialogObserver, assert_native_selection, validate_artifacts, validate_receipt,
    dialog_diagnostic, ui_diagnostic, capture_wait_failure, retain_dialog_records,
    validate_ui, _json_object, _ordinary_child, _read_owned, _write_fresh,
    VALID_NAME, INVALID_NAME, SENTINEL_NAME, OUTPUT_NAME, MANIFEST_NAME,
    SENTINEL_BYTES, INVALID_BYTES, INVALID_MESSAGE, OPTIONS, ACTIVE_OPTIONS,
)


def synthetic_png():
    stream = BytesIO()
    with Image.new('RGBA', (640, 64), 'white') as image:
        image.putpixel((40, 32), (17, 47, 139, 255))
        image.save(stream, format='PNG')
    return stream.getvalue()


def baseline_state():
    return {'snapshot': {'document': {'schema': 'nivram/1',
        'graph': {'atoms': [{'id': 'a', 'symbol': 'C', 'x': 1, 'y': 2, 'charge': 0}], 'bonds': []},
        'style': {'bondWidth': 4.2}, 'export': dict(ACTIVE_OPTIONS)},
        'revision': 8, 'dirty': True, 'savedIdentity': None, 'recoveryGeneration': 3},
        'drawing': '<g id="drawing"><path d="M1 2L4 5"/></g>', 'undoDisabled': False, 'redoDisabled': True}


def settings():
    return {'style': {'bondWidth': 4.2, 'labelSize': 18, 'bondSpacing': .18,
                     'labelPadding': .06, 'color': True, 'hashBondStyle': 'spaced'},
            'export': dict(OPTIONS), 'profile': {'id': 'd'*32, 'revision': 1, 'name': 'Synthetic native batch PNG'}}


class BatchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixtures = Path(self.temporary.name) / 'synthetic fixtures'
        self.fixtures.mkdir()
        self.output = self.fixtures / 'batch PNG outputs \u00fc'
        self.output.mkdir()
        self.source_bytes = {VALID_NAME: b'Owned synthetic project bytes for evidence-helper tests.\n', INVALID_NAME: INVALID_BYTES}
        for name, data in self.source_bytes.items():
            (self.fixtures / name).write_bytes(data)
        self.png = synthetic_png()
        (self.output / SENTINEL_NAME).write_bytes(SENTINEL_BYTES)
        (self.output / OUTPUT_NAME).write_bytes(self.png)
        self.settings = settings()
        self.receipt = {'sessionId': 'c'*32, 'status': 'completed', 'settings': copy.deepcopy(self.settings),
            'manifestName': MANIFEST_NAME, 'manifestError': None, 'items': [
                {'id': 'a'*32, 'name': VALID_NAME, 'extension': '.nivram', 'status': 'exported',
                 'sourceSha256': hashlib.sha256(self.source_bytes[VALID_NAME]).hexdigest(),
                 'outputName': OUTPUT_NAME, 'outputSha256': hashlib.sha256(self.png).hexdigest(),
                 'dimensions': {'width': 640, 'height': 64, 'padding': 12}, 'error': None},
                {'id': 'b'*32, 'name': INVALID_NAME, 'extension': '.mol', 'status': 'failed',
                 'sourceSha256': hashlib.sha256(INVALID_BYTES).hexdigest(), 'outputName': None,
                 'outputSha256': None, 'dimensions': None,
                 'error': {'code': 'invalid_input', 'message': INVALID_MESSAGE}},
            ]}
        self.state = baseline_state()
        self.write_manifest()

    def write_manifest(self, receipt=None):
        value = {'schema': 'nivram-batch-export/1', **(self.receipt if receipt is None else receipt)}
        (self.output / MANIFEST_NAME).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return value

    def validate(self, receipt=None, after=None):
        return validate_artifacts(self.fixtures, self.output, self.receipt if receipt is None else receipt,
            self.settings, self.source_bytes, self.state, self.state if after is None else after)

    def test_complete_real_file_evidence_preserves_bytes_and_full_editor_state(self):
        before = {path: path.read_bytes() for path in (*self.fixtures.glob('*.mol'), *self.fixtures.glob('*.nivram'), *self.output.iterdir())}
        metadata, pixels = self.validate()
        self.assertEqual((metadata['width'], metadata['height'], metadata['padding']), (640, 64, 12))
        self.assertTrue(metadata['opaque'] and metadata['whiteBorders'] and metadata['nonwhiteInk'])
        self.assertEqual(metadata['outputName'], OUTPUT_NAME)
        self.assertEqual(metadata['sha256'], hashlib.sha256(self.png).hexdigest())
        self.assertEqual(len(pixels), 640*64*4)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_manifest_items_may_follow_the_actual_explorer_return_order(self):
        receipt = copy.deepcopy(self.receipt)
        receipt['items'].reverse()
        self.write_manifest(receipt)
        self.assertEqual(self.validate(receipt)[0]['outputName'], OUTPUT_NAME)

    def test_malformed_terminal_receipts_and_settings_reject_even_matching_manifest(self):
        changes = [
            lambda value: value.update(status='running'),
            lambda value: value.update(manifestError='checkpoint failed'),
            lambda value: value.update(sessionId='bad'),
            lambda value: value.update(manifestName='../outside.json'),
            lambda value: value['settings']['export'].update(width=480),
            lambda value: value['settings']['export'].update(width=640.0),
            lambda value: value['settings']['export'].update(margin=True),
            lambda value: value['settings']['profile'].update(revision=True),
            lambda value: value['settings']['style'].update(color=1),
            lambda value: value['settings']['profile'].update(path='C:\\private\\profile'),
            lambda value: value.update(extraField=True),
            lambda value: value.pop('manifestError'),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                receipt = copy.deepcopy(self.receipt)
                change(receipt)
                self.write_manifest(receipt)
                with self.assertRaises(AssertionError):
                    self.validate(receipt)

    def test_source_item_receipt_identity_failures_are_not_ignored(self):
        changes = [
            lambda value: value['items'][0].update(id='b'*32),
            lambda value: value['items'][0].update(name='other.nivram'),
            lambda value: value['items'][0].update(name=[]),
            lambda value: value['items'][0].update(extension='.mol'),
            lambda value: value['items'][0].update(sourceSha256='0'*64),
            lambda value: value['items'][0].update(status='failed'),
            lambda value: value['items'][0].update(outputName='../outside.png'),
            lambda value: value['items'][0].update(outputName=SENTINEL_NAME),
            lambda value: value['items'][0].update(outputSha256='0'*64),
            lambda value: value['items'][0]['dimensions'].update(height=True),
            lambda value: value['items'][1].update(outputName='invalid.png'),
            lambda value: value['items'][1].update(outputSha256='f'*64),
            lambda value: value['items'][1].update(dimensions={'width': 640}),
            lambda value: value['items'][1]['error'].update(code='export_failed'),
            lambda value: value['items'].append(copy.deepcopy(value['items'][0])),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                receipt = copy.deepcopy(self.receipt)
                change(receipt)
                self.write_manifest(receipt)
                with self.assertRaises(AssertionError):
                    self.validate(receipt)

    def test_manifest_receipt_disagreement_including_bool_number_equivalence_rejects(self):
        manifest = copy.deepcopy(self.write_manifest())
        manifest['settings']['profile']['revision'] = True
        with self.assertRaisesRegex(AssertionError, 'receipt_mismatch'):
            validate_receipt(self.receipt, manifest, self.settings, self.source_bytes, self.fixtures)
        manifest = {'schema': 'nivram-batch-export/1', **copy.deepcopy(self.receipt)}
        manifest['items'][0]['dimensions']['height'] = 64.0
        with self.assertRaisesRegex(AssertionError, 'receipt_mismatch'):
            validate_receipt(self.receipt, manifest, self.settings, self.source_bytes, self.fixtures)

    def test_changed_inputs_or_collision_sentinel_reject_without_writing_other_outputs(self):
        for path in (self.fixtures / VALID_NAME, self.fixtures / INVALID_NAME, self.output / SENTINEL_NAME):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                outputs = (self.output / OUTPUT_NAME).read_bytes(), (self.output / MANIFEST_NAME).read_bytes()
                path.write_bytes(b'Changed outside the batch.')
                with self.assertRaises(AssertionError):
                    self.validate()
                self.assertEqual(outputs, ((self.output / OUTPUT_NAME).read_bytes(), (self.output / MANIFEST_NAME).read_bytes()))
                path.write_bytes(original)

    def test_changed_graph_revision_dirty_recovery_drawing_or_history_rejects(self):
        changes = [
            lambda value: value['snapshot']['document']['graph']['atoms'][0].update(charge=1),
            lambda value: value['snapshot'].update(revision=9),
            lambda value: value['snapshot'].update(dirty=False),
            lambda value: value['snapshot'].update(recoveryGeneration=4),
            lambda value: value['snapshot']['document']['export'].update(width=640),
            lambda value: value.update(drawing='<g id="drawing"/>'),
            lambda value: value.update(undoDisabled=True),
            lambda value: value.update(redoDisabled=False),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                after = copy.deepcopy(self.state)
                change(after)
                with self.assertRaises(AssertionError):
                    self.validate(after=after)

    def test_extra_outputs_or_partial_temporary_siblings_reject(self):
        for name in ('extra.png', '.nivram-batch-extra.tmp', 'native batch invalid \u00fc.png'):
            path = self.output / name
            path.write_bytes(b'extra')
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, 'output_set'):
                self.validate()
            path.unlink()

    def test_transparent_or_nonwhite_border_or_blank_or_wrong_width_pixels_reject(self):
        for kind in ('alpha', 'border', 'blank', 'width'):
            with self.subTest(kind=kind):
                stream = BytesIO()
                with Image.new('RGBA', (639 if kind == 'width' else 640, 64), 'white') as image:
                    if kind != 'blank':
                        image.putpixel((40, 32), (17, 47, 139, 255))
                    if kind == 'alpha':
                        image.putpixel((40, 32), (17, 47, 139, 254))
                    if kind == 'border':
                        image.putpixel((0, 32), (255, 254, 255, 255))
                    image.save(stream, format='PNG')
                data = stream.getvalue()
                receipt = copy.deepcopy(self.receipt)
                receipt['items'][0]['outputSha256'] = hashlib.sha256(data).hexdigest()
                self.write_manifest(receipt)
                (self.output / OUTPUT_NAME).write_bytes(data)
                with self.assertRaises(AssertionError):
                    self.validate(receipt)

    def test_manifest_duplicate_nonfinite_truncated_and_oversized_bytes_reject(self):
        for data in (b'{"status":"failed","status":"completed"}', b'{"x":NaN}', b'{}x', b'\xff', b'[]', b' '*(256*1024+1)):
            with self.subTest(data=data[:30]), self.assertRaises(AssertionError):
                _json_object(data)

    def test_scoped_reads_fresh_writes_and_redirected_children_fail_closed(self):
        with self.assertRaises(AssertionError):
            _ordinary_child(self.fixtures, self.fixtures / '..' / 'outside.png')
        with self.assertRaises(AssertionError):
            _write_fresh(self.fixtures, self.fixtures / VALID_NAME, b'replacement')
        with self.assertRaises(AssertionError):
            _read_owned(self.output, self.output / OUTPUT_NAME, 3)
        original_is_junction = Path.is_junction
        with patch.object(Path, 'is_junction', lambda value: value == self.fixtures / VALID_NAME or original_is_junction(value)):
            with self.assertRaises(AssertionError):
                _read_owned(self.fixtures, self.fixtures / VALID_NAME, 100)

    def test_actual_selection_requires_two_unique_exact_inputs_and_one_exact_folder(self):
        paths = tuple(self.fixtures / name for name in self.source_bytes)
        actual = tuple(str(path) for path in reversed(paths))
        self.assertEqual(assert_native_selection('open-multiple', actual, paths, self.fixtures), [path.name for path in reversed(paths)])
        self.assertEqual(assert_native_selection('folder', (str(self.output),), (self.output,), self.fixtures), [self.output.name])
        invalid = (None, str(paths[0]), (), (str(paths[0]),), (str(paths[0]), str(paths[0])), (paths[0], paths[1]),
                   (str(paths[0]), str(self.output)), (str(paths[0]), str(self.fixtures / '..' / 'outside.mol')))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(AssertionError):
                assert_native_selection('open-multiple', value, paths, self.fixtures)
        with self.assertRaises(AssertionError):
            assert_native_selection('folder', (str(self.fixtures),), (self.output,), self.fixtures)


class NativeDialogObserverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = (self.root / 'first \u00fc.nivram', self.root / 'second \u00fc.mol')
        for path in self.inputs:
            path.write_bytes(b'Owned synthetic input.')
        self.folder = self.root / 'outputs \u00fc'
        self.folder.mkdir()
        self.calls, self.fixture_calls = [], []
        self.finish_count = 0
        self.results = (tuple(str(path) for path in reversed(self.inputs)), (str(self.folder),))

        class Window:
            def create_file_dialog(inner, *args, **kwargs):
                self.calls.append((args, kwargs))
                return self.results[len(self.calls)-1]
        self.window = Window()

    def choose(self, target, **kwargs):
        self.fixture_calls.append((target, kwargs))
        parent = self
        class Choice:
            def finish(inner):
                parent.finish_count += 1
                return {'selected': True, 'reason': None}
        return Choice()

    def observer(self):
        return NativeDialogObserver(self.window, self.choose, root=self.root, inputs=self.inputs,
            folder=self.folder, open_kind=1, folder_kind=3, timeout=lambda: 15)

    def test_real_original_called_with_unchanged_arguments_and_results_preserved(self):
        original = self.window.create_file_dialog
        observer = self.observer()
        observer.attach()
        first = self.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
        second = self.window.create_file_dialog(3)
        observer.assert_complete()
        observer.close()
        self.assertIs(first, self.results[0])
        self.assertIs(second, self.results[1])
        self.assertEqual(self.window.create_file_dialog, original)
        self.assertEqual([call[1]['mode'] for call in self.fixture_calls], ['open-multiple', 'folder'])
        self.assertEqual(self.finish_count, 2)
        self.assertEqual(len(self.calls), 2)

    def test_wrong_kind_or_argument_does_not_invoke_original_or_fixture(self):
        for args, kwargs in (((3,), {}), ((1,), {}), ((1,), {'allow_multiple': False}),
                             ((1,), {'allow_multiple': 1, 'file_types': ('Molecular projects and 2D MOL (*.nivram;*.mol)',)}),
                             ((True,), {'allow_multiple': True, 'file_types': ('Molecular projects and 2D MOL (*.nivram;*.mol)',)}),
                             ((1,), {'allow_multiple': True, 'file_types': ('Other files',)}), ((1, 'extra'), {})):
            with self.subTest(args=args, kwargs=kwargs):
                observer = self.observer()
                observer.attach()
                try:
                    with self.assertRaises(AssertionError):
                        self.window.create_file_dialog(*args, **kwargs)
                finally:
                    observer.close()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fixture_calls, [])

    def test_wrong_actual_native_return_is_rejected_and_original_restored(self):
        self.results = (None, (str(self.folder),))
        original = self.window.create_file_dialog
        observer = self.observer()
        observer.attach()
        try:
            with self.assertRaises(AssertionError):
                self.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
            with self.assertRaises(AssertionError):
                observer.assert_complete()
        finally:
            observer.close()
        self.assertEqual(self.window.create_file_dialog, original)
        self.assertEqual(self.finish_count, 1)

    def test_original_exception_still_finishes_fixture_and_can_restore(self):
        def original(*args, **kwargs):
            raise RuntimeError('Synthetic original failure.')
        self.window.create_file_dialog = original
        observer = self.observer()
        observer.attach()
        try:
            with self.assertRaises(RuntimeError):
                self.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
        finally:
            observer.close()
        self.assertIs(self.window.create_file_dialog, original)
        self.assertEqual(self.finish_count, 1)
        self.assertFalse(observer.records[0]['originalReturned'])

    def test_third_dialog_is_not_allowed_and_observer_does_not_clobber_external_change(self):
        observer = self.observer()
        observer.attach()
        self.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
        self.window.create_file_dialog(3)
        with self.assertRaises(AssertionError):
            self.window.create_file_dialog(3)
        self.assertEqual(len(self.calls), 2)
        replacement = lambda *args, **kwargs: None
        self.window.create_file_dialog = replacement
        with self.assertRaises(AssertionError):
            observer.close()
        self.assertIs(self.window.create_file_dialog, replacement)

    def failed_choice(self, error):
        parent = self
        class Choice:
            def finish(inner):
                parent.finish_count += 1
                raise error
        return lambda *args, **kwargs: Choice()

    def test_finish_failure_records_only_sanitized_receipt_and_restores_original(self):
        error = RuntimeError('Do not disclose C:\\private\\exception text')
        error.dialog_result = {'selected': False, 'reason': 'dialog_close_unconfirmed',
            'stage': 'confirm-close', 'selection_mode': 'folder', 'selection_count': 1,
            'dialog_close_confirmed': False, 'control_count': 3, 'dialog_count': 1,
            'label_counts': {'filename': 1, 'folder': 0, 'other': 0}, 'idok_label_kind': 'folder',
            'parent_phase': 'result_validate', 'worker_returncode': 0, 'worker_exit_confirmed': True,
            'path': 'C:\\private\\receipt', 'caption': 'Private dialog caption'}
        original = self.window.create_file_dialog
        observer = self.observer()
        observer.choose = self.failed_choice(error)
        observer.attach()
        try:
            with self.assertRaises(RuntimeError) as raised:
                self.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
            self.assertIs(raised.exception, error)
        finally:
            observer.close()
        record = observer.records[0]
        self.assertTrue(record['originalReturned'] and record['fixtureFinishFailed'])
        self.assertFalse(record.get('actualReturnVerified', False))
        self.assertEqual(record['fixtureFailure']['reason'], 'dialog_close_unconfirmed')
        self.assertEqual(record['fixtureFailure']['label_counts'], {'filename': 1, 'folder': 0, 'other': 0})
        self.assertNotIn('private', json.dumps(record).casefold())
        self.assertEqual(self.window.create_file_dialog, original)
        self.assertEqual(self.finish_count, 1)

    def test_original_failure_is_preserved_when_finish_also_fails(self):
        original_error = RuntimeError('Original synthetic failure')
        finish_error = ValueError('Different synthetic finish failure')
        finish_error.dialog_result = {'selected': False, 'reason': 'worker_failure'}
        def original(*args, **kwargs):
            raise original_error
        self.window.create_file_dialog = original
        observer = self.observer()
        observer.choose = self.failed_choice(finish_error)
        observer.attach()
        try:
            with self.assertRaises(RuntimeError) as raised:
                self.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
            self.assertIs(raised.exception, original_error)
        finally:
            observer.close()
        self.assertIs(self.window.create_file_dialog, original)
        self.assertTrue(observer.records[0]['originalCallFailed'])
        self.assertFalse(observer.records[0]['originalReturned'])
        self.assertEqual(observer.records[0]['fixtureFailure']['reason'], 'worker_failure')
        self.assertEqual(self.finish_count, 1)

    def test_bad_exception_receipt_property_cannot_replace_the_finish_failure(self):
        class Failure(RuntimeError):
            @property
            def dialog_result(inner):
                raise LookupError('Untrusted property error')
        error = Failure('Original fixture finish failure')
        observer = self.observer()
        observer.choose = self.failed_choice(error)
        observer.attach()
        try:
            with self.assertRaises(Failure) as raised:
                self.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
            self.assertIs(raised.exception, error)
        finally:
            observer.close()
        self.assertFalse(observer.records[0]['fixtureFailure']['available'])

    def test_failed_case_retains_deepcopy_before_wrapper_cleanup(self):
        error = RuntimeError('Synthetic finish failure')
        error.dialog_result = {'selected': False, 'reason': 'supported_folder_control_not_found'}
        observer = self.observer()
        observer.choose = self.failed_choice(error)
        original = self.window.create_file_dialog
        observer.attach()
        report = {}
        try:
            with self.assertRaises(RuntimeError):
                self.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
        finally:
            retain_dialog_records(report, observer, failure_active=True)
        observer.records[0]['fixtureFailure']['reason'] = 'worker_failure'
        self.assertEqual(report['batch_png_dialogs'][0]['fixtureFailure']['reason'], 'supported_folder_control_not_found')
        self.assertTrue(report['batch_png_dialog_observer_cleanup']['restored'])
        self.assertEqual(self.window.create_file_dialog, original)
        observer.records.extend([{}, {}, {}])
        self.assertEqual(len(observer.snapshot()), 2)

    def test_cleanup_error_cannot_mask_active_failure_or_clobber_changed_wrapper(self):
        observer = self.observer()
        observer.attach()
        replacement = lambda *args, **kwargs: None
        self.window.create_file_dialog = replacement
        report = {}
        retain_dialog_records(report, observer, failure_active=True)
        self.assertEqual(report['batch_png_dialogs'], [])
        self.assertEqual(report['batch_png_dialog_observer_cleanup'], {'restored': False})
        self.assertIs(self.window.create_file_dialog, replacement)
        with self.assertRaisesRegex(AssertionError, 'observer_changed'):
            retain_dialog_records({}, observer, failure_active=False)


class BatchDiagnosticTests(unittest.TestCase):
    def test_known_bounded_dialog_facts_survive_without_paths_text_or_unknown_keys(self):
        value = {'selected': False, 'reason': 'supported_folder_control_not_found', 'stage': 'identify-field',
            'selection_mode': 'folder', 'selection_count': 1, 'filename_field_verified': False,
            'dialog_close_confirmed': False, 'control_count': 9, 'dialog_count': 1,
            'label_counts': {'filename': 1, 'folder': 0, 'other': 2}, 'idok_label_kind': 'folder',
            'parent_phase': 'result_validate', 'error_kind': None, 'worker_returncode': 0,
            'worker_exit_confirmed': True, 'elapsed_seconds': 14.006,
            'root': 'C:\\private\\path', 'caption': 'Private title', 'nonce': 'a'*32,
            'cleanup': {'shutdownConfirmed': True, 'worker_exit_confirmed': True,
                'supervisor_thread_exited': True, 'cancel_requested': True,
                'owned_dialog_cancel_posted': False, 'cleanup_error_kind': 'os_error',
                'scope': 'Private arbitrary scope', 'exception': 'Private exception'}}
        result = dialog_diagnostic(value)
        self.assertTrue(result['available'])
        self.assertEqual(result['reason'], value['reason'])
        self.assertEqual(result['stage'], value['stage'])
        self.assertEqual(result['label_counts'], value['label_counts'])
        self.assertEqual(result['idok_label_kind'], 'folder')
        self.assertEqual(result['elapsed_seconds'], 14.006)
        self.assertTrue(result['cleanup']['shutdownConfirmed'])
        self.assertEqual(result['cleanup']['cleanup_error_kind'], 'os_error')
        serialized = json.dumps(result, allow_nan=False)
        self.assertNotIn('private', serialized.casefold())
        self.assertNotIn('nonce', serialized)
        self.assertNotIn('scope', serialized)

    def test_unknown_categories_and_boolean_number_confusion_are_redacted(self):
        secret = 'C:\\private\\do-not-disclose'
        result = dialog_diagnostic({'selected': 1, 'filename_field_verified': 'true',
            'dialog_close_confirmed': 1, 'worker_exit_confirmed': 0, 'reason': secret,
            'stage': secret, 'selection_mode': secret, 'parent_phase': secret,
            'error_kind': secret, 'cleanup_error_kind': secret, 'idok_label_kind': secret,
            'selection_count': True, 'control_count': True, 'dialog_count': 257,
            'worker_returncode': False, 'elapsed_seconds': True,
            'cleanup': {'cancel_requested': 1, 'scope': secret, 'cleanup_error_kind': secret}})
        for key in ('reason', 'stage', 'selection_mode', 'parent_phase', 'error_kind', 'cleanup_error_kind', 'idok_label_kind'):
            self.assertEqual(result[key], 'unrecognized')
        for key in ('selected', 'filename_field_verified', 'dialog_close_confirmed', 'worker_exit_confirmed',
                    'selection_count', 'control_count', 'dialog_count', 'worker_returncode', 'elapsed_seconds'):
            self.assertIsNone(result[key])
        self.assertIsNone(result['cleanup']['cancel_requested'])
        self.assertNotIn(secret, json.dumps(result))

    def test_nonfinite_oversized_or_wrong_type_scalar_values_never_enter_report(self):
        for number in (float('nan'), float('inf'), float('-inf'), -1, 61, 2**10000, '15', True):
            with self.subTest(kind=type(number).__name__):
                result = dialog_diagnostic({'elapsed_seconds': number})
                self.assertIsNone(result['elapsed_seconds'])
                json.dumps(result, allow_nan=False)
        for number in (-1, 257, 1.0, True, [], {}):
            with self.subTest(number=number):
                result = dialog_diagnostic({'control_count': number, 'dialog_count': number})
                self.assertIsNone(result['control_count'])
                self.assertIsNone(result['dialog_count'])
        for number in (-(2**31)-1, 2**32, 1.0, True, '0'):
            self.assertIsNone(dialog_diagnostic({'worker_returncode': number})['worker_returncode'])

    def test_invalid_label_counts_are_wholly_redacted_including_sum_and_extra_keys(self):
        for labels in ({'filename': 1, 'folder': 1, 'other': 1},
            {'filename': True, 'folder': 0, 'other': 0},
            {'filename': 257, 'folder': 0, 'other': 0},
            {'filename': -1, 'folder': 0, 'other': 0},
            {'filename': 0, 'folder': 0, 'other': 0, 'caption': 'Private caption'},
            {'filename': 0, 'other': 0}, None, []):
            result = dialog_diagnostic({'control_count': 2, 'label_counts': labels})
            self.assertEqual(result['label_counts'], {'filename': None, 'folder': None, 'other': None})

    def test_unavailable_or_subclass_receipts_do_not_run_custom_mapping_methods(self):
        class Hostile(dict):
            def get(inner, *args):
                raise AssertionError('Must never call this mapping method')
        for value in (None, [], 'Private receipt', Hostile()):
            result = dialog_diagnostic(value)
            self.assertFalse(result['available'])
            self.assertIsNone(result['reason'])
            json.dumps(result, allow_nan=False)

    def test_ui_diagnostic_accepts_only_status_enums_and_bounded_counts_disabled_flags(self):
        for status in ('complete', 'selection-cancelled', 'stopped', 'choosing', 'processing', 'unavailable'):
            result = ui_diagnostic({'status': status, 'startDisabled': False, 'profileDisabled': False,
                'cancelDisabled': True, 'refreshDisabled': False, 'meterMax': 2, 'meterValue': 0,
                'rowCount': 0, 'stageCount': 0, 'rawStatus': 'Private error C:\\private',
                'rows': ['Private filename'], 'caption': 'Private dialog title'})
            self.assertEqual(result['status'], status)
            self.assertEqual(result['meterMax'], 2)
            self.assertIs(result['startDisabled'], False)
            self.assertNotIn('private', json.dumps(result).casefold())
        result = ui_diagnostic({'status': 'Private status', 'meterMax': True, 'meterValue': 65,
            'rowCount': -1, 'stageCount': 2.0, 'startDisabled': 0, 'cancelDisabled': 'true'})
        self.assertEqual(result['status'], 'unrecognized')
        for key in ('meterMax', 'meterValue', 'rowCount', 'stageCount', 'startDisabled', 'cancelDisabled'):
            self.assertIsNone(result[key])

    def test_wait_diagnostic_capture_preserves_original_failure_if_evaluator_also_fails(self):
        original_error = TimeoutError('Original wait failure')
        def unavailable(expression, label):
            self.assertEqual(label, 'batch-completion-wait-failure-ui')
            raise ValueError('Private evaluator error')
        report = {}
        try:
            raise original_error
        except Exception:
            capture_wait_failure(report, unavailable)
            import sys
            self.assertIs(sys.exc_info()[1], original_error)
        self.assertEqual(report['batch_png_wait_failure_ui']['status'], 'unavailable')
        self.assertFalse(report['batch_png_wait_failure_ui']['available'])
        self.assertNotIn('private', json.dumps(report).casefold())

    def test_wait_diagnostic_capture_redacts_unexpected_successful_evaluator_payload(self):
        report = {}
        capture_wait_failure(report, lambda expression, label: {'status': 'stopped', 'meterValue': 0,
            'caption': 'Private caption', 'rawStatus': 'Private status', 'path': 'C:\\private\\input'})
        self.assertEqual(report['batch_png_wait_failure_ui']['status'], 'stopped')
        self.assertEqual(report['batch_png_wait_failure_ui']['meterValue'], 0)
        self.assertNotIn('private', json.dumps(report).casefold())


class BatchUiTests(unittest.TestCase):
    def value(self):
        return {'status': f'1 of 2 PNG files saved. Manifest: {MANIFEST_NAME}. Completed files were retained.',
            'startDisabled': False, 'profileDisabled': False, 'cancelDisabled': True, 'refreshDisabled': False,
            'meterMax': 2, 'meterValue': 2, 'rows': [f'{VALID_NAME}: exported \u2192 {OUTPUT_NAME}',
                f'{INVALID_NAME}: failed \u2014 {INVALID_MESSAGE}'], 'stageCount': 0,
            'progress': {'sawChoosing': True, 'sawBusy': True, 'sawProcessing': True, 'events': 8}}

    def test_actual_progress_and_terminal_rows_are_required(self):
        value = self.value()
        self.assertEqual(validate_ui(value), value['progress'])
        value['rows'].reverse()
        self.assertEqual(validate_ui(value), value['progress'])

    def test_malformed_progress_controls_success_text_or_rows_reject(self):
        changes = [lambda value: value.update(status='2 of 2 PNG files saved.'),
            lambda value: value.update(startDisabled=True), lambda value: value.update(cancelDisabled=False),
            lambda value: value.update(refreshDisabled=None), lambda value: value.update(meterValue=1),
            lambda value: value.update(stageCount=1), lambda value: value.update(rows=[value['rows'][0]]*2),
            lambda value: value['progress'].update(sawChoosing=False),
            lambda value: value['progress'].update(sawBusy=False),
            lambda value: value['progress'].update(sawProcessing=False),
            lambda value: value['progress'].update(events=True), lambda value: value.update(progress=None)]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                value = self.value()
                change(value)
                with self.assertRaises(AssertionError):
                    validate_ui(value)


if __name__ == '__main__':
    unittest.main()
