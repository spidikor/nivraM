"""Strict, row-bounded PNG overview/crop derivation; no chemical recognition.

Non-interlaced grayscale 1/2/4/8/16-bit, indexed 1/2/4/8-bit and
RGB/gray-alpha/RGBA 8/16-bit sources support sample/palette transparency.
Adam7 supports the legal core color/depth combinations through 16 bits using bounded pass rows.
The source is
CRC-checked and fully decompressed sequentially, including rows outside a crop.
Pillow sees generated two-row carriers only, never the source dimensions.
Its pinned C decoder implements the dependent PNG byte-row filters.
"""
from __future__ import annotations

import base64
from array import array
from dataclasses import dataclass
import hashlib
import io
import math
import struct
import sys
import time
import zlib

from PIL import Image, __version__ as PILLOW_VERSION

from . import png_icc


MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 128_000_000
MAX_EDGE = 32_768
MAX_FILTERED_BYTES = 512 * 1024 * 1024
MAX_CHUNKS = 4096
MAX_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_CROP_PIXELS = 1_000_000
MAX_CROP_EDGE = 2048
OVERVIEW_EDGE = 960
INPUT_PIECE_BYTES = 65536
PIXEL_PIPELINE_BUDGET = 16 * 1024 * 1024
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
SRGB_CHROMATICITIES = (31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
P3_CHROMATICITIES = (31270, 32900, 68000, 32000, 26500, 69000, 15000, 6000)
# Fixed D65/P3 -> D65/sRGB linear matrix, independently derived from exact xy
# coordinates and checked against W3C CSS Color 4 rational matrices. No inverse
# or chromatic adaptation is computed from source-controlled values.
P3_TO_SRGB = ((3685649/3008840, -676809/3008840, 0.0),
              (-5617931/133579120, 139197051/133579120, 0.0),
              (-1323971/67420360, -1514763/19262960, 148092003/134840720))
CICP_DISPLAY_P3 = bytes((12, 13, 0, 1))
CICP_SRGB = bytes((1, 13, 0, 1))
_LINEAR_ITEM_BYTES = array('d').itemsize
_LINEAR_HEADER_BYTES = sys.getsizeof(array('d'))


class PngPreviewError(ValueError):
    """Invalid/unsupported input must not publish a derived preview."""


@dataclass(frozen=True)
class _Encoding:
    depth: int
    color: int
    palette: bytes | None
    transparency: bytes | None
    interlace: int = 0
    srgb_intent: int | None = None
    gamma: int | None = None
    chromaticities: tuple[int, ...] | None = None
    cicp: bytes | None = None
    icc: png_icc.Profile | None = None

    @property
    def p3(self):
        return (self.cicp == CICP_DISPLAY_P3 if self.cicp is not None
                else self.icc is None and self.chromaticities == P3_CHROMATICITIES)

    @property
    def channels(self):
        return {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[self.color]

    @property
    def output_color(self):
        return 6 if self.color in (4, 6) or self.transparency is not None else 2


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(data, zlib.crc32(kind))))


def _deadline(end: float) -> None:
    if time.monotonic() >= end:
        raise TimeoutError('PNG preview exceeded its bounded processing deadline.')


