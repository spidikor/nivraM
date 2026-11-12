"""Real filesystem publication faults; synthetic PNGs, not browser rendering."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import nivram.batch_export as batch_module
import test_batch_export as fixtures
from test_batch_export import png, project, settings


class PublicationTests(unittest.TestCase):
    setUp = fixtures.BatchExportTests.setUp
    tearDown = fixtures.BatchExportTests.tearDown
    begin = fixtures.BatchExportTests.begin
    prepare = fixtures.BatchExportTests.prepare
    write = fixtures.BatchExportTests.write
    manifest = fixtures.BatchExportTests.manifest

    def first_saved_second_ready(self):
        batch = self.begin([self.source] * 3)
        self.prepare(batch)
        first = self.write(batch)['item']
        self.prepare(batch, 1)
        return batch, first, self.destination / 'ethanol (2).png'

    def assert_uncertain_stops(self, batch, first, result, target, retained):
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['item']['status'], 'failed')
        self.assertEqual(result['item']['error']['code'], 'write_unverified')
        self.assertEqual(result['item']['outputName'], target.name)
        self.assertIsNone(result['item']['outputSha256'])
        self.assertIsNone(result['item']['dimensions'])
        self.assertNotIn('private path', json.dumps(result))
        if retained is None:
            self.assertFalse(target.exists())
        else:
            self.assertEqual(target.read_bytes(), retained)
        self.assertEqual((self.destination / first['outputName']).read_bytes(), png())
        manifest = self.manifest(batch)
        self.assertEqual(manifest['status'], 'stopped')
        self.assertEqual(manifest['items'][0], first)
        self.assertEqual(manifest['items'][1], result['item'])
        self.assertEqual([item['status'] for item in manifest['items']], ['exported', 'failed', 'cancelled'])
        with patch.object(self.manager._preparation_service, 'prepare_batch') as prepare:
            self.assertEqual(self.prepare(batch, 2)['status'], 'cancelled')
            prepare.assert_not_called()
        self.assertEqual(self.write(batch, 2)['status'], 'cancelled')
        self.assertEqual(self.manager.finish(batch['sessionId'])['status'], 'stopped')
        self.assertEqual(self.manager.cancel(batch['sessionId'])['status'], 'stopped')
        self.assertEqual({path.name for path in self.destination.glob('*.png')},
                         {first['outputName']} | ({target.name} if retained is not None else set()))
        self.assertTrue(self.manager._session['cancelled'].is_set())
        self.assertIsNone(self.manager._session['active'])
        self.assertTrue(all(item['_bytes'] is None and '_preparation' not in item
                            for item in self.manager._session['items']))

    def test_published_png_readback_error_stops_and_retains_unverified_output(self):
        batch, first, target = self.first_saved_second_ready()
        original = Path.open
        observed = []
        def fail_read(path, mode='r', *args, **kwargs):
            if path == target and mode == 'rb':
                observed.append(path)
                raise PermissionError('injected private path readback error')
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', fail_read):
            result = self.write(batch, 1)
        self.assertEqual(observed, [target])
        self.assert_uncertain_stops(batch, first, result, target, png())

    def test_publication_operation_error_stops_without_claiming_output_exists(self):
        batch, first, target = self.first_saved_second_ready()
        operation = 'rename' if batch_module.os.name == 'nt' else 'link'
        original = getattr(batch_module.os, operation)
        def fail_target(source, destination):
            if Path(destination) == target:
                raise PermissionError('injected private path operation error')
            return original(source, destination)
        with patch.object(batch_module.os, operation, side_effect=fail_target):
            result = self.write(batch, 1)
        self.assert_uncertain_stops(batch, first, result, target, None)

    def test_published_png_hash_mismatch_stops_without_claiming_verified_digest(self):
        batch, first, target = self.first_saved_second_ready()
        original = batch_module._publish_unique
        replacement = b'Owned synthetic external replacement; preserve this file.'
        def replace_after_publish(*args, **kwargs):
            published = original(*args, **kwargs)
            self.assertEqual(published, target)
            published.write_bytes(replacement)
            return published
        with patch.object(batch_module, '_publish_unique', replace_after_publish):
            result = self.write(batch, 1)
        self.assert_uncertain_stops(batch, first, result, target, replacement)

    def test_postpublication_temporary_cleanup_error_does_not_lose_output_identity(self):
        batch, first, target = self.first_saved_second_ready()
        original = Path.unlink
        observed = []
        def fail_cleanup(path, *args, **kwargs):
            if path.parent == self.destination and path.name.startswith('.nivram-batch-'):
                self.assertEqual(target.read_bytes(), png())
                observed.append(path)
                raise PermissionError('injected private path cleanup error')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_cleanup):
            result = self.write(batch, 1)
        self.assertEqual(len(observed), 1)
        self.assert_uncertain_stops(batch, first, result, target, png())

    def test_manifest_acknowledgement_lost_after_commit_retains_actual_manifest(self):
        batch, first, target = self.first_saved_second_ready()
        original = batch_module.atomic_write
        committed = []
        def lose_acknowledgement(path, data):
            original(path, data)
            committed.append((path, bytes(data)))
            raise OSError('synthetic manifest acknowledgement lost')
        with patch.object(batch_module, 'atomic_write', lose_acknowledgement):
            result = self.write(batch, 1)
        self.assertEqual(result['status'], 'exported')
        self.assertIsNotNone(result['manifestError'])
        self.assertEqual(len(committed), 1)
        path, data = committed[0]
        self.assertEqual(path.read_bytes(), data)
        actual = json.loads(data)
        self.assertEqual(actual['status'], 'running')
        self.assertEqual([item['status'] for item in actual['items']], ['exported', 'exported', 'pending'])
        stopped = self.manager.finish(batch['sessionId'])
        self.assertEqual(stopped['status'], 'stopped')
        self.assertEqual([item['status'] for item in stopped['items']], ['exported', 'exported', 'cancelled'])
        self.assertEqual(self.prepare(batch, 2)['status'], 'cancelled')
        self.assertEqual(self.write(batch, 2)['status'], 'cancelled')
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(target.read_bytes(), png())
        self.assertEqual((self.destination / first['outputName']).read_bytes(), png())

    def test_source_and_profile_snapshot_survive_edits_after_preparation(self):
        original_source = self.source.read_bytes()
        original_graph = copy.deepcopy(project()['graph'])
        chosen = settings()
        chosen['profile'] = {'id': '1' * 32, 'revision': 1, 'name': 'Synthetic profile'}
        frozen = copy.deepcopy(chosen)
        batch = self.begin([self.source, self.source], chosen)
        first_ready = self.prepare(batch)
        chosen['style']['bondWidth'] = 5
        chosen['export']['width'] = 9000
        chosen['profile'].update(revision=2, name='Changed after preparation')
        self.source.write_bytes(b'Changed after preparation; not a project.')
        for index in range(2):
            ready = first_ready if index == 0 else self.prepare(batch, index)
            self.assertEqual(ready['status'], 'ready')
            self.assertEqual(ready['graph'], original_graph)
            self.assertEqual(ready['project']['style'], frozen['style'])
            self.assertEqual(ready['project']['export'], frozen['export'])
            written = self.write(batch, index)
            self.assertEqual(written['status'], 'exported')
            data = (self.destination / written['item']['outputName']).read_bytes()
            self.assertEqual(data, png())
            self.assertEqual(written['item']['outputSha256'], hashlib.sha256(data).hexdigest())
        complete = self.manager.finish(batch['sessionId'])
        manifest = self.manifest(batch)
        self.assertEqual(manifest, {'schema': 'nivram-batch-export/1', **complete})
        self.assertEqual(manifest['settings'], frozen)
        self.assertTrue(all(item['sourceSha256'] == hashlib.sha256(original_source).hexdigest()
                            for item in manifest['items']))
        self.assertEqual(self.source.read_bytes(), b'Changed after preparation; not a project.')


if __name__ == '__main__':
    unittest.main()
