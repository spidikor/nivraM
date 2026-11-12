"""Population export arithmetic/transport and owned native-save regression checks."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import conditional_save
from nivram.desktop import DesktopApi, _smoke_failures
from nivram.population_export import LIMITATION, MAX_BYTES, PopulationExportSaver, validate_payload
from nivram.reference_export import validate_payload as validate_reference_payload
from test_reference_export import csv_payload, png_payload, reference_document


def population_document(which='complete'):
    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'population-analytical.json').read_text())
    envelope = fixture[which]['envelope']
    return {'schemaVersion': 1, 'exportKind': 'supplied-population-results', 'limitation': LIMITATION,
            'source': {'name': '=synthetic-model.json', 'modelInputHash': 'b' * 64},
            'selectedSample': {'index': 70, 'ph': 7},
            'visibleStateIds': [envelope['model']['states'][0]['state_id']],
            'referenceContext': reference_document()['reference'], 'populationResults': envelope}


class PopulationPayloadTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('Network forbidden')).start()
        patch('subprocess.Popen', side_effect=AssertionError('Processes forbidden')).start()
        patch('ctypes.WinDLL', side_effect=AssertionError('Native DLLs forbidden'), create=True).start()

    def test_complete_incomplete_and_domain_gaps_preserve_exact_json_and_csv(self):
        for which in ('complete', 'incomplete', 'narrow'):
            original = population_document(which)
            before = copy.deepcopy(original)
            for kind, payload in (('json', json.dumps(original, indent=2) + '\n'), ('csv', csv_payload(original))):
                with self.subTest(which=which, kind=kind):
                    self.assertEqual(validate_payload(kind, payload), payload.encode())
            self.assertEqual(original, before)

    def test_fresh_frontend_number_spellings_do_not_invent_a_different_signature(self):
        document = population_document()
        payload = json.dumps(document)
        payload = payload.replace('7.0', '7').replace('0.0,', '0,')
        self.assertEqual(validate_payload('json', payload), payload.encode())

    def test_hidden_curves_retain_all_samples_and_original_fractions(self):
        document = population_document()
        document['visibleStateIds'] = []
        document['referenceContext'] = None
        payload = json.dumps(document)
        self.assertEqual(validate_payload('json', payload), payload.encode())
        self.assertEqual(document['populationResults']['samples'][70]['result']['fractions'], [.5, .5])

    def test_wrong_identity_conditions_arithmetic_mass_and_shape_are_rejected(self):
        changes = [
            lambda d: d.update(limitation='prediction'),
            lambda d: d['source'].update(modelInputHash='wrong'),
            lambda d: d['source'].update(name='x' * 257),
            lambda d: d['selectedSample'].update(index=True),
            lambda d: d['selectedSample'].update(ph=7.1),
            lambda d: d.update(visibleStateIds=['missing']),
            lambda d: d.update(visibleStateIds=d['visibleStateIds'] * 2),
            lambda d: d['populationResults'].update(documentRevision=True),
            lambda d: d['populationResults']['samples'].pop(),
            lambda d: d['populationResults']['model']['states'][0].pop('degeneracy'),
            lambda d: d['populationResults']['samples'][70]['result'].update(model_signature='c' * 64),
            lambda d: d['populationResults']['samples'][70]['result'].update(input_identity='wrong'),
            lambda d: d['populationResults']['samples'][70]['result']['conditions'].update(temperature_kelvin=299),
            lambda d: d['populationResults']['samples'][70]['result']['provenance'].update(source='wrong'),
            lambda d: d['populationResults']['samples'][70]['result'].update(state_ids=['x', 'y']),
            lambda d: d['populationResults']['samples'][70]['result'].update(fractions=[.4, .6]),
            lambda d: d['populationResults']['samples'][70]['result'].update(percentages=[40, 60]),
            lambda d: d['populationResults']['samples'][70]['result'].update(log_fractions=[0, 0]),
            lambda d: d['populationResults']['samples'][70]['result'].update(omitted_fraction=.2),
            lambda d: d['populationResults']['samples'][70]['result'].update(warnings=[]),
            lambda d: d['populationResults']['samples'][70].update(status='outside_model_domain', result=None, reason='incorrect gap'),
        ]
        for index, change in enumerate(changes):
            document = population_document()
            change(document)
            with self.subTest(change=index), self.assertRaises(ValueError):
                validate_payload('json', json.dumps(document))

    def test_unavailable_samples_retain_explicit_gaps(self):
        document = population_document()
        document['populationResults']['samples'][70].update(status='unavailable', result=None, reason='Provider sample missing.')
        validate_payload('json', json.dumps(document))

    def test_png_is_decodable_fixed_size_and_preserved_exactly(self):
        payload, data = png_payload(1000, 720)
        self.assertEqual(validate_payload('png', payload), data)
        for invalid in (png_payload(1000, 560)[0], png_payload(999, 720)[0], payload[:-4], 'data:image/png;base64,' + payload):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                validate_payload('png', invalid)

    def test_json_and_csv_reject_ambiguous_or_unsafe_transports(self):
        document = population_document()
        valid_json, valid_csv = json.dumps(document), csv_payload(document)
        for kind, invalid in (
            ('json', valid_json.replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1', 1)),
            ('json', valid_json.replace('298.15', 'NaN', 1)),
            ('json', '\ud800'), ('json', '{"nested":' * 30 + '0' + '}' * 30),
            ('csv', valid_csv + valid_csv.splitlines(keepends=True)[1]),
            ('csv', valid_csv.replace("'=synthetic-model.json\",\"true\"", "=synthetic-model.json\",\"false\"")),
            ('csv', valid_csv.replace('"path"', '"wrong"', 1)),
            ('csv', valid_csv.replace('/source/name', '/source/~2name', 1)),
            ('json', 'x' * (MAX_BYTES + 1)), ('html', '<h1>x</h1>')):
            with self.subTest(kind=kind, prefix=invalid[:50]), self.assertRaises(ValueError):
                validate_payload(kind, invalid)

    def test_reference_boundary_is_not_widened_and_csv_arrays_allow_141_rows(self):
        population = population_document()
        for kind, payload in (('json', json.dumps(population)), ('csv', csv_payload(population)), ('png', png_payload(1000, 720)[0])):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_reference_payload(kind, payload)
        reference = reference_document()
        reference['reference']['sources'] = [{}] * 101
        with self.assertRaises(ValueError):
            validate_reference_payload('json', json.dumps(reference))


class PopulationSaveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / 'populations.json'
        self.payload = json.dumps(population_document(), indent=2) + '\n'
        self.data = self.payload.encode()
        self.token = 'synthetic-session'
        self.window = SimpleNamespace(create_file_dialog=Mock(return_value=str(self.destination)),
                                      create_confirmation_dialog=Mock(return_value=True))
        self.saver = PopulationExportSaver()
        self.addCleanup(self.saver.close)

    def save(self, kind='json', payload=None, token=None):
        return self.saver.save(self.window, kind, self.payload if payload is None else payload,
                               self.token if token is None else token, self.token)

    def test_authentication_precedes_validation_and_dialog(self):
        with patch('nivram.population_export.validate_payload') as validator:
            self.assertEqual(self.save(token='wrong')['status'], 'failed')
            validator.assert_not_called()
        self.window.create_file_dialog.assert_not_called()

    def test_json_csv_png_save_original_bytes_and_exact_receipt(self):
        png, data = png_payload(1000, 720)
        for kind, payload, expected in (('json', self.payload, self.data),
                                       ('csv', csv_payload(population_document()), None), ('png', png, data)):
            destination = self.root / ('populations.' + kind)
            self.window.create_file_dialog.return_value = str(destination)
            expected = payload.encode() if expected is None else expected
            with self.subTest(kind=kind):
                self.assertEqual(self.save(kind, payload), {'status': 'saved', 'bytes': len(expected),
                                                          'sha256': hashlib.sha256(expected).hexdigest()})
                self.assertEqual(destination.read_bytes(), expected)
        self.assertEqual(self.window.create_file_dialog.call_args.kwargs['save_filename'], 'nivram-supplied-populations.png')

    def test_existing_destination_uses_verified_unique_backup(self):
        self.destination.write_bytes(b'OLD')
        unrelated = self.root / 'populations.json.bak'
        unrelated.write_bytes(b'UNRELATED')
        def transaction(path, data, expected, backup):
            self.assertEqual(expected, hashlib.sha256(b'OLD').hexdigest())
            backup(b'OLD')
            backups = list(self.root.glob('.nv-pop-*.bak'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b'OLD')
            Path(path).write_bytes(data)
            return hashlib.sha256(data).hexdigest()
        with patch('nivram.reference_export.conditional_write_existing', side_effect=transaction):
            self.assertEqual(self.save()['status'], 'saved')
        self.assertEqual(unrelated.read_bytes(), b'UNRELATED')

    def test_conflict_and_uncertain_cleanup_never_report_saved(self):
        self.destination.write_bytes(b'OLD')
        for error, status in ((conditional_save.SaveConflict('private'), 'conflict'),
                              (conditional_save.SaveOutcomeUnknown('private'), 'unconfirmed'),
                              (conditional_save.SaveError('private', operation='CloseHandle'), 'unconfirmed')):
            with self.subTest(status=status), patch('nivram.reference_export.conditional_write_existing', side_effect=error):
                result = self.save()
                self.assertEqual(result['status'], status)
                self.assertNotIn('private', result['message'])
                self.assertNotIn('sha256', result)
        self.assertEqual(self.destination.read_bytes(), b'OLD')

    def test_cancel_close_busy_and_invalid_data_do_not_publish(self):
        self.assertEqual(self.save(payload='{}')['status'], 'failed')
        self.window.create_file_dialog.assert_not_called()
        def chooser(*args, **kwargs):
            self.assertEqual(self.save()['status'], 'failed')
            with patch.object(self.saver._done, 'wait', return_value=False), self.assertRaises(RuntimeError):
                self.saver.close()
            return str(self.destination)
        self.window.create_file_dialog.side_effect = chooser
        self.assertEqual(self.save(), {'status': 'cancelled'})
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.saver.close(), {'closed': True, 'active': 0})
        self.assertEqual(self.save()['status'], 'failed')

    def test_backup_and_destination_readback_mismatch_do_not_claim_saved(self):
        self.destination.write_bytes(b'OLD')
        def transaction(path, data, expected, backup):
            try:
                backup(b'OLD')
            except OSError as error:
                raise conditional_save.SaveError('backup failed') from error
            self.fail('Failed backup allowed target write')
        with patch('nivram.reference_export.conditional_write_existing', side_effect=transaction), \
             patch.object(self.saver, '_read', side_effect=[b'OLD', b'WRONG']):
            self.assertEqual(self.save()['status'], 'failed')
        self.assertEqual(self.destination.read_bytes(), b'OLD')
        self.destination.unlink()
        with patch('nivram.reference_export.atomic_write', side_effect=lambda path, data, **kw: Path(path).write_bytes(b'WRONG')):
            self.assertEqual(self.save()['status'], 'unconfirmed')

    def test_desktop_uses_owned_token_without_mutating_project_state(self):
        api = DesktopApi(self.root / 'profile')
        for owner in (api._images, api._batch, api._reference_exports, api._population_exports):
            self.addCleanup(owner.close)
        api._window = self.window
        api._server = SimpleNamespace(api_token=self.token)
        api._path = self.root / 'unchanged.nivram'
        api._pending_open = ('pending', self.root / 'opening.nivram')
        self.assertEqual(api.save_population_export('json', self.payload, self.token)['status'], 'saved')
        self.assertEqual(api._path.name, 'unchanged.nivram')
        self.assertEqual(api._pending_open[0], 'pending')

    def test_missing_population_shutdown_receipt_fails_smoke_gate(self):
        self.assertIn('population_export_shutdown_unconfirmed', _smoke_failures({}))


if __name__ == '__main__':
    unittest.main()
