import io
import unittest

from PIL import Image

from nivram.acceptance_png import tiled_source
from nivram.image_import import inspect_png_metadata


class AcceptancePngTests(unittest.TestCase):
    def test_streamed_fixture_places_exact_rgba_tile_with_white_surround(self):
        tile = Image.new('RGBA', (13, 9), (12, 37, 245, 187))
        out = io.BytesIO()
        tile.save(out, format='PNG')
        data, rect = tiled_source(out.getvalue(), size=(81, 94), origin=(17, 29))
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            self.assertEqual(source.size, (81, 94))
            with source.crop((17, 29, 30, 38)) as crop:
                self.assertEqual(crop.tobytes(), tile.tobytes())
            self.assertEqual(source.getpixel((16, 29)), (255, 255, 255, 255))
            self.assertEqual(source.getpixel((30, 29)), (255, 255, 255, 255))
        self.assertEqual(rect, {'x': 17, 'y': 29, 'width': 13, 'height': 9})
        inspected = inspect_png_metadata(data)
        self.assertEqual(len(inspected['candidates']), 1)
        self.assertEqual(len(inspected['candidates'][0]['graph']['atoms']), 3)
        tile.close()
