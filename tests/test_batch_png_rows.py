"""Known synthetic PNG row bytes and fail-closed batch publication boundaries."""
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from nivram.batch_export import BatchExportManager, _decode_png, _unfilter_png_row
import test_batch_export as fixtures


def pixels(channels, *values):
    return b''.join(bytes(value[:channels]) for value in values)


class BatchPngRowTests(unittest.TestCase):
    def test_sub_and_zero_prior_paeth_preserve_channel_boundaries_and_wrap(self):
        for channels in (3, 4):
            filtered = pixels(channels, (255, 1, 128, 250), (2, 255, 130, 10), (255, 2, 128, 255))
            expected = pixels(channels, (255, 1, 128, 250), (1, 0, 2, 4), (0, 2, 130, 3))
            for kind in (1, 4):
                with self.subTest(channels=channels, kind=kind):
                    self.assertEqual(_unfilter_png_row(kind, filtered, bytes(len(filtered)), channels), expected)

    def test_average_uses_floor_of_full_sum_and_preserves_modulo_bytes(self):
        for channels in (3, 4):
            previous = pixels(channels, (1, 255, 3, 253), (255, 1, 255, 5))
            filtered = pixels(channels, (0, 128, 255, 2), (129, 128, 130, 255))
            expected = pixels(channels, (0, 255, 0, 128), (0, 0, 1, 65))
            with self.subTest(channels=channels):
                self.assertEqual(_unfilter_png_row(3, filtered, previous, channels), expected)

    def test_average_first_row_has_zero_above_and_zero_left_at_each_channel(self):
        for channels in (3, 4):
            filtered = pixels(channels, (255, 1, 128, 250), (129, 255, 192, 131))
            expected = pixels(channels, (255, 1, 128, 250), (0, 255, 0, 0))
            with self.subTest(channels=channels):
                self.assertEqual(_unfilter_png_row(3, filtered, bytes(len(filtered)), channels), expected)

    def test_paeth_distinguishing_left_corner_and_above_corner_ties(self):
        # At the second pixel, lanes 0/1 have (a,b,c)=(0,3,2)/(3,0,2).
        # They must choose a/b respectively over a different c, both predicting 0.
        # Lane 2 chooses c=1 uniquely; alpha lane exercises modulo-256 addition.
        for channels in (3, 4):
            previous = pixels(channels, (2, 2, 1, 253), (3, 0, 2, 252))
            filtered = pixels(channels, (254, 1, 255, 0), (255, 17, 128, 10))
            expected = pixels(channels, (0, 3, 0, 253), (255, 17, 129, 6))
            with self.subTest(channels=channels):
                self.assertEqual(_unfilter_png_row(4, filtered, previous, channels), expected)

    def test_none_and_up_do_not_need_the_native_image_decoder(self):
        for channels in (3, 4):
            filtered = pixels(channels, (255, 1, 128, 250))
            previous = pixels(channels, (1, 255, 128, 10))
            with patch('nivram.png_preview.Image.open', side_effect=AssertionError('Unexpected carrier')):
                self.assertEqual(_unfilter_png_row(0, filtered, previous, channels), filtered)
                self.assertEqual(_unfilter_png_row(2, filtered, previous, channels), pixels(channels, (0, 0, 0, 4)))

    def test_native_decoder_receives_only_two_row_carriers_without_source_metadata(self):
        actual_open = Image.open
        for color in (2, 6):
            channels = 3 if color == 2 else 4
            for kind in (1, 3, 4):
                with self.subTest(color=color, kind=kind):
                    source, _ = fixtures.pixel_png(color=color, kind=kind,
                        pixels=[(5, 5, (7, 81, 203, 255)[:channels])])
                    source = source[:33] + fixtures.chunk(b'pHYs', struct.pack('>IIB', 2835, 2835, 1)) + source[33:]
                    observations = []
                    def observed_open(carrier):
                        image = actual_open(carrier)
                        observations.append((image.size, image.mode, dict(image.info)))
                        return image
                    with patch('nivram.png_preview.Image.open', side_effect=observed_open):
                        returned, dimensions = _decode_png(fixtures.encoded(source), fixtures.settings()['export'], threading.Event())
                    self.assertEqual(returned, source)
                    self.assertEqual(dimensions, {'width': 32, 'height': 12, 'padding': 2})
                    # Guard the native raster/metadata boundary, allowing faster
                    # paths that need fewer carriers or no image opening at all.
                    self.assertTrue(all(size[0] == 32 and 1 <= size[1] <= 2
                        and mode == ('RGB' if color == 2 else 'RGBA') and info == {}
                        for size, mode, info in observations))

    def test_carrier_failure_marks_invalid_png_and_publishes_no_file(self):
        class StaticPreparation:
            def prepare_batch(self, *_args, **_kwargs):
                project = fixtures.project()
                return {'project': project, 'graph': project['graph'], 'scene': {}}
        for error in (OSError('private native decoder detail'), SyntaxError('private native decoder detail')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory(prefix='nv-row-failure-') as directory:
                root = Path(directory)
                source = root/'synthetic.nivram'
                original = json.dumps(fixtures.project()).encode('utf-8')
                source.write_bytes(original)
                destination = root/'outputs'
                destination.mkdir()
                manager = BatchExportManager(preparation_service=StaticPreparation())
                try:
                    batch = manager._begin_selected([source], destination, fixtures.settings())
                    item_id = batch['items'][0]['id']
                    self.assertEqual(manager.prepare(batch['sessionId'], item_id)['status'], 'ready')
                    data, _ = fixtures.pixel_png(kind=4)
                    with patch('nivram.png_preview.Image.open', side_effect=error):
                        result = manager.write_png(batch['sessionId'], item_id, fixtures.encoded(data))
                    self.assertEqual(result['status'], 'failed')
                    self.assertEqual(result['item']['error']['code'], 'invalid_png')
                    self.assertIsNone(result['item']['outputName'])
                    self.assertIsNone(result['item']['outputSha256'])
                    self.assertEqual(list(destination.glob('*.png')), [])
                    self.assertEqual(source.read_bytes(), original)
                    manifest = json.loads((destination/batch['manifestName']).read_text(encoding='utf-8'))
                    self.assertEqual(manifest['items'][0]['status'], 'failed')
                    self.assertNotIn('private native', json.dumps(result))
                finally:
                    manager.close()


if __name__ == '__main__':
    unittest.main()
