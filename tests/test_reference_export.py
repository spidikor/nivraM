"""Reference native-save contracts without native UI, DLLs, processes or network."""
import base64
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

from nivram import conditional_save
from nivram.desktop import DesktopApi
from nivram.reference_export import MAX_BYTES, ReferenceExportSaver, validate_payload


def reference_document():
    return {'schemaVersion': 1, 'exportKind': 'ph-reference-context',
            'selectedSample': {'index': 74, 'ph': 7.4},
            'reference': {'schemaVersion': 1, 'datasetVersion': 'synthetic-test-1',
                'purpose': 'Reference only', 'reviewedDate': '2026-09-21',
                'interpretationRules': ['No population prediction.'], 'context': None,
                'comparison': None, 'sources': [], 'overlays': [], 'gap': None},
            'populationResults': None,
            'limitation': 'Reference contexts and unverified comparisons only; not molecular population results.'}


def csv_payload(document):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(['path', 'type', 'value', 'spreadsheet_escaped'])
    def visit(value, path):
        if isinstance(value, (dict, list)):
            if not value:
                writer.writerow([path, 'object' if isinstance(value, dict) else 'array',
                                 '{}' if isinstance(value, dict) else '[]', 'false'])
            else:
                for key, item in (value.items() if isinstance(value, dict) else enumerate(value)):
                    visit(item, path + '/' + str(key).replace('~', '~0').replace('/', '~1'))
        else:
            kind = ('null' if value is None else 'boolean' if type(value) is bool
                    else 'number' if isinstance(value, (int, float)) else 'string')
            raw = ('null' if value is None else str(value).lower() if type(value) is bool else str(value))
            guarded = kind == 'string' and (raw.lstrip('\ufeff \t\r\n').startswith(('=', '+', '@', '-'))
                                             or raw.startswith(('\t', '\r', '\n')))
            writer.writerow([path, kind, "'" + raw if guarded else raw, str(guarded).lower()])
    visit(document, '')
    return stream.getvalue()


def png_payload(width=1000, height=560):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * (width * 4)) * height)) + chunk(b'IEND', b''))
    return base64.b64encode(data).decode('ascii'), data


class ReferencePayloadTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('Network forbidden')).start()
        patch('subprocess.Popen', side_effect=AssertionError('Processes forbidden')).start()
        patch('ctypes.WinDLL', side_effect=AssertionError('Native DLLs forbidden'), create=True).start()

    def test_json_and_csv_preserve_the_exact_reviewed_utf8_bytes(self):
        document = reference_document()
        for kind, payload in [('json', json.dumps(document, indent=2) + '\n'), ('csv', csv_payload(document))]:
            with self.subTest(kind=kind):
                self.assertEqual(validate_payload(kind, payload), payload.encode('utf-8'))

    def test_png_requires_complete_valid_fixed_size_png(self):
        payload, data = png_payload()
        self.assertEqual(validate_payload('png', payload), data)
        for invalid in (png_payload(999, 560)[0], png_payload(1000, 559)[0], payload[:-4],
                        'data:image/png;base64,' + payload, 'not base64', base64.b64encode(data + b'x').decode()):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                validate_payload('png', invalid)

    def test_json_rejects_other_documents_duplicate_keys_and_nonfinite_values(self):
        valid = json.dumps(reference_document())
        for payload in ('{}', '[]', valid.replace('"ph-reference-context"', '"molecular-results"'),
                        valid.replace('"populationResults": null', '"populationResults": []'),
                        valid.replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1', 1),
                        valid.replace('7.4', 'NaN'), valid.replace('"index": 74', '"index": true'),
                        valid.replace('7.4', '7.5')):
            with self.subTest(payload=payload[:35]), self.assertRaises(ValueError):
                validate_payload('json', payload)

    def test_nested_bounds_and_no_payload_mutation(self):
        document = reference_document()
        original = copy.deepcopy(document)
        validate_payload('json', json.dumps(document))
        self.assertEqual(document, original)
        document['reference']['purpose'] = 'x' * 16385
        with self.assertRaises(ValueError):
            validate_payload('json', json.dumps(document))

    def test_csv_rejects_duplicates_missing_envelope_columns_types_and_formula_injection(self):
        valid = csv_payload(reference_document())
        lines = valid.splitlines(keepends=True)
        for invalid in (valid + lines[1], ''.join(lines[:1] + lines[2:]), valid.replace('"path"', '"filename"', 1),
                        valid.replace('"ph-reference-context"', '"other"'),
                        valid.replace('"number","74"', '"number","NaN"'),
                        valid.replace('"Reference only","false"', '"=1+1","false"'),
                        valid.replace('"Reference only","false"', '"Reference only","true"')):
            with self.subTest(payload=invalid[:80]), self.assertRaises(ValueError):
                validate_payload('csv', invalid)

    def test_csv_guarded_text_is_accepted_without_removing_its_apostrophe(self):
        document = reference_document()
        document['reference']['purpose'] = '=synthetic'
        payload = csv_payload(document)
        self.assertIn("'=synthetic", payload)
        self.assertEqual(validate_payload('csv', payload), payload.encode())

    def test_unknown_kinds_types_oversized_and_invalid_unicode_are_rejected(self):
        for kind, value in [('html', '<h1>x</h1>'), ('json', {}), ('json', '\ud800'),
                            ('csv', 'x' * (MAX_BYTES + 1)), ('png', 'A' * (4 * ((MAX_BYTES + 2)//3) + 1))]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_payload(kind, value)


class ReferenceSaveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / 'reference.json'
        self.payload = json.dumps(reference_document(), indent=2) + '\n'
        self.data = self.payload.encode()
        self.window = SimpleNamespace(create_file_dialog=Mock(return_value=str(self.destination)),
                                      create_confirmation_dialog=Mock(return_value=True))
        self.saver = ReferenceExportSaver()
        self.addCleanup(self.saver.close)
        self.token = 'synthetic-session-token'
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('Network forbidden')).start()
        patch('subprocess.Popen', side_effect=AssertionError('Processes forbidden')).start()
        patch('ctypes.WinDLL', side_effect=AssertionError('Native DLLs forbidden'), create=True).start()

    def save(self, kind='json', payload=None, token=None):
        return self.saver.save(self.window, kind, self.payload if payload is None else payload,
                               self.token if token is None else token, self.token)

    def test_authentication_precedes_decoder_dialog_and_write(self):
        for token in ('wrong', '', None, [], 'x' * 1000):
            with self.subTest(token=type(token).__name__), patch('nivram.reference_export.validate_payload') as decoder:
                result = self.saver.save(self.window, 'json', self.payload, token, self.token)
                self.assertEqual(result['status'], 'failed')
                decoder.assert_not_called()
        self.window.create_file_dialog.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_invalid_payload_never_opens_the_chooser(self):
        self.assertEqual(self.save(payload='{}')['status'], 'failed')
        self.window.create_file_dialog.assert_not_called()

    def test_new_file_requires_exact_readback_before_saved_receipt(self):
        self.assertEqual(self.save(), {'status': 'saved', 'bytes': len(self.data),
                                      'sha256': hashlib.sha256(self.data).hexdigest()})
        self.assertEqual(self.destination.read_bytes(), self.data)
        self.assertEqual(list(self.root.iterdir()), [self.destination])

    def test_csv_and_png_save_verified_original_bytes(self):
        png, image = png_payload()
        for kind, payload, expected in [('csv', csv_payload(reference_document()), None), ('png', png, image)]:
            with self.subTest(kind=kind):
                destination = self.root / ('reference.' + kind)
                self.window.create_file_dialog.return_value = str(destination)
                expected = payload.encode() if expected is None else expected
                self.assertEqual(self.save(kind, payload), {'status': 'saved', 'bytes': len(expected),
                    'sha256': hashlib.sha256(expected).hexdigest()})
                self.assertEqual(destination.read_bytes(), expected)

    def test_cancelled_chooser_never_writes(self):
        self.window.create_file_dialog.return_value = None
        self.assertEqual(self.save(), {'status': 'cancelled'})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_final_appended_extension_is_the_confirmed_and_saved_destination(self):
        selected = self.root / 'choice.txt'
        final = self.root / 'choice.txt.json'
        final.write_bytes(b'OLD')
        self.window.create_file_dialog.return_value = str(selected)
        self.window.create_confirmation_dialog.return_value = False
        self.assertEqual(self.save(), {'status': 'cancelled'})
        self.assertIn(final.name, self.window.create_confirmation_dialog.call_args.args[1])
        self.assertEqual(final.read_bytes(), b'OLD')
        self.assertFalse(selected.exists())

    def test_existing_save_passes_preconfirmation_hash_and_preserves_unique_verified_backup(self):
        self.destination.write_bytes(b'OLD')
        unrelated = self.root / 'reference.json.bak'
        unrelated.write_bytes(b'UNRELATED')
        def transaction(path, data, expected, backup):
            self.assertEqual(Path(path), self.destination)
            self.assertEqual(expected, hashlib.sha256(b'OLD').hexdigest())
            self.window.create_confirmation_dialog.assert_called_once()
            backup(b'OLD')
            backups = [p for p in self.root.iterdir() if p not in (self.destination, unrelated)]
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b'OLD')
            self.destination.write_bytes(data)
            return hashlib.sha256(data).hexdigest()
        with patch('nivram.reference_export.conditional_write_existing', side_effect=transaction) as writer:
            self.assertEqual(self.save()['status'], 'saved')
            writer.assert_called_once()
        self.assertEqual(unrelated.read_bytes(), b'UNRELATED')

    def test_change_during_confirmation_is_conflict_without_backup_or_overwrite(self):
        self.destination.write_bytes(b'OLD')
        def confirm(*_args):
            self.destination.write_bytes(b'FOREIGN')
            return True
        self.window.create_confirmation_dialog.side_effect = confirm
        def transaction(path, data, expected, backup):
            self.assertEqual(expected, hashlib.sha256(b'OLD').hexdigest())
            self.assertEqual(Path(path).read_bytes(), b'FOREIGN')
            raise conditional_save.SaveConflict('private path must not leak')
        with patch('nivram.reference_export.conditional_write_existing', side_effect=transaction):
            result = self.save()
        self.assertEqual(result['status'], 'conflict')
        self.assertNotIn('private', result['message'])
        self.assertEqual(self.destination.read_bytes(), b'FOREIGN')
        self.assertEqual(list(self.root.iterdir()), [self.destination])

    def test_new_file_race_uses_exclusive_publication_and_preserves_foreign(self):
        def race(path, data, *, exclusive=False):
            self.assertTrue(exclusive)
            Path(path).write_bytes(b'FOREIGN')
            raise FileExistsError('private path')
        with patch('nivram.reference_export.atomic_write', side_effect=race):
            self.assertEqual(self.save()['status'], 'conflict')
        self.assertEqual(self.destination.read_bytes(), b'FOREIGN')

    def test_postpublication_failure_and_fresh_readback_mismatch_never_claim_saved(self):
        def publish_then_fail(path, data, **_kwargs):
            Path(path).write_bytes(data)
            raise OSError('SECRET arbitrary exception')
        with patch('nivram.reference_export.atomic_write', side_effect=publish_then_fail):
            result = self.save()
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertNotIn('SECRET', result['message'])
        self.destination.unlink()
        def publish_foreign(path, data, **_kwargs):
            Path(path).write_bytes(b'FOREIGN')
        with patch('nivram.reference_export.atomic_write', side_effect=publish_foreign):
            result = self.save()
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertNotIn('sha256', result)

    def test_conditional_failures_preserve_uncertain_cleanup_classification(self):
        self.destination.write_bytes(b'OLD')
        cases = [(conditional_save.SaveUnsupported('SECRET'), 'unsupported'),
                 (conditional_save.SaveError('SECRET'), 'failed'),
                 (conditional_save.SaveConflict('SECRET'), 'conflict'),
                 (conditional_save.SaveOutcomeUnknown('SECRET'), 'unconfirmed'),
                 (conditional_save.SaveError('SECRET', operation='CloseHandle'), 'unconfirmed'),
                 (conditional_save.SaveError('SECRET', cleanup_errors=[OSError('SECRET')]), 'unconfirmed'),
                 (conditional_save.SaveError('SECRET', outcome='committed'), 'unconfirmed')]
        for error, status in cases:
            with self.subTest(status=status), patch('nivram.reference_export.conditional_write_existing', side_effect=error):
                result = self.save()
                self.assertEqual(result['status'], status)
                self.assertNotIn('SECRET', result['message'])
                self.assertNotIn('sha256', result)

    def test_backup_readback_failure_aborts_before_the_mock_transaction_writes(self):
        self.destination.write_bytes(b'OLD')
        def transaction(path, data, expected, backup):
            try:
                backup(b'OLD')
            except OSError as error:
                raise conditional_save.SaveError('backup failed') from error
            self.fail('Invalid backup allowed the target write')
        with patch('nivram.reference_export.conditional_write_existing', side_effect=transaction), \
             patch('nivram.reference_export._read_bounded', side_effect=[b'OLD', b'WRONG']):
            self.assertEqual(self.save()['status'], 'failed')
        self.assertEqual(self.destination.read_bytes(), b'OLD')

    def test_closed_owner_rejects_future_work_without_dialog(self):
        self.saver.close()
        self.assertEqual(self.save()['status'], 'failed')
        self.window.create_file_dialog.assert_not_called()

    def test_close_during_dialog_prevents_publication_and_reports_pending_owner_honestly(self):
        def dialog(*_args, **_kwargs):
            with patch.object(self.saver._done, 'wait', return_value=False), self.assertRaises(RuntimeError):
                self.saver.close()
            return str(self.destination)
        self.window.create_file_dialog.side_effect = dialog
        self.assertEqual(self.save(), {'status': 'cancelled'})
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.saver.close(), {'closed': True, 'active': 0})

    def test_reentrant_export_is_rejected_without_a_second_dialog(self):
        def dialog(*_args, **_kwargs):
            self.assertEqual(self.save()['status'], 'failed')
            return str(self.destination)
        self.window.create_file_dialog.side_effect = dialog
        self.assertEqual(self.save()['status'], 'saved')
        self.window.create_file_dialog.assert_called_once()

    def test_close_after_transaction_started_never_falsely_reports_cancellation(self):
        self.destination.write_bytes(b'OLD')
        def transaction(path, data, expected, backup):
            backup(b'OLD')
            with patch.object(self.saver._done, 'wait', return_value=False), self.assertRaises(RuntimeError):
                self.saver.close()
            Path(path).write_bytes(data)
            return hashlib.sha256(data).hexdigest()
        with patch('nivram.reference_export.conditional_write_existing', side_effect=transaction):
            self.assertEqual(self.save()['status'], 'saved')
        self.assertEqual(self.saver.close(), {'closed': True, 'active': 0})

    def test_oversized_existing_destination_is_not_confirmed_or_modified(self):
        old = b'O' * (MAX_BYTES + 1)
        self.destination.write_bytes(old)
        self.assertEqual(self.save()['status'], 'unsupported')
        self.window.create_confirmation_dialog.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), old)

    def test_unsafe_device_path_is_rejected_before_any_read_or_native_writer(self):
        self.window.create_file_dialog.return_value = r'\\.\pipe\synthetic-reference'
        with patch('nivram.reference_export._read_bounded') as reader:
            self.assertEqual(self.save()['status'], 'unsupported')
            reader.assert_not_called()

    def test_desktop_method_checks_its_owned_server_token_and_never_changes_project_path(self):
        api = DesktopApi(self.root / 'profile')
        self.addCleanup(api._images.close)
        self.addCleanup(api._batch.close)
        self.addCleanup(api._reference_exports.close)
        api._window = self.window
        api._server = SimpleNamespace(api_token=self.token)
        api._path = self.root / 'unchanged.nivram'
        api._pending_open = ('pending', self.root / 'open.nivram')
        self.assertEqual(api.save_reference_export('json', self.payload, self.token)['status'], 'saved')
        self.assertEqual(api._path.name, 'unchanged.nivram')
        self.assertEqual(api._pending_open[0], 'pending')


if __name__ == '__main__':
    unittest.main()
