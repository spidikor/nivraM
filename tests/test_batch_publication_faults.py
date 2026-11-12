"""Compound publication faults with real files and injected preparation.

The PNG is synthetic; no chemistry worker, molecular renderer or native UI runs.
"""
import base64
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from nivram import batch_export as batch
from test_batch_preparation import PreparationService, PROJECT, SETTINGS


def synthetic_png():
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data)))
    rows = (b'\x00' + b'\xff' * (32 * 4)) * 12
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 32, 12, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class CompoundPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / 'outputs'
        self.destination.mkdir()
        self.source = self.root / 'input.nivram'
        self.source.write_text(json.dumps(PROJECT), encoding='utf-8')
        self.service = PreparationService()
        self.manager = batch.BatchExportManager(preparation_service=self.service)
        self.addCleanup(self.manager.close)
        self.png = synthetic_png()
        self.encoded = base64.b64encode(self.png).decode('ascii')
        self.session = self.manager._begin_selected(
            [self.source] * 3, self.destination, SETTINGS)
        self.assertEqual(self.prepare(0)['status'], 'ready')
        self.first = self.write(0)['item']
        self.assertEqual(self.first['status'], 'exported')
        self.assertEqual(self.first['outputSha256'], hashlib.sha256(self.png).hexdigest())
        self.assertEqual(self.prepare(1)['status'], 'ready')
        self.target = self.destination / 'input (2).png'

    def prepare(self, index):
        return self.manager.prepare(self.session['sessionId'], self.session['items'][index]['id'])

    def write(self, index):
        return self.manager.write_png(
            self.session['sessionId'], self.session['items'][index]['id'], self.encoded)

    def manifest(self):
        return json.loads((self.destination / self.session['manifestName']).read_text())

    def publication_and_cleanup_fault(self, *, publish, lose_ack=True):
        operation = 'rename' if batch.os.name == 'nt' else 'link'
        original_operation = getattr(batch.os, operation)
        original_unlink = Path.unlink
        attempted, temporary, cleanup = [], [], []

        def publication(source, destination):
            attempted.append(Path(destination).name)
            if Path(destination) != self.target:
                return original_operation(source, destination)
            temporary.append(Path(source))
            if publish:
                original_operation(source, destination)
            if lose_ack:
                raise OSError('injected private path publication error')

        def unlink(path, *args, **kwargs):
            if path in temporary:
                cleanup.append(path)
                raise PermissionError('injected private path temporary cleanup error')
            return original_unlink(path, *args, **kwargs)

        with patch.object(batch.os, operation, publication), patch.object(Path, 'unlink', unlink):
            result = self.write(1)
        self.assertEqual(attempted, ['input.png', self.target.name])
        self.assertEqual(len(temporary), 1)
        self.assertEqual(cleanup, temporary)
        # The existing first name collided and was preserved. Cleanup was
        # intentionally unsuccessful; only owned-directory teardown removes residue.
        self.assertEqual((self.destination / self.first['outputName']).read_bytes(), self.png)
        if publish:
            self.assertEqual(self.target.read_bytes(), self.png)
        else:
            self.assertFalse(self.target.exists())
        if not publish or batch.os.name != 'nt':
            self.assertEqual(temporary[0].read_bytes(), self.png)
        self.assertIsNone(result['manifestError'], 'A manifest fault must not hide the PNG fault.')
        return result

    def assert_uncertainty_stops(self, result, *, published):
        self.assertEqual(result['status'], 'stopped', json.dumps(result))
        item = result['item']
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(item['error']['code'], 'write_unverified')
        self.assertEqual(item['outputName'], self.target.name)
        self.assertIsNone(item['outputSha256'])
        self.assertIsNone(item['dimensions'])
        self.assertNotIn('private path', json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        receipt = self.manifest()
        self.assertEqual(receipt['status'], 'stopped')
        self.assertEqual(receipt['items'][0], self.first)
        self.assertEqual(receipt['items'][1], item)
        self.assertEqual([row['status'] for row in receipt['items']],
                         ['exported', 'failed', 'cancelled'])
        self.assertEqual(self.prepare(2)['status'], 'cancelled')
        self.assertEqual(self.write(2)['status'], 'cancelled')
        self.assertEqual(len(self.service.calls), 2, 'Third item must never reach preparation.')
        self.assertEqual(self.manager.finish(self.session['sessionId'])['status'], 'stopped')
        self.assertEqual(self.manager.cancel(self.session['sessionId'])['status'], 'stopped')
        self.assertTrue(self.manager._session['cancelled'].is_set())
        self.assertIsNone(self.manager._session['active'])
        self.assertTrue(all(row['_bytes'] is None and '_preparation' not in row
                            for row in self.manager._session['items']))
        self.assertEqual({p.name for p in self.destination.glob('*.png')},
                         {self.first['outputName']} | ({self.target.name} if published else set()))
        self.assertEqual((self.destination / self.first['outputName']).read_bytes(), self.png)
        if published:
            self.assertEqual(self.target.read_bytes(), self.png)

    def test_operation_failure_then_cleanup_failure_preserves_uncertainty(self):
        result = self.publication_and_cleanup_fault(publish=False)
        self.assert_uncertainty_stops(result, published=False)

    def test_lost_publication_ack_then_cleanup_failure_preserves_output_and_uncertainty(self):
        result = self.publication_and_cleanup_fault(publish=True)
        self.assert_uncertainty_stops(result, published=True)

    def test_successful_publication_then_cleanup_failure_remains_uncertain(self):
        result = self.publication_and_cleanup_fault(publish=True, lose_ack=False)
        self.assert_uncertainty_stops(result, published=True)

    def test_prepublication_flush_and_cleanup_failures_remain_per_file(self):
        original_fsync, original_unlink = batch.os.fsync, Path.unlink
        flush_errors, cleanup = [], []

        def fsync(fd):
            if not flush_errors:
                flush_errors.append(fd)
                raise OSError('injected prepublication flush error')
            return original_fsync(fd)

        def unlink(path, *args, **kwargs):
            if path.parent == self.destination and path.name.startswith('.nivram-batch-'):
                cleanup.append(path)
                raise PermissionError('injected temporary cleanup error')
            return original_unlink(path, *args, **kwargs)

        with patch.object(batch.os, 'fsync', fsync), patch.object(Path, 'unlink', unlink):
            result = self.write(1)
        self.assertEqual(len(flush_errors), 1)
        self.assertEqual(len(cleanup), 1)
        self.assertEqual(cleanup[0].read_bytes(), self.png)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['item']['error']['code'], 'write_failed')
        self.assertIsNone(result['item']['outputName'])
        self.assertIsNone(result['manifestError'])
        self.assertFalse(self.target.exists())
        self.assertEqual(self.manifest()['status'], 'running')
        self.assertEqual(self.prepare(2)['status'], 'ready')
        last = self.write(2)
        self.assertEqual(last['status'], 'exported')
        self.assertEqual(last['item']['outputName'], self.target.name)
        self.assertEqual(self.target.read_bytes(), self.png)
        self.assertEqual((self.destination / self.first['outputName']).read_bytes(), self.png)
        self.assertEqual(len(self.service.calls), 3)
        self.assertEqual(self.manager.finish(self.session['sessionId'])['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
