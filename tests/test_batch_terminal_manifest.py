"""A committed final manifest is not an acknowledgement of successful finish.

Real temporary files, injected preparation and synthetic PNGs; no rendering.
"""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from nivram import batch_export as batch
import test_batch_publication_faults as fixtures


class TerminalManifestTests(unittest.TestCase):
    setUp = fixtures.CompoundPublicationTests.setUp
    prepare = fixtures.CompoundPublicationTests.prepare
    write = fixtures.CompoundPublicationTests.write
    manifest = fixtures.CompoundPublicationTests.manifest

    def test_lost_final_manifest_ack_stays_stopped_and_preserves_completed_outputs(self):
        second = self.write(1)['item']
        self.assertEqual(self.prepare(2)['status'], 'ready')
        third = self.write(2)['item']
        items = [self.first, second, third]
        self.assertEqual([item['status'] for item in items], ['exported'] * 3)
        self.assertEqual(self.manifest()['status'], 'running')
        self.assertEqual(len(self.service.calls), 3)
        manifest_path = self.destination / self.session['manifestName']
        original = batch.atomic_write
        committed = []

        def lose_final_ack(path, data):
            self.assertEqual(Path(path), manifest_path)
            self.assertEqual(json.loads(data)['status'], 'completed')
            original(path, data)
            committed.append(bytes(data))
            raise OSError('injected private path final manifest acknowledgement loss')

        with patch.object(batch, 'atomic_write', lose_final_ack):
            stopped = self.manager.finish(self.session['sessionId'])
            self.assertEqual(stopped['status'], 'stopped')
            self.assertIsNotNone(stopped['manifestError'])
            self.assertEqual(stopped['items'], items)
            self.assertNotIn('private path', json.dumps(stopped))
            self.assertNotIn(str(self.root), json.dumps(stopped))
            self.assertEqual(self.manager.finish(self.session['sessionId']), stopped)
            self.assertEqual(self.manager.cancel(self.session['sessionId']), stopped)

        # The actual committed bytes describe completion; the live receipt must
        # retain uncertainty without silently rewriting those recovery bytes.
        self.assertEqual(len(committed), 1)
        self.assertEqual(manifest_path.read_bytes(), committed[0])
        actual = json.loads(committed[0])
        self.assertEqual(actual['status'], 'completed')
        self.assertIsNone(actual['manifestError'])
        self.assertEqual(actual['items'], items)
        self.assertEqual({path.name for path in self.destination.glob('*.png')},
                         {item['outputName'] for item in items})
        for item in items:
            data = (self.destination / item['outputName']).read_bytes()
            self.assertEqual(data, self.png)
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['outputSha256'])
        self.assertTrue(self.manager._session['cancelled'].is_set())
        self.assertIsNone(self.manager._session['active'])
        self.assertTrue(all(item['_bytes'] is None and '_preparation' not in item
                            for item in self.manager._session['items']))


if __name__ == '__main__':
    unittest.main()
