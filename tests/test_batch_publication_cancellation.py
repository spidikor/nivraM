"""Cancellation after real publication, before its acknowledgement returns.

Event barriers force the ordering; preparation and PNGs remain synthetic.
"""
import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from nivram import batch_export as batch
import test_batch_publication_faults as fixtures


class PublicationCancellationTests(unittest.TestCase):
    setUp = fixtures.CompoundPublicationTests.setUp
    prepare = fixtures.CompoundPublicationTests.prepare
    write = fixtures.CompoundPublicationTests.write
    manifest = fixtures.CompoundPublicationTests.manifest

    def race_publication(self, *, lose_acknowledgement):
        operation = 'rename' if batch.os.name == 'nt' else 'link'
        original = getattr(batch.os, operation)
        published = threading.Event()
        release = threading.Event()
        cancel_returned = threading.Event()
        results, errors, attempts = {}, [], []

        def publication(source, destination):
            attempts.append(Path(destination).name)
            original(source, destination)
            if Path(destination) == self.target:
                published.set()
                if not release.wait(5):
                    raise AssertionError('Publication barrier was not released.')
                if lose_acknowledgement:
                    raise OSError('injected private path acknowledgement lost')

        def write():
            try:
                results['write'] = self.write(1)
            except BaseException as error:
                errors.append(('write', type(error).__name__))

        def cancel():
            try:
                results['cancel'] = self.manager.cancel(self.session['sessionId'])
            except BaseException as error:
                errors.append(('cancel', type(error).__name__))
            finally:
                cancel_returned.set()

        writer = threading.Thread(target=write, name='test-publish-before-ack', daemon=True)
        canceller = threading.Thread(target=cancel, name='test-cancel-during-ack', daemon=True)
        with patch.object(batch.os, operation, publication):
            try:
                writer.start()
                self.assertTrue(published.wait(2), 'Second PNG must commit before cancellation.')
                self.assertEqual(self.target.read_bytes(), self.png)
                canceller.start()
                self.assertTrue(self.manager._session['cancelled'].wait(2),
                                'Cancellation must be signalled while publication holds the lock.')
                self.assertFalse(cancel_returned.is_set(),
                                 'Cancellation receipt must await the in-flight write.')
            finally:
                release.set()
                for thread in (writer, canceller):
                    if thread.ident is not None:
                        thread.join(2)
                self.assertFalse(writer.is_alive(), 'Owned publication thread did not stop.')
                self.assertFalse(canceller.is_alive(), 'Owned cancellation thread did not stop.')

        self.assertEqual(errors, [])
        self.assertEqual(set(results), {'write', 'cancel'})
        self.assertEqual(attempts, ['input.png', self.target.name])
        self.assertEqual((self.destination / self.first['outputName']).read_bytes(), self.png)
        self.assertEqual(self.target.read_bytes(), self.png)
        self.assertEqual(list(self.destination.glob('*.tmp')), [])
        self.assertEqual({p.name for p in self.destination.glob('*.png')},
                         {self.first['outputName'], self.target.name})
        return results['write'], results['cancel']

    def assert_terminal_preserves_outputs(self, written, terminal, *, uncertain):
        status = 'stopped' if uncertain else 'cancelled'
        self.assertEqual(terminal['status'], status)
        self.assertIsNone(written['manifestError'])
        self.assertIsNone(terminal['manifestError'])
        self.assertEqual(terminal['items'][0], self.first)
        self.assertEqual(terminal['items'][1], written['item'])
        self.assertEqual([row['status'] for row in terminal['items']],
                         ['exported', 'failed' if uncertain else 'exported', 'cancelled'])
        disk = (self.destination / self.session['manifestName']).read_bytes()
        self.assertEqual(json.loads(disk), {'schema': 'nivram-batch-export/1', **terminal})
        self.assertEqual(self.prepare(2)['status'], 'cancelled')
        self.assertEqual(self.write(2)['status'], 'cancelled')
        self.assertEqual(len(self.service.calls), 2, 'No third preparation is permitted.')
        self.assertEqual(self.manager.finish(self.session['sessionId']), terminal)
        self.assertEqual(self.manager.cancel(self.session['sessionId']), terminal)
        self.assertEqual((self.destination / self.session['manifestName']).read_bytes(), disk)
        self.assertEqual((self.destination / self.first['outputName']).read_bytes(), self.png)
        self.assertEqual(self.target.read_bytes(), self.png)
        self.assertTrue(self.manager._session['cancelled'].is_set())
        self.assertIsNone(self.manager._session['active'])
        self.assertTrue(all(row['_bytes'] is None and '_preparation' not in row
                            for row in self.manager._session['items']))
        for receipt in (written, terminal):
            self.assertNotIn('private path', json.dumps(receipt))
            self.assertNotIn(str(self.root), json.dumps(receipt))

    def test_cancellation_cannot_downgrade_lost_publication_acknowledgement(self):
        written, terminal = self.race_publication(lose_acknowledgement=True)
        self.assertEqual(written['status'], 'stopped')
        self.assertEqual(written['item']['error']['code'], 'write_unverified')
        self.assertEqual(written['item']['outputName'], self.target.name)
        self.assertIsNone(written['item']['outputSha256'])
        self.assertIsNone(written['item']['dimensions'])
        self.assert_terminal_preserves_outputs(written, terminal, uncertain=True)

    def test_cancellation_preserves_publication_with_successful_acknowledgement(self):
        written, terminal = self.race_publication(lose_acknowledgement=False)
        self.assertEqual(written['status'], 'exported')
        self.assertIsNone(written['item']['error'])
        self.assertEqual(written['item']['outputName'], self.target.name)
        self.assertEqual(written['item']['outputSha256'], hashlib.sha256(self.png).hexdigest())
        self.assertEqual(written['item']['dimensions'], {'width': 32, 'height': 12, 'padding': 2})
        self.assert_terminal_preserves_outputs(written, terminal, uncertain=False)


if __name__ == '__main__':
    unittest.main()