def _source(data: bytes, end: float) -> tuple[int, int, _Encoding, list[tuple[int, int]]]:
    if not isinstance(data, bytes) or len(data) > MAX_SOURCE_BYTES or not data.startswith(PNG_SIGNATURE):
        raise PngPreviewError('Provide a PNG source no larger than 64 MiB.')
    view = memoryview(data)
    position, count, width, height, color = 8, 0, 0, 0, 0
    idat, seen, after_idat = [], set(), False
    palette = transparency = srgb_intent = gamma = chromaticities = cicp = icc = None
    # Strict subset: sRGB/canonical fallbacks, or explicit gamma plus canonical
    # sRGB or exact D65/P3 primaries, plus bounded ICC v4 matrix/TRC profiles.
    # Other color, orientation and animation extensions are rejected rather
    # than misrepresented, including unknown ancillary chunks.
    inert = {b'tEXt', b'zTXt', b'iTXt', b'pHYs', b'tIME'}
    while position < len(data):
        _deadline(end)
        count += 1
        if count > MAX_CHUNKS or len(data)-position < 12:
            raise PngPreviewError('PNG chunks are truncated or exceed the count limit.')
        length = struct.unpack_from('>I', data, position)[0]
        kind = data[position+4:position+8]
        stop = position + 12 + length
        if (stop > len(data) or any(not (65 <= x <= 90 or 97 <= x <= 122) for x in kind)
                or not 65 <= kind[2] <= 90):
            raise PngPreviewError('PNG chunk type, reserved bit, or length is invalid.')
        body = view[position+8:position+8+length]
        if zlib.crc32(body, zlib.crc32(kind)) != struct.unpack_from('>I', data, stop-4)[0]:
            raise PngPreviewError('PNG chunk checksum failed.')
        if count == 1 and kind != b'IHDR':
            raise PngPreviewError('PNG header must be first.')
        if kind == b'IHDR':
            if count != 1 or length != 13:
                raise PngPreviewError('PNG header is invalid or duplicated.')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            if (not 0 < width <= MAX_EDGE or not 0 < height <= MAX_EDGE
                    or width*height > MAX_PIXELS):
                raise PngPreviewError('PNG dimensions exceed the bounded preview limits.')
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (depth not in depths.get(color, ()) or compression or filtering
                    or interlace not in (0, 1)):
                raise PngPreviewError('Preview supports non-interlaced 1/2/4/8/16-bit grayscale, 1/2/4/8-bit indexed and 8/16-bit RGB, grayscale-alpha or RGBA PNGs, and the same legal sample depths with Adam7 interlacing.')
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            if interlace:
                _adam7_layout(width, height, channels, depth)
            elif ((width*channels*depth+7)//8+1)*height > MAX_FILTERED_BYTES:
                raise PngPreviewError('PNG filtered output exceeds the sequential processing limit.')
        elif kind == b'IDAT':
            if after_idat:
                raise PngPreviewError('PNG image-data chunks must be consecutive.')
            if color == 3 and palette is None:
                raise PngPreviewError('Indexed PNG requires a palette before image data.')
            # cICP has precedence over the admitted lower-priority color chunks.
            # Structural/value policy checks still apply to every such chunk.
            if cicp is None and icc is None:
                if srgb_intent is not None and chromaticities == P3_CHROMATICITIES:
                    raise PngPreviewError('PNG sRGB declaration contradicts D65/P3 chromaticities.')
                if srgb_intent is None and (b'gAMA' in seen or b'cHRM' in seen):
                    if gamma is None or b'cHRM' not in seen:
                        raise PngPreviewError('PNG gamma conversion requires explicit supported chromaticities and gamma.')
                elif srgb_intent is not None and gamma not in (None, 45455):
                    raise PngPreviewError('PNG sRGB gamma fallback must be canonical.')
            idat.append((position+8, length))
        elif kind == b'IEND':
            if length or stop != len(data) or not idat:
                raise PngPreviewError('PNG end marker, trailing data, or image-data sequence is invalid.')
            return width, height, _Encoding(depth, color, palette, transparency, interlace, srgb_intent,
                                                    gamma if cicp is None and icc is None and srgb_intent is None else None, chromaticities, cicp, icc), idat
        elif kind == b'iCCP':
            if idat or b'PLTE' in seen or kind in seen:
                raise PngPreviewError('PNG ICC profile is duplicated or misplaced.')
            try:
                icc = png_icc.parse_iccp(body, lambda: _deadline(end), PIXEL_PIPELINE_BUDGET)
                if (icc.color_model == 'GRAY') != (color in (0, 4)):
                    raise PngPreviewError('PNG ICC color model does not match source color type.')
            except ValueError as exc:
                raise PngPreviewError(str(exc)) from exc
        elif kind == b'cICP':
            if (idat or b'PLTE' in seen or kind in seen or length != 4
                    or body not in (CICP_DISPLAY_P3, CICP_SRGB)):
                raise PngPreviewError('PNG cICP must be unique, before PLTE/IDAT, and exactly full-range Display-P3 (12,13,0,1) or sRGB (1,13,0,1).')
            cicp = bytes(body)
        elif kind == b'sRGB':
            if idat or b'PLTE' in seen or kind in seen or length != 1 or body[0] > 3:
                raise PngPreviewError('PNG sRGB declaration is invalid, duplicated, or misplaced.')
            srgb_intent = body[0]
        elif kind == b'sBIT':
            significant_channels = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}[color]
            sample_depth = 8 if color == 3 else depth
            if (idat or b'PLTE' in seen or kind in seen or length != significant_channels
                    or any(not 1 <= value <= sample_depth for value in body)):
                raise PngPreviewError('PNG significant-bit declaration is invalid, duplicated, or misplaced.')
            # Display the stored IHDR-depth samples, as PNG permits. sBIT is
            # optional original-precision metadata, not a pixel transform. Do
            # not alter tRNS comparison/alpha or forward it to the derivative.
        elif kind in (b'gAMA', b'cHRM'):
            valid = (length == 4 and 0 < int.from_bytes(body, 'big') <= 0x7fffffff
                     if kind == b'gAMA' else length == 32 and
                     struct.unpack('>8I', body) in (SRGB_CHROMATICITIES, P3_CHROMATICITIES))
            if idat or b'PLTE' in seen or kind in seen or not valid:
                raise PngPreviewError('PNG gamma/chromaticity declaration is invalid, duplicated, or misplaced.')
            if kind == b'gAMA':
                gamma = int.from_bytes(body, 'big')
            else:
                chromaticities = struct.unpack('>8I', body)
        elif kind == b'PLTE':
            if (idat or kind in seen or b'tRNS' in seen or color in (0, 4)
                    or not 3 <= length <= 768 or length % 3
                    or color == 3 and length//3 > 1 << depth):
                raise PngPreviewError('PNG palette is invalid or misplaced.')
            palette = bytes(body)
        elif kind == b'tRNS':
            if (idat or kind in seen or color in (4, 6)
                    or color == 0 and length != 2
                    or color == 2 and length != 6
                    or color == 3 and (palette is None or length > len(palette)//3)):
                raise PngPreviewError('PNG transparency is invalid or misplaced.')
            transparency = bytes(body)
        elif kind in inert:
            if kind == b'pHYs' and (idat or kind in seen or length != 9 or body[-1] > 1):
                raise PngPreviewError('PNG physical-dimensions chunk is invalid or misplaced.')
            if kind == b'tIME' and (kind in seen or length != 7):
                raise PngPreviewError('PNG timestamp chunk is invalid or duplicated.')
        else:
            raise PngPreviewError('PNG uses an unsupported color, orientation, animation, or extension chunk.')
        if idat and kind != b'IDAT':
            after_idat = True
        seen.add(kind)
        position = stop
    raise PngPreviewError('PNG end marker is missing.')


def _adam7_layout(width: int, height: int, channels: int, depth: int = 8) -> tuple[tuple, ...]:
    """Bound packed or whole-byte passes before inflation; omit empty passes."""
    passes = []
    total = 0
    for x, y, dx, dy in ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
                         (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)):
        columns = max(0, (width-x+dx-1)//dx)
        rows = max(0, (height-y+dy-1)//dy)
        if not columns or not rows:
            continue
        stride = (columns*channels*depth+7)//8
        total += (stride+1)*rows
        if total > MAX_FILTERED_BYTES:
            raise PngPreviewError('PNG filtered output exceeds the sequential processing limit.')
        passes.append((x, y, dx, dy, columns, rows, stride))
    return tuple(passes)


def _unfilter_adam7(kind: int, filtered: bytes, previous: bytes,
                        byte_distance: int, end: float) -> bytes:
    """Reconstruct pass bytes: one for packed rows, 2*channels for 16-bit."""
    _deadline(end)
    if not 0 <= kind <= 4:
        raise PngPreviewError('PNG scanline filter is invalid.')
    if kind == 0:
        return filtered
    row = bytearray(filtered)
    for index in range(len(row)):
        if index % 4096 == 0:
            _deadline(end)
        left = row[index-byte_distance] if index >= byte_distance else 0
        up = previous[index]
        corner = previous[index-byte_distance] if index >= byte_distance else 0
        if kind == 1:
            predictor = left
        elif kind == 2:
            predictor = up
        elif kind == 3:
            predictor = (left+up)//2
        else:
            estimate = left+up-corner
            a, b, c = abs(estimate-left), abs(estimate-up), abs(estimate-corner)
            predictor = left if a <= b and a <= c else up if b <= c else corner
        row[index] = (row[index]+predictor) & 255
    _deadline(end)
    return bytes(row)


def _decode_adam7(data, ranges, width, height, encoding, rectangle,
                       out_width, out_height, output, xs, ys, end, color_table=None):
    """Validate the entire stream, retaining only pass rows and output pixels."""
    channels = encoding.channels
    passes = _adam7_layout(width, height, channels, encoding.depth)
    expected = sum((item[6]+1)*item[5] for item in passes)
    bpp = 4 if encoding.output_color == 6 else 3
    transparent = (bytes(value & 255 for value in struct.unpack('>HHH', encoding.transparency))
                   if color_table is None and encoding.depth == 8 and encoding.color == 2 and encoding.transparency is not None else None)
    pending = bytearray()
    decoder = zlib.decompressobj()
    pass_index = pass_row = processed = selected_count = target_y = 0
    previous = bytes(passes[0][6])
    selected_x = []

    def select_columns(item):
        start, _, step, _, _, _, _ = item
        coordinates = xs if xs is not None else range(rectangle['x'], rectangle['x']+out_width)
        return [((source_x-start)//step, output_x)
                for output_x, source_x in enumerate(coordinates)
                if source_x >= start and (source_x-start) % step == 0]

    selected_x = select_columns(passes[0])

    def consume(plain):
        nonlocal pass_index, pass_row, processed, selected_count, target_y, previous, selected_x
        _deadline(end)
        processed += len(plain)
        if processed > expected:
            raise PngPreviewError('PNG decompressed data exceeds the exact image length.')
        pending.extend(plain)
        if pass_index == len(passes):
            return
        _, start_y, _, step_y, columns, rows, stride = passes[pass_index]
        if len(pending) != stride+1:
            return
        previous = _unfilter_adam7(pending[0], bytes(pending[1:]), previous, max(1, (channels*encoding.depth+7)//8), end)
        pending.clear()
        # Retain packed padding in previous for filtering, but validate only
        # actual samples; validate palette indices on EVERY row before selection.
        samples = (_samples(previous, columns, encoding.depth)
                   if encoding.color in (0, 3) and encoding.depth < 16 else None)
        if encoding.color == 3 and max(samples) >= len(encoding.palette)//3:
            raise PngPreviewError('PNG pixel index exceeds the supplied palette.')
        _deadline(end)
        source_y = start_y+pass_row*step_y
        output_y = None
        if ys is not None:
            while target_y < out_height and ys[target_y] < source_y:
                target_y += 1
            if target_y < out_height and ys[target_y] == source_y:
                output_y = target_y
        elif rectangle['y'] <= source_y < rectangle['y']+out_height:
            output_y = source_y-rectangle['y']
        if output_y is not None:
            expanded = color_table is not None or encoding.depth == 16 or encoding.color in (0, 3, 4)
            pixels = _expand_color(previous, samples, columns, encoding, color_table, end) if expanded else previous
            pixel_stride = bpp if expanded else channels
            _deadline(end)
            for column, output_x in selected_x:
                if output_x % 256 == 0:
                    _deadline(end)
                pixel = pixels[column*pixel_stride:(column+1)*pixel_stride]
                start = (output_y*out_width+output_x)*bpp
                # Alpha is unassociated; preserve hidden gray/palette/RGB values.
                output[start:start+pixel_stride] = pixel
                if transparent is not None:
                    output[start+3] = 0 if pixel == transparent else 255
                selected_count += 1
        pass_row += 1
        if pass_row == rows:
            pass_index += 1
            pass_row = target_y = 0
            if pass_index < len(passes):
                previous = bytes(passes[pass_index][6])
                selected_x = select_columns(passes[pass_index])

    try:
        for start, length in ranges:
            for offset in range(start, start+length, INPUT_PIECE_BYTES):
                compressed = memoryview(data)[offset:min(offset+INPUT_PIECE_BYTES, start+length)]
                while True:
                    _deadline(end)
                    limit = (passes[pass_index][6]+1-len(pending)) if pass_index < len(passes) else 1
                    plain = decoder.decompress(compressed, limit)
                    compressed = decoder.unconsumed_tail
                    consume(plain)
                    if decoder.unused_data:
                        raise PngPreviewError('PNG contains trailing or concatenated compressed data.')
                    if not compressed and not plain or decoder.eof:
                        break
    except zlib.error as exc:
        raise PngPreviewError('PNG image compression or checksum is invalid.') from exc
    if (not decoder.eof or pass_index != len(passes) or pending or processed != expected
            or selected_count != out_width*out_height):
        raise PngPreviewError('PNG compressed stream or exact image length is incomplete.')
    _deadline(end)
    return processed


def _unfilter(kind: int, filtered: bytes, previous: bytes, width: int, color: int) -> bytes:
    if not 0 <= kind <= 4:
        raise PngPreviewError('PNG scanline filter is invalid.')
    if kind == 0:
        return filtered
    # Documented Image.open API with exactly two rows. The first row recreates
    # the prior decoded row; the second retains the source filter. level=0 uses
    # stored deflate blocks; this is not a full-source image or hidden crop.
    raw = b'\0' + previous + bytes([kind]) + filtered
    carrier = (PNG_SIGNATURE
               + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, 2, 8, color, 0, 0, 0))
               + _chunk(b'IDAT', zlib.compress(raw, 0)) + _chunk(b'IEND', b''))
    with Image.open(io.BytesIO(carrier)) as image:
        pixels = image.tobytes()
    return pixels[len(previous):]


def _samples(row: bytes, width: int, depth: int) -> bytes:
    """Unpack source samples only, retaining packed bytes for the next filter."""
    if depth == 8:
        return row
    per_byte, mask = 8//depth, (1 << depth)-1
    result = bytearray(width)
    for slot in range(per_byte):
        # The last byte's unused low bits are not pixels (PNG 7.2).
        count = (width+per_byte-1-slot)//per_byte
        table = bytes((value >> (8-depth*(slot+1))) & mask for value in range(256))
        result[slot::per_byte] = row[:count].translate(table)
    return bytes(result)


def _unfilter_source(kind: int, filtered: bytes, previous: bytes, width: int,
                     encoding: _Encoding, end: float) -> bytes:
    if encoding.depth == 16 and kind != 0:
        # PNG filters predict individual bytes, with a left distance of
        # 2*channels. Even/odd byte lanes therefore have independent predictors
        # with distance channels. Two 8-bit carriers recover every high AND low
        # byte without Pillow's 16-bit multichannel display conversion. Keep
        # the interleaved full-precision row for the next row's predictors.
        high = _unfilter(kind, filtered[::2], previous[::2], width, encoding.color)
        _deadline(end)
        low = _unfilter(kind, filtered[1::2], previous[1::2], width, encoding.color)
        _deadline(end)
        decoded = bytearray(len(filtered))
        decoded[::2], decoded[1::2] = high, low
        return bytes(decoded)
    # Packed gray/index rows use a one-byte predictor. Treat packed bytes as
    # grayscale8, never as decoded palette entries. Unfiltered 16-bit rows
    # return directly without any carrier or precision reduction.
    carrier_width = len(filtered) if encoding.color in (0, 3) else width
    carrier_color = 0 if encoding.color in (0, 3) else encoding.color
    return _unfilter(kind, filtered, previous, carrier_width, carrier_color)


def _expand_16(row: bytes, width: int, encoding: _Encoding) -> bytes:
    """Rounded linear 16-to-8 scaling, after full-precision transparency."""
    color, transparency = encoding.color, encoding.transparency
    alpha = None
    if transparency is not None:
        # Compare exact two-/six-byte samples before reducing their precision.
        # Different 16-bit colors can produce the same 8-bit output color.
        source_bpp = encoding.channels*2
        alpha = bytes(0 if row[x:x+source_bpp] == transparency else 255
                      for x in range(0, len(row), source_bpp))
    # W3C PNG 13.12: round(sample*255/65535), exactly (sample+128)//257.
    # Alpha remains unassociated: preserve hidden RGB even when alpha is zero.
    reduced = bytes((sample+128)//257 for (sample,) in struct.iter_unpack('>H', row))
    if color == 6 or color == 2 and transparency is None:
        return reduced
    bpp = 4 if encoding.output_color == 6 else 3
    pixels = bytearray(width*bpp)
    if color in (0, 4):
        gray = reduced if color == 0 else reduced[::2]
        for channel in range(3):
            pixels[channel::bpp] = gray
        if color == 4:
            pixels[3::4] = reduced[1::2]
    else:
        for channel in range(3):
            pixels[channel::4] = reduced[channel::3]
    if alpha is not None:
        pixels[3::4] = alpha
    return bytes(pixels)


def _expand(row: bytes, samples: bytes | None, width: int, encoding: _Encoding) -> bytes:
    """Expand at most one selected row to unassociated 8-bit RGB/RGBA."""
    if encoding.depth == 16:
        return _expand_16(row, width, encoding)
    color, transparency = encoding.color, encoding.transparency
    if color == 6 or color == 2 and transparency is None:
        return row
    bpp = 4 if encoding.output_color == 6 else 3
    pixels = bytearray(width*bpp)
    if color in (0, 4):
        gray = (samples.translate(bytes(min(255, value*255//((1 << encoding.depth)-1))
                                        for value in range(256))) if color == 0 else row[::2])
        for channel in range(3):
            pixels[channel::bpp] = gray
        if color == 4:
            pixels[3::4] = row[1::2]
        elif transparency is not None:
            # PNG third edition requires masking unused high sample bits.
            transparent = int.from_bytes(transparency, 'big') & ((1 << encoding.depth)-1)
            pixels[3::4] = samples.translate(bytes(0 if value == transparent else 255 for value in range(256)))
    elif color == 3:
        palette = encoding.palette
        for channel in range(3):
            table = palette[channel::3].ljust(256, b'\0')
            pixels[channel::bpp] = samples.translate(table)
        if transparency is not None:
            pixels[3::4] = samples.translate(transparency.ljust(256, b'\xff'))
    else:  # RGB with an exact transparent sample, never premultiply its colors.
        for channel in range(3):
            pixels[channel::4] = row[channel::3]
        transparent = bytes(value & 255 for value in struct.unpack('>HHH', transparency))
        pixels[3::4] = bytes(0 if row[x:x+3] == transparent else 255 for x in range(0, len(row), 3))
    return bytes(pixels)


def _gamma_table(depth: int, gamma: int, end: float) -> bytearray:
    """Fixed-size original-sample LUT; no alpha correction or profile parsing."""
    maximum = (1 << depth)-1
    table = bytearray(maximum+1)
    exponent = 100000 / gamma
    for sample in range(maximum+1):
        if sample % 256 == 0:
            _deadline(end)
        linear = (sample / maximum) ** exponent
        encoded = (12.92*linear if linear <= 0.0031308 else
                   1.055*linear**(1/2.4)-0.055)
        table[sample] = min(255, max(0, math.floor(encoded*255+0.5)))
    _deadline(end)
    return table


def _linear_table(depth: int, gamma: int | None, end: float) -> array:
    """Original-depth linear light: explicit power, or cICP sRGB when gamma is None."""
    maximum = (1 << depth)-1
    table = array('d', [0.0]) * (maximum+1)
    exponent = 100000 / gamma if gamma is not None else None
    for sample in range(maximum+1):
        if sample % 256 == 0:
            _deadline(end)
        value = sample / maximum
        table[sample] = (value ** exponent if exponent is not None else
                         value / 12.92 if value <= 0.04045 else
                         ((value + 0.055) / 1.055) ** 2.4)
    _deadline(end)
    return table


def _p3_to_srgb8(values, table):
    linear = tuple(table[value] for value in values)
    # Exact matrix row sums are one. The neutral-axis identity also avoids
    # floating cancellation for gray sources and RGB/palette neutral samples.
    transformed = (linear if linear[0] == linear[1] == linear[2] else
                   tuple(sum(a*b for a, b in zip(row, linear)) for row in P3_TO_SRGB))
    result = []
    for value in transformed:
        light = min(1.0, max(0.0, value))  # Explicit channel clipping, not perceptual gamut mapping.
        encoded = 12.92*light if light <= 0.0031308 else 1.055*light**(1/2.4)-0.055
        result.append(min(255, max(0, math.floor(encoded*255+0.5))))
    return result


def _expand_color(row, samples, width, encoding, table, end):
    # Existing expansion performs exact original-sample tRNS comparison and
    # alpha reduction first. Replace only RGB using ORIGINAL precision below.
    pixels = _expand(row, samples, width, encoding)
    if table is None:
        return pixels
    result = bytearray(pixels)
    bpp, color = (4 if encoding.output_color == 6 else 3), encoding.color
    channels = encoding.channels
    for x in range(width):
        if x % 256 == 0:
            _deadline(end)
        if color == 3:
            values = encoding.palette[samples[x]*3:samples[x]*3+3]
        elif encoding.depth == 16:
            offset = x*channels*2
            values = tuple(int.from_bytes(row[offset+c*2:offset+c*2+2], 'big')
                           for c in range(1 if color in (0, 4) else 3))
        elif color == 0:
            values = (samples[x],)
        else:
            offset = x*channels
            values = row[offset:offset+(1 if color == 4 else 3)]
        if color in (0, 4) and not (encoding.icc is not None and encoding.cicp is None):
            values = (values[0],)*3
        if encoding.icc is not None and encoding.cicp is None:
            for c, value in enumerate(png_icc.convert(values, encoding.icc, table)):
                result[x*bpp+c] = value
        elif encoding.p3:
            for c, value in enumerate(_p3_to_srgb8(values, table)):
                result[x*bpp+c] = value
        else:
            for c, value in enumerate(values):
                result[x*bpp+c] = table[value]
    _deadline(end)
    return result


def preview_capability(data: bytes) -> dict:
    """Check bounded source/chunk/encoding support without decoding any pixels.

    Availability is not proof of a valid compressed stream; ``preview_png``
    performs that check before returning anything visible.
    """
    width = height = None
    if (isinstance(data, bytes) and len(data) >= 33 and data.startswith(PNG_SIGNATURE)
            and data[8:16] == b'\0\0\0\rIHDR'):
        width, height = struct.unpack_from('>II', data, 16)
    try:
        width, height, _color, _ranges = _source(data, time.monotonic()+45)
    except (PngPreviewError, TimeoutError) as exc:
        return {'available': False, 'reason': str(exc), 'width': width, 'height': height}
    return {'available': True, 'reason': None, 'width': width, 'height': height}


def _geometry(width: int, height: int, mode: str, rect: dict | None) -> tuple[dict, int, int]:
    if mode == 'overview':
        if rect is not None:
            raise PngPreviewError('Overview requests cannot supply a crop rectangle.')
        denominator = max(OVERVIEW_EDGE, width, height)
        return ({'x': 0, 'y': 0, 'width': width, 'height': height},
                max(1, width*OVERVIEW_EDGE//denominator), max(1, height*OVERVIEW_EDGE//denominator))
    if mode != 'crop' or not isinstance(rect, dict) or set(rect) != {'x', 'y', 'width', 'height'}:
        raise PngPreviewError('Choose overview or an explicit integer-pixel crop rectangle.')
    if any(type(value) is not int for value in rect.values()):
        raise PngPreviewError('Crop coordinates and dimensions must be integers.')
    x, y, w, h = (rect[key] for key in ('x', 'y', 'width', 'height'))
    if (x < 0 or y < 0 or w < 1 or h < 1 or x+w > width or y+h > height
            or w > MAX_CROP_EDGE or h > MAX_CROP_EDGE or w*h > MAX_CROP_PIXELS):
        raise PngPreviewError('Crop is outside the source or exceeds 2048 pixels per edge/one million pixels.')
    return dict(rect), w, h


def _encode(pixels: bytearray, width: int, height: int, color: int, end: float, srgb_intent: int | None = None) -> bytes:
    output = bytearray(PNG_SIGNATURE + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0)))
    if srgb_intent is not None:
        output.extend(_chunk(b'sRGB', bytes([srgb_intent])))
    encoder = zlib.compressobj()
    stride = width*(3 if color == 2 else 4)

    def append(payload: bytes) -> None:
        if payload:
            if len(output)+len(payload)+24 > MAX_PREVIEW_BYTES:
                raise PngPreviewError('Derived PNG exceeds the 4 MiB output limit.')
            output.extend(_chunk(b'IDAT', payload))

    for offset in range(0, len(pixels), stride):
        _deadline(end)
        append(encoder.compress(b'\0' + pixels[offset:offset+stride]))
    append(encoder.flush())
    output.extend(_chunk(b'IEND', b''))
    return bytes(output)


def preview_png(data: bytes, *, mode: str, rect: dict | None = None,
                deadline_seconds: float = 45, expected_source_sha256: str | None = None) -> dict:
    """Return a validated navigation overview or exact, unscaled source crop.

    ``data`` must be a native-owned immutable snapshot. An isolated supervisor
    must enforce cancellation, one active worker, wall time and output limits;
    this cooperative deadline does not replace that process boundary.
    """
    if (isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 45):
        raise PngPreviewError('PNG processing deadline must be positive and no greater than 45 seconds.')
    end = time.monotonic() + deadline_seconds
    width, height, encoding, ranges = _source(data, end)
    source_hash = hashlib.sha256(data).hexdigest()
    if expected_source_sha256 is not None and expected_source_sha256 != source_hash:
        raise PngPreviewError('PNG source snapshot identity changed.')
    rectangle, out_width, out_height = _geometry(width, height, mode, rect)
    color = encoding.output_color
    bpp = 3 if color == 2 else 4
    stride = (width*encoding.channels*encoding.depth+7)//8
    output_bytes = out_width*out_height*bpp
    # Conservative explicit buffer accounting, not a process-RAM guarantee:
    # output pixels plus <=32 row-sized simultaneous carrier/codec/copy buffers,
    # and 256 KiB input/zlib working allowance. The 16-bit path processes one
    # half-row carrier at a time; lanes, interleaving and scaling stay inside
    # this same conservative allowance. Source/encoded/transport separate.
    accounted = output_bytes + 32*(max(stride, width*bpp)+1) + 262144
    if encoding.interlace:
        # Output-sized coordinate lists and the current pass selection only.
        accounted += 128*(out_width+out_height)
    icc_active = encoding.icc is not None and encoding.cicp is None
    if encoding.icc is not None:
        accounted += png_icc.PROFILE_WORK_BYTES
    converting = icc_active or encoding.gamma is not None or encoding.cicp == CICP_DISPLAY_P3
    table_bytes = (1 << (8 if encoding.color == 3 else encoding.depth)) if converting else 0
    p3 = encoding.p3
    if p3:
        table_bytes = table_bytes*_LINEAR_ITEM_BYTES + _LINEAR_HEADER_BYTES
    # P3 uses exactly allocated array doubles, including the measured header;
    # the old gamma-only bytearray accounting stays unchanged.
    # The gamma-only LUT fills a bytearray directly, without a Python-int list.
    # Existing 32-row allowance covers expansion and its RGB replacement copy.
    if icc_active:
        table_bytes = png_icc.table_bytes(8 if encoding.color == 3 else encoding.depth, encoding.icc)
    accounted += table_bytes
    if p3 or icc_active:
        # Multiplication briefly retains the one-double seed array too.
        accounted += _LINEAR_HEADER_BYTES + _LINEAR_ITEM_BYTES
    if accounted > PIXEL_PIPELINE_BUDGET:
        raise PngPreviewError('PNG pixel pipeline exceeds its buffer budget.')
    output = bytearray(output_bytes)
    color_table = (png_icc.tables(encoding.icc, 8 if encoding.color == 3 else encoding.depth, lambda: _deadline(end))
                   if icc_active else (_linear_table if p3 else _gamma_table)(8 if encoding.color == 3 else encoding.depth, encoding.gamma, end)
                   if converting else None)
    xs = [(2*x+1)*width//(2*out_width) for x in range(out_width)] if mode == 'overview' else None
    ys = [(2*y+1)*height//(2*out_height) for y in range(out_height)] if mode == 'overview' else None
    if encoding.interlace:
        processed = _decode_adam7(data, ranges, width, height, encoding, rectangle,
                                        out_width, out_height, output, xs, ys, end, color_table)
    else:
        previous, pending = bytes(stride), bytearray()
        decoder = zlib.decompressobj()
        row, target_row, processed = 0, 0, 0

        def consume(plain: bytes) -> None:
            nonlocal previous, row, target_row, processed
            processed += len(plain)
            if row >= height and plain:
                raise PngPreviewError('PNG decompressed data exceeds the exact image length.')
            pending.extend(plain)
            if len(pending) == stride+1:
                _deadline(end)
                previous = _unfilter_source(pending[0], bytes(pending[1:]), previous, width, encoding, end)
                pending.clear()
                samples = (_samples(previous, width, encoding.depth)
                           if encoding.color in (0, 3) and encoding.depth < 16 else None)
                if encoding.color == 3 and max(samples) >= len(encoding.palette)//3:
                    raise PngPreviewError('PNG pixel index exceeds the supplied palette.')
                if mode == 'overview' and target_row < out_height and row == ys[target_row]:
                    pixels = _expand_color(previous, samples, width, encoding, color_table, end)
                    selected = b''.join(pixels[x*bpp:(x+1)*bpp] for x in xs)
                    start = target_row*out_width*bpp
                    output[start:start+len(selected)] = selected
                    target_row += 1
                elif mode == 'crop' and rectangle['y'] <= row < rectangle['y']+out_height:
                    pixels = _expand_color(previous, samples, width, encoding, color_table, end)
                    start = (row-rectangle['y'])*out_width*bpp
                    left = rectangle['x']*bpp
                    output[start:start+out_width*bpp] = pixels[left:left+out_width*bpp]
                    target_row += 1
                row += 1

        try:
            for start, length in ranges:
                for offset in range(start, start+length, INPUT_PIECE_BYTES):
                    _deadline(end)
                    compressed = memoryview(data)[offset:min(offset+INPUT_PIECE_BYTES, start+length)]
                    while True:
                        plain = decoder.decompress(compressed, (stride+1-len(pending)) if row < height else 1)
                        compressed = decoder.unconsumed_tail
                        consume(plain)
                        if decoder.unused_data:
                            raise PngPreviewError('PNG contains trailing or concatenated compressed data.')
                        if not compressed and not plain:
                            break
                        if decoder.eof:
                            break
        except zlib.error as exc:
            raise PngPreviewError('PNG image compression or checksum is invalid.') from exc
        if not decoder.eof or row != height or pending or target_row != out_height:
            raise PngPreviewError('PNG compressed stream or exact image length is incomplete.')
    _deadline(end)
    png = _encode(output, out_width, out_height, color, end, 1 if converting or encoding.cicp == CICP_SRGB else encoding.srgb_intent)
    result = {
        'schemaVersion': 1, 'sourceSha256': source_hash, 'sourceWidth': width, 'sourceHeight': height,
        'mode': mode, 'rect': rectangle, 'width': out_width, 'height': out_height,
        'navigationOnly': mode == 'overview', 'resampling': 'nearest-center' if mode == 'overview' else 'none',
        'pngBase64': base64.b64encode(png).decode('ascii'), 'pngSha256': hashlib.sha256(png).hexdigest(),
        'engine': {'name': 'nivraM strict sequential PNG', 'version': (5 if encoding.depth == 16 else 4) if encoding.interlace else 3,
                   'rowDecoder': (f'{ {0: 'grayscale', 2: 'RGB', 4: 'grayscale-alpha', 6: 'RGBA'}[encoding.color]}16 byte filters' if encoding.depth == 16 else
                                  f'grayscale{encoding.depth} packed byte filters' if encoding.color == 0 else
                                  'grayscale-alpha8 byte filters' if encoding.color == 4 else
                                  f'indexed{encoding.depth} packed byte filters' if encoding.color == 3 else
                                  'RGBA8 byte filters' if encoding.color == 6 else 'RGB8 byte filters') if encoding.interlace else 'Pillow',
                   'rowDecoderVersion': 1 if encoding.interlace else PILLOW_VERSION, 'zlibVersion': zlib.ZLIB_RUNTIME_VERSION},
        'bufferAccounting': {'sourceSnapshotBytes': len(data), 'filteredBytesProcessed': processed,
                       'sourceRowBytes': stride, 'maxCarrierHeight': 0 if encoding.interlace else 2, 'outputPixelBytes': len(output),
                       'pixelPipelineAccountedUpperBoundBytes': accounted,
                       'pixelPipelineBudgetBytes': PIXEL_PIPELINE_BUDGET, 'encodedPngBytes': len(png),
                       'excludes': 'Interpreter/native baseline, immutable source, encoded PNG and JSON/base64/bridge/browser copies.'},
    }
    if encoding.cicp == CICP_SRGB:
        # The source declares sRGB encoding, not rendering intent. Intent 1 is
        # our derivative policy; no transfer/gamut conversion or LUT is needed.
        result['engine']['colorInterpretation'] = {
            'name': 'cICP sRGB', 'version': 1, 'sourceCICP': list(encoding.cicp),
            'sourceColorDepth': 8 if encoding.color == 3 else encoding.depth,
            'outputIntentPolicy': 1,
            'samplePolicy': 'stored-samples; scale-to-8bit; no-transfer-or-gamut-conversion'}
    if converting:
        result['engine']['colorConversion'] = {
            'name': 'canonical-cHRM gamma to sRGB', 'version': 1,
            'sourceGammaInteger': encoding.gamma, 'outputIntentPolicy': 1,
            'sourceColorDepth': 8 if encoding.color == 3 else encoding.depth}
        if p3:
            result['engine']['colorConversion'] = {
                'name': 'explicit-gamma D65/P3-primary to sRGB', 'version': 1,
                'sourceChromaticities': list(P3_CHROMATICITIES),
                'sourceGammaInteger': encoding.gamma,
                'sourceColorDepth': 8 if encoding.color == 3 else encoding.depth,
                'outputIntentPolicy': 1,
                'gamutPolicy': 'clip-linear-sRGB-channels-to-[0,1]',
                'transferPolicy': 'declared-power-to-linear; sRGB-encode; round-once-8bit'}
        if encoding.cicp is not None:
            result['engine']['colorConversion'] = {
                'name': 'cICP Display-P3 to sRGB', 'version': 1,
                'sourceCICP': list(encoding.cicp),
                'sourceColorDepth': 8 if encoding.color == 3 else encoding.depth,
                'outputIntentPolicy': 1,
                'gamutPolicy': 'clip-linear-sRGB-channels-to-[0,1]',
                'transferPolicy': 'sRGB-decode; D65/P3-to-sRGB; sRGB-encode; round-once-8bit'}
        result['bufferAccounting']['colorTableBytes'] = table_bytes
        if p3 or icc_active:
            result['bufferAccounting']['colorTableConstructionTemporaryBytes'] = _LINEAR_HEADER_BYTES + _LINEAR_ITEM_BYTES
    if encoding.icc is not None:
        result['bufferAccounting']['iccProfileWorkUpperBoundBytes'] = png_icc.PROFILE_WORK_BYTES
        result['bufferAccounting']['iccInflatedProfileBytes'] = encoding.icc.size
        result['engine']['iccProfile'] = {
            'sha256': encoding.icc.sha256, 'sourceIntent': encoding.icc.intent,
            'selectedMetadata': 'cICP' if encoding.cicp is not None else 'iCCP',
            'admissionPolicy': 'bounded-v4-display-RGB-XYZ-matrix-TRC'}
        if encoding.icc.color_model == 'GRAY':
            result['engine']['iccProfile'].update({
                'admissionPolicy': 'bounded-v4-display-GRAY-XYZ-kTRC',
                'sourceColorModel': 'GRAY', 'sizeBytes': encoding.icc.size})
    if icc_active:
        result['engine']['colorConversion'] = {
            'name': 'bounded ICC v4 matrix/TRC to sRGB', 'version': 1,
            'profileSha256': encoding.icc.sha256, 'sourceIntent': encoding.icc.intent,
            'selectedMetadata': 'iCCP', 'outputIntentPolicy': 1, 'blackPointCompensation': False,
            'sourceColorDepth': 8 if encoding.color == 3 else encoding.depth,
            'gamutPolicy': 'clip-linear-sRGB-channels-to-[0,1]',
            'transferPolicy': 'original-sample-TRC; PCS-D50-to-D65-sRGB; round-once-8bit',
            'numericPolicy': 'bounded-fixed-coefficients; float64; no-physical-calibration'}
        if encoding.icc.color_model == 'GRAY':
            result['engine']['colorConversion'].update({
                'name': 'bounded ICC v4 GRAY kTRC to sRGB',
                'sourceColorModel': 'GRAY',
                'transferPolicy': 'original-sample-kTRC; multiply-PCS-D50-white; PCS-D50-to-D65-sRGB; round-once-8bit'})
    _deadline(end)
    return result
