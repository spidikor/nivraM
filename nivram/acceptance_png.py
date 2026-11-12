"""Synthetic streamed large PNG fixture for native acceptance; no private pixels."""
import io
import struct
import zlib

from PIL import Image


def tiled_source(tile_png, *, size=(11559, 9999), origin=(1000, 1000)):
    width, height = size
    x, y = origin
    with Image.open(io.BytesIO(tile_png)) as image:
        if image.width * image.height > 1_000_000:
            raise ValueError('The synthetic tile must fit the detail-review limit.')
        tile = image.convert('RGBA')
    try:
        tw, th = tile.size
        if not (0 <= x <= width - tw and 0 <= y <= height - th):
            raise ValueError('The synthetic tile must fit the source.')
        pixels = tile.tobytes()
    finally:
        tile.close()
    white = b'\xff' * (width * 4)
    compressor = zlib.compressobj()
    parts = []
    for row in range(height):
        raster = white
        if y <= row < y + th:
            offset = (row - y) * tw * 4
            raster = white[:x*4] + pixels[offset:offset+tw*4] + white[(x+tw)*4:]
        parts.append(compressor.compress(b'\0' + raster))
    parts.append(compressor.flush())

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'tEXt', b'SMILES\0CCO') + chunk(b'IDAT', b''.join(parts)) + chunk(b'IEND', b''))
    return data, {'x': x, 'y': y, 'width': tw, 'height': th}
