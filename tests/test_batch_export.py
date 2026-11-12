import base64
import copy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

from PIL import Image

from nivram.batch_export import BatchExportManager, _decode_png, _publish_unique
from nivram.chemistry import export_mol, import_structure
from nivram.scene import DEFAULT_STYLE


def settings(width=32):
    return {'style': {**DEFAULT_STYLE, 'bondWidth': 2},
            'export': {'width': width, 'marginMode': 'pixels', 'margin': 2}, 'profile': None}


def project():
    return {'schema': 'nivram/1', 'graph': import_structure('CCO'),
            'style': DEFAULT_STYLE, 'export': {'width': 2000, 'marginMode': 'percent', 'margin': 2}}


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(data, zlib.crc32(kind)))


def png(width=32, height=12, compressed=None, suffix=b'', depth=8, color=6):
    rows = b''.join(b'\x00' + b'\xff\xff\xff\xff' * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows) if compressed is None else compressed) + chunk(b'IEND', b'') + suffix)


def encoded(data=None):
    return base64.b64encode(png() if data is None else data).decode('ascii')


def pixel_png(*, width=32, height=12, color=6, kind=0, pixels=(), idat_slice=None):
    """Synthetic filter fixtures, independently decoded by Pillow in tests."""
    channels = 4 if color == 6 else 3
    raster = bytearray(b'\xff' * (width * height * channels))
    for x, y, value in pixels:
        offset = (y*width+x)*channels
        raster[offset:offset+channels] = bytes(value)
    stride = width*channels
    rows, previous = [], bytes(stride)
    for y in range(height):
        row = bytes(raster[y*stride:(y+1)*stride])
        filtered = bytearray(stride)
        for index, value in enumerate(row):
            left = row[index-channels] if index >= channels else 0
            above = previous[index]
            corner = previous[index-channels] if index >= channels else 0
            if kind == 0:
                predictor = 0
            elif kind == 1:
                predictor = left
            elif kind == 2:
                predictor = above
            elif kind == 3:
                predictor = (left+above)//2
            else:
                estimate = left+above-corner
                predictor = min((left, above, corner), key=lambda candidate: abs(estimate-candidate))
            filtered[index] = (value-predictor) % 256
        rows.append(bytes([kind])+filtered)
        previous = row
    stream = zlib.compress(b''.join(rows))
    pieces = (stream,) if idat_slice is None else tuple(stream[index:index+idat_slice]
                                                     for index in range(0, len(stream), idat_slice))
    data = (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0))
            + b''.join(chunk(b'IDAT', piece) for piece in pieces) + chunk(b'IEND', b''))
    return data, bytes(raster)


def assert_pixel_fixture(test, data, raster, color, width=32, height=12):
    with Image.open(BytesIO(data)) as image:
        test.assertEqual(image.size, (width, height))
        test.assertEqual(image.mode, 'RGBA' if color == 6 else 'RGB')
        test.assertEqual(image.tobytes(), raster)


class BatchExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.destination = self.root / 'outputs'
        self.destination.mkdir()
        self.source = self.root / 'ethanol.nivram'
        self.source.write_text(json.dumps(project()), encoding='utf-8')
        self.manager = BatchExportManager()

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def begin(self, paths=None, chosen=None):
        return self.manager._begin_selected(paths or [self.source], self.destination, chosen or settings())

    def prepare(self, batch, index=0):
        return self.manager.prepare(batch['sessionId'], batch['items'][index]['id'])

    def write(self, batch, index=0, data=None):
        return self.manager.write_png(batch['sessionId'], batch['items'][index]['id'], encoded(data))

    def manifest(self, batch):
        return json.loads((self.destination / batch['manifestName']).read_text(encoding='utf-8'))

    def test_two_native_dialogs_are_the_only_public_path_boundary(self):
        class Dialogs:
            calls = []
            def create_file_dialog(inner, kind, **kwargs):
                inner.calls.append((kind, kwargs))
                return (str(self.source),) if len(inner.calls) == 1 else (str(self.destination),)
        dialog = Dialogs()
        batch = self.manager.begin(dialog, settings())
        self.assertEqual(batch['status'], 'running')
        self.assertTrue(dialog.calls[0][1]['allow_multiple'])
        self.assertEqual(len(dialog.calls), 2)
        self.assertNotIn(str(self.root), json.dumps(batch))
        with self.assertRaises(ValueError):
            self.manager.prepare(batch['sessionId'], str(self.source))
        with self.assertRaises(ValueError):
            self.manager.begin(dialog, settings())
        self.manager.cancel(batch['sessionId'])

    def test_input_and_settings_are_snapshots_not_late_disk_reads(self):
        original = self.source.read_bytes()
        chosen = settings()
        batch = self.begin(chosen=chosen)
        chosen['style']['bondWidth'] = 5
        chosen['export']['width'] = 9000
        self.source.write_text('malformed later edit', encoding='utf-8')
        result = self.prepare(batch)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['project']['style']['bondWidth'], 2)
        self.assertEqual(result['project']['export']['width'], 32)
        self.assertEqual(result['item']['sourceSha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(result['graph'], project()['graph'])
        result['project']['graph']['atoms'][0]['x'] = 999
        self.assertEqual(batch['settings']['style']['bondWidth'], 2)
        self.assertEqual(self.source.read_text(), 'malformed later edit')

    def test_project_and_mol_preserve_graph_and_have_verified_result_hashes(self):
        mol = self.root / 'second.mol'
        mol.write_text(export_mol(project()['graph']), encoding='utf-8')
        batch = self.begin([self.source, mol])
        for index in range(2):
            ready = self.prepare(batch, index)
            self.assertEqual(ready['status'], 'ready')
            self.assertEqual(len(ready['graph']['atoms']), 3)
            written = self.write(batch, index)
            self.assertEqual(written['status'], 'exported')
            item = written['item']
            data = (self.destination / item['outputName']).read_bytes()
            self.assertEqual(item['outputSha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(item['dimensions'], {'width': 32, 'height': 12, 'padding': 2})
            self.assertEqual(self.manifest(batch)['items'][index]['status'], 'exported')
        result = self.manager.finish(batch['sessionId'])
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn(str(self.root), json.dumps(self.manifest(batch)))
        self.assertTrue(all(item['_bytes'] is None for item in self.manager._session['items']))

    def test_malformed_png_and_smiles_disguised_as_mol_are_not_inputs(self):
        files = []
        for name, data in [('bad.nivram', b'{invalid'), ('smiles.mol', b'CCO'),
                           ('reference.png', png()), ('empty.nivram', b'{"schema":"nivram/1","graph":{"atoms":[],"bonds":[]}}')]:
            path = self.root / name
            path.write_bytes(data)
            files.append(path)
        batch = self.begin(files + [self.source])
        for index in range(len(files)):
            if batch['items'][index]['status'] == 'pending':
                self.assertEqual(self.prepare(batch, index)['status'], 'failed')
        self.assertEqual(self.prepare(batch, 4)['status'], 'ready')
        self.assertEqual(self.write(batch, 4)['status'], 'exported')
        manifest = self.manager.finish(batch['sessionId'])
        self.assertEqual([item['error']['code'] for item in manifest['items'][:4]],
                         ['invalid_input', 'invalid_input', 'unsupported_type', 'empty_drawing'])

    def test_sdf_and_3d_mol_are_rejected(self):
        from rdkit import Chem
        from rdkit.Chem import rdDepictor
        molecule = Chem.MolFromSmiles('CCO')
        rdDepictor.Compute2DCoords(molecule)
        molecule.GetConformer().Set3D(True)
        block = Chem.MolToMolBlock(molecule)
        paths = []
        for index, data in enumerate((block, export_mol(project()['graph']) + '\n$$$$\n')):
            path = self.root / f'bad{index}.mol'
            path.write_text(data, encoding='utf-8')
            paths.append(path)
        batch = self.begin(paths)
        for index in range(2):
            self.assertEqual(self.prepare(batch, index)['status'], 'failed')

    def test_sequential_and_opaque_id_contract(self):
        batch = self.begin([self.source, self.source])
        with self.assertRaises(ValueError):
            self.prepare(batch, 1)
        self.prepare(batch)
        with self.assertRaises(ValueError):
            self.prepare(batch)
        with self.assertRaises(ValueError):
            self.prepare(batch, 1)
        with self.assertRaises(ValueError):
            self.manager.write_png('../outside', batch['items'][0]['id'], encoded())
        with self.assertRaises(ValueError):
            self.manager.fail(batch['sessionId'], batch['items'][0]['id'], 'C:\\private\\data')
        self.write(batch)
        with self.assertRaises(ValueError):
            self.write(batch)
        with self.assertRaises(ValueError):
            self.manager.finish(batch['sessionId'])

    def test_unique_names_preserve_existing_and_duplicate_input_results(self):
        existing = self.destination / 'ethanol.png'
        existing.write_bytes(b'earlier user file')
        batch = self.begin([self.source, self.source])
        for index in range(2):
            self.prepare(batch, index)
            self.write(batch, index)
        result = self.manager.finish(batch['sessionId'])
        self.assertEqual(existing.read_bytes(), b'earlier user file')
        self.assertEqual([item['outputName'] for item in result['items']], ['ethanol (2).png', 'ethanol (3).png'])
        self.assertEqual(list(self.destination.glob('*.tmp')), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows exclusive rename behavior')
    def test_collision_created_at_publication_is_retried_without_overwrite(self):
        real_rename = os.rename
        collided = False
        def race(source, destination):
            nonlocal collided
            if Path(destination).suffix == '.png' and not collided:
                collided = True
                Path(destination).write_bytes(b'concurrent user file')
            return real_rename(source, destination)
        batch = self.begin()
        self.prepare(batch)
        with patch('nivram.batch_export.os.rename', side_effect=race):
            result = self.write(batch)
        self.assertEqual(result['item']['outputName'], 'ethanol (2).png')
        self.assertEqual((self.destination / 'ethanol.png').read_bytes(), b'concurrent user file')

    def test_later_write_failure_retains_earlier_success_and_continues(self):
        batch = self.begin([self.source, self.source, self.source])
        self.prepare(batch)
        first = self.write(batch)['item']
        self.prepare(batch, 1)
        with patch('nivram.batch_export._publish_unique', side_effect=OSError('private path deliberately hidden')):
            failed = self.write(batch, 1)
        self.assertEqual(failed['item']['error']['code'], 'write_failed')
        self.assertNotIn('private path', json.dumps(failed))
        self.prepare(batch, 2)
        self.write(batch, 2)
        self.manager.finish(batch['sessionId'])
        self.assertEqual((self.destination / first['outputName']).read_bytes(), png())

    def test_manifest_failure_stops_batch_but_preserves_published_png(self):
        batch = self.begin([self.source, self.source])
        self.prepare(batch)
        before = (self.destination / batch['manifestName']).read_bytes()
        with patch('nivram.batch_export.atomic_write', side_effect=OSError('disk unavailable')):
            result = self.write(batch)
        self.assertEqual(result['status'], 'exported')
        self.assertIsNotNone(result['manifestError'])
        self.assertEqual((self.destination / result['item']['outputName']).read_bytes(), png())
        self.assertEqual((self.destination / batch['manifestName']).read_bytes(), before)
        finished = self.manager.finish(batch['sessionId'])
        self.assertEqual(finished['status'], 'stopped')
        self.assertEqual(finished['items'][1]['status'], 'cancelled')

    def test_externally_modified_manifest_is_not_replaced(self):
        batch = self.begin()
        path = self.destination / batch['manifestName']
        path.write_bytes(b'user replacement')
        self.prepare(batch)
        self.assertEqual(path.read_bytes(), b'user replacement')
        self.assertEqual(self.manager.finish(batch['sessionId'])['status'], 'stopped')

    def test_cancel_preserves_success_and_rejects_late_render_or_write(self):
        batch = self.begin([self.source, self.source, self.source])
        self.prepare(batch)
        self.write(batch)
        self.prepare(batch, 1)
        cancelled = self.manager.cancel(batch['sessionId'])
        self.assertEqual([item['status'] for item in cancelled['items']], ['exported', 'cancelled', 'cancelled'])
        self.assertEqual(self.write(batch, 1)['status'], 'cancelled')
        self.assertEqual(self.prepare(batch, 2)['status'], 'cancelled')
        self.assertEqual(len(list(self.destination.glob('*.png'))), 1)
        self.assertEqual(self.manifest(batch)['status'], 'cancelled')

    def test_close_cancels_and_releases_source_snapshots(self):
        batch = self.begin()
        receipt = self.manager.close()
        self.assertEqual(receipt['status'], 'cancelled')
        self.assertIsNone(self.manager._session['items'][0]['_bytes'])
        self.assertEqual(self.manager.finish(batch['sessionId'])['status'], 'cancelled')

    def test_application_shutdown_while_folder_dialog_is_open_cannot_start_a_late_batch(self):
        class Dialogs:
            calls = 0
            def create_file_dialog(inner, kind, **kwargs):
                inner.calls += 1
                if inner.calls == 1:
                    return (str(self.source),)
                self.manager.close()
                return (str(self.destination),)
        self.assertEqual(self.manager.begin(Dialogs(), settings()), {'status': 'cancelled'})
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_bounded_source_snapshots_and_no_full_hash_claim_for_rejected_reads(self):
        with patch('nivram.batch_export.MAX_PROJECT_BYTES', 10):
            batch = self.begin()
        self.assertEqual(batch['items'][0]['error']['code'], 'source_too_large')
        self.assertIsNone(batch['items'][0]['sourceSha256'])
        self.manager.finish(batch['sessionId'])
        with patch('nivram.batch_export.MAX_TOTAL_SOURCE_BYTES', self.source.stat().st_size):
            second = self.begin([self.source, self.source])
        self.assertEqual(second['items'][1]['error']['code'], 'source_total_limit')
        self.assertIsNone(second['items'][1]['sourceSha256'])

    def test_cancellation_before_atomic_publish_leaves_no_partial_file(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(InterruptedError):
            _publish_unique(self.destination, 'cancelled', '.png', png(), event)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_pixel_contract_failure_never_publishes_or_changes_input_and_later_item_succeeds(self):
        original = self.source.read_bytes()
        invalid = [pixel_png(kind=4, pixels=[(5, 5, (0, 0, 0, 254))])[0],
                   pixel_png(color=2, kind=3, pixels=[(1, 5, (254, 255, 255))])[0],
                   png(compressed=zlib.compress(b'\x00'+b'\xff'*127)),
                   png(compressed=zlib.compress((b'\x05'+b'\xff'*128)*12))]
        existing = self.destination / 'ethanol.png'
        existing.write_bytes(b'existing user data')
        batch = self.begin([self.source] * (len(invalid)+1))
        for index, data in enumerate(invalid):
            self.assertEqual(self.prepare(batch, index)['status'], 'ready')
            with patch('nivram.batch_export._publish_unique') as publish:
                result = self.write(batch, index, data)
                publish.assert_not_called()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['item']['error']['code'], 'invalid_png')
            self.assertIsNone(result['item']['outputName'])
            self.assertEqual(list(self.destination.glob('*.png')), [existing])
            self.assertEqual(list(self.destination.glob('*.tmp')), [])
            self.assertEqual(self.manifest(batch)['items'][index]['status'], 'failed')
        self.prepare(batch, len(invalid))
        self.assertEqual(self.write(batch, len(invalid))['status'], 'exported')
        self.assertEqual(self.manager.finish(batch['sessionId'])['status'], 'completed')
        self.assertEqual(existing.read_bytes(), b'existing user data')
        self.assertEqual(self.source.read_bytes(), original)


class BatchPngValidationTests(unittest.TestCase):
    def decode(self, data, event=None):
        return _decode_png(encoded(data), settings()['export'], event or threading.Event())

    def test_complete_stream_and_dimensions(self):
        data, dimensions = self.decode(png())
        self.assertEqual(data, png())
        self.assertEqual(dimensions, {'width': 32, 'height': 12, 'padding': 2})

    def test_all_five_filters_accept_exact_white_borders_and_opaque_colored_content(self):
        for color in (2, 6):
            channels = 3 if color == 2 else 4
            for kind in range(5):
                with self.subTest(color=color, kind=kind):
                    data, raster = pixel_png(color=color, kind=kind,
                        pixels=[(2, 2, (7, 81, 203, 255)[:channels]),
                                (29, 9, (199, 33, 121, 255)[:channels])])
                    assert_pixel_fixture(self, data, raster, color)
                    self.assertEqual(self.decode(data)[0], data)

    def test_transparency_inside_drawing_is_rejected_for_every_filter(self):
        for kind in range(5):
            for alpha in (0, 254):
                with self.subTest(kind=kind, alpha=alpha):
                    data, raster = pixel_png(kind=kind, pixels=[(5, 5, (7, 81, 203, alpha))])
                    assert_pixel_fixture(self, data, raster, 6)
                    with self.assertRaisesRegex(ValueError, 'transparent pixels'):
                        self.decode(data)

    def test_each_requested_margin_rejects_nearly_white_pixels_with_every_filter(self):
        for color in (2, 6):
            channels = 3 if color == 2 else 4
            for kind in range(5):
                for x, y in ((8, 1), (8, 10), (1, 5), (30, 5)):
                    with self.subTest(color=color, kind=kind, x=x, y=y):
                        data, raster = pixel_png(color=color, kind=kind,
                                                pixels=[(x, y, (255, 254, 255, 255)[:channels])])
                        assert_pixel_fixture(self, data, raster, color)
                        with self.assertRaisesRegex(ValueError, 'margins must be opaque white'):
                            self.decode(data)

    def test_zero_and_fractional_margins_use_exact_ceiling_without_extra_padding(self):
        for mode, margin, padding in (('pixels', 0, 0), ('pixels', 2.1, 3), ('percent', 2.5, 1)):
            with self.subTest(mode=mode, margin=margin):
                options = {'width': 32, 'marginMode': mode, 'margin': margin}
                data, _ = pixel_png(kind=4, pixels=[(padding, padding, (0, 33, 199, 255))])
                self.assertEqual(_decode_png(encoded(data), options, threading.Event())[1]['padding'], padding)
                if padding:
                    invalid, _ = pixel_png(kind=4, pixels=[(padding-1, padding, (0, 33, 199, 255))])
                    with self.assertRaisesRegex(ValueError, 'margins must be opaque white'):
                        _decode_png(encoded(invalid), options, threading.Event())

    def test_maximum_width_scanlines_cross_decode_and_idat_blocks_without_losing_pixels(self):
        data, raster = pixel_png(width=9999, height=7, kind=4, idat_slice=3,
                                pixels=[(2, 2, (7, 81, 203, 255)), (9996, 4, (199, 33, 121, 255))])
        assert_pixel_fixture(self, data, raster, 6, width=9999, height=7)
        decoded, dimensions = _decode_png(encoded(data), settings(9999)['export'], threading.Event())
        self.assertEqual(decoded, data)
        self.assertEqual(dimensions, {'width': 9999, 'height': 7, 'padding': 2})

    def test_cancel_between_reconstructed_rows_stops_pixel_validation(self):
        class CancelBetweenRows:
            count = 0
            def is_set(inner):
                inner.count += 1
                return inner.count > 3
        data, _ = pixel_png(kind=4)
        with self.assertRaises(InterruptedError):
            self.decode(data, CancelBetweenRows())

    def test_large_compressible_png_streams_across_bounded_decode_blocks(self):
        data = png(width=1024, height=512)
        decoded, dimensions = _decode_png(encoded(data), settings(1024)['export'], threading.Event())
        self.assertEqual(decoded, data)
        self.assertEqual(dimensions['height'], 512)

    def test_deflate_stream_may_cross_contiguous_idat_boundaries(self):
        raw = b''.join(b'\x00' + b'\xff' * (32 * 4) for _ in range(12))
        stream = zlib.compress(raw)
        split = png()[:33] + b''.join(chunk(b'IDAT', stream[index:index+3]) for index in range(0, len(stream), 3)) + chunk(b'IEND', b'')
        self.assertEqual(self.decode(split)[1]['width'], 32)

    def test_corrupt_truncated_extra_and_bomb_streams_are_rejected(self):
        wrong_filter = zlib.compress((b'\x05' + b'\xff' * 128) * 12)
        invalid = [png(width=31), png(height=32768), png()[:-1], png(suffix=b'extra'),
                   png(compressed=zlib.compress(b'\x00' * 100000)), png(compressed=zlib.compress(b'\x00')[:-1]),
                   png(compressed=zlib.compress(b'x') + zlib.compress(b'y')), png(compressed=wrong_filter),
                   png()[:30] + b'\x00' + png()[31:]]
        for data in invalid:
            with self.subTest(length=len(data)), self.assertRaises((ValueError, zlib.error)):
                self.decode(data)

    def test_transport_size_checked_before_decode(self):
        with patch('nivram.batch_export.MAX_PNG_BYTES', 3), self.assertRaises(ValueError):
            self.decode(png())
        with self.assertRaises(ValueError):
            _decode_png('%%%invalid', settings()['export'], threading.Event())

    def test_cancel_during_native_stream_validation(self):
        class CancelDuringChunks:
            count = 0
            def is_set(inner):
                inner.count += 1
                return inner.count > 2
        with self.assertRaises(InterruptedError):
            self.decode(png(), CancelDuringChunks())


if __name__ == '__main__':
    unittest.main()
