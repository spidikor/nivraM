"""Bounded ICC v4 display RGB matrix/TRC and GRAY kTRC, not a general CMS.

Untrusted profiles never enter Pillow/ImageCms. PCS columns are already D50
adapted; chad is validated, not applied twice. See docs/png-encodings.md.
"""
from array import array
from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
import struct
import sys
import zlib

MAX_COMPRESSED = 256 * 1024
MAX_PROFILE = 1024 * 1024
MAX_TAGS = 64
MAX_CURVE = 4096
# Conservative simultaneous inflation copies, input/tails, parsed curve objects,
# tag objects and bounded parsing scratch. Separate from immutable PNG snapshot.
PROFILE_WORK_BYTES = 8 * MAX_PROFILE + 2 * MAX_COMPRESSED + 262144
D50 = (63190 / 65536, 1.0, 54061 / 65536)
# Published CSS Color 4 Bradford D50->D65 and XYZ D65->linear sRGB.
_ADAPT = ((.955473421488075, -.02309845494876471, .06325924320057072),
          (-.0283697093338637, 1.0099953980813041, .021041441191917323),
          (.012314014864481998, -.020507649298898964, 1.330365926242124))
_RGB = ((12831/3959, -329/214, -1974/3959),
        (-851781/878810, 1648619/878810, 36519/878810),
        (705/12673, -2585/12673, 705/667))
PCS_TO_SRGB = tuple(tuple(sum(_RGB[i][k]*_ADAPT[k][j] for k in range(3))
                           for j in range(3)) for i in range(3))


def _require(ok, message):
    if not ok:
        raise ValueError('PNG ICC profile: ' + message)


def _fixed(body, offset, count):
    return tuple(v / 65536 for v in struct.unpack_from('>' + 'i'*count, body, offset))


def _det(a):
    return (a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])
            -a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])
            +a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0]))


@dataclass(frozen=True)
class Curve:
    kind: str
    values: tuple

    def evaluate(self, x):
        if self.kind == 'identity':
            return x
        if self.kind == 'gamma':
            return x ** self.values[0]
        if self.kind == 'table':
            position = x * (len(self.values)-1)
            index = min(len(self.values)-2, int(position))
            return (self.values[index] + (self.values[index+1]-self.values[index]) *
                    (position-index)) / 65535
        g, a, b, c, d = self.values
        return min(1., max(0., (a*x+b)**g if x >= d else c*x))


def _curve(body):
    if body[:4] == b'curv':
        _require(len(body) >= 12, 'truncated curve.')
        n = int.from_bytes(body[8:12], 'big')
        _require(n <= MAX_CURVE and len(body) == 12 + 2*n, 'curve size exceeds supported bounds.')
        if n == 0:
            return Curve('identity', ())
        values = struct.unpack_from('>'+'H'*n, body, 12)
        if n == 1:
            _require(0 < values[0] <= 2560, 'unsupported curve gamma.')
            return Curve('gamma', (values[0]/256,))
        _require(values[0] == 0 and values[-1] == 65535 and
                 all(a <= b for a,b in zip(values, values[1:])), 'curve table must be monotone with full endpoints.')
        return Curve('table', values)
    _require(body[:4] == b'para' and len(body) >= 12, 'unsupported transfer curve type.')
    kind, reserved = struct.unpack_from('>HH', body, 8)
    _require(kind in (0, 3) and reserved == 0 and len(body) == (16 if kind == 0 else 32),
             'unsupported parametric curve or size.')
    values = _fixed(body, 12, 1 if kind == 0 else 5)
    g = values[0]
    _require(0 < g <= 10, 'unsupported parametric gamma.')
    if kind == 0:
        return Curve('gamma', values)
    g, a, b, c, d = values
    _require(0 < a <= 16 and abs(b) <= 16 and 0 <= c <= 16 and 0 <= d <= 1,
             'parametric coefficients exceed supported bounds.')
    _require(a*d+b >= 0, 'parametric power domain is invalid.')
    low, high = c*d, (a*d+b)**g
    _require(-2/65536 <= high-low <= .002, 'parametric curve join is discontinuous.')
    black = b**g if d == 0 else 0.
    white = (a+b)**g
    _require(abs(black) <= .002 and abs(white-1) <= .002, 'parametric endpoints are unsupported.')
    return Curve('parametric3', values)


def _xyz(body):
    _require(len(body) == 20 and body[:4] == b'XYZ ', 'XYZ tags must contain exactly one triple.')
    return _fixed(body, 8, 3)


def _mluc(body, check):
    _require(len(body) >= 16 and body[:4] == b'mluc', 'only bounded mluc text tags are supported.')
    count, size = struct.unpack_from('>II', body, 8)
    _require(1 <= count <= 64 and size == 12 and 16+count*12 <= len(body), 'invalid multilingual record table.')
    for i in range(count):
        check()
        language, country, length, offset = struct.unpack_from('>2s2sII', body, 16+i*12)
        _require(all(97 <= c <= 122 for c in language) and all(65 <= c <= 90 for c in country),
                 'invalid multilingual locale.')
        _require(length % 2 == 0 and offset >= 16+count*12 and offset+length <= len(body), 'invalid multilingual string span.')
        try:
            body[offset:offset+length].decode('utf-16-be', errors='strict')
        except UnicodeDecodeError as exc:
            raise ValueError('PNG ICC profile: invalid UTF-16 text.') from exc


@dataclass(frozen=True)
class Profile:
    sha256: str
    size: int
    intent: int
    matrix: tuple
    curves: tuple
    color_model: str = 'RGB'


def parse_iccp(body, check, budget):
    """Bound inflation before allocation, then validate every admitted tag."""
    _require(budget >= PROFILE_WORK_BYTES, 'profile processing exceeds pixel buffer budget.')
    _require(3 <= len(body) <= MAX_COMPRESSED+81, 'compressed envelope exceeds supported bounds.')
    # Scan only the bounded keyword, not an attacker-sized copy/search.
    name_end = next((i for i in range(min(80, len(body))) if body[i] == 0), -1)
    _require(1 <= name_end <= 79 and name_end+2 < len(body), 'invalid profile keyword or empty compressed stream.')
    name = bytes(body[:name_end])
    _require(all(32 <= x <= 126 or 161 <= x <= 255 for x in name) and
             not name.startswith(b' ') and not name.endswith(b' ') and b'  ' not in name,
             'invalid profile keyword.')
    _require(body[name_end+1] == 0, 'unsupported compression method.')
    compressed = body[name_end+2:]
    _require(len(compressed) <= MAX_COMPRESSED, 'compressed profile exceeds 256 KiB.')
    decoder = zlib.decompressobj()
    raw = bytearray()
    try:
        for offset in range(0, len(compressed), 65536):
            piece = compressed[offset:offset+65536]
            while True:
                check()
                plain = decoder.decompress(piece, min(65536, MAX_PROFILE-len(raw)+1))
                _require(len(raw)+len(plain) <= MAX_PROFILE, 'inflated profile exceeds 1 MiB.')
                raw.extend(plain)
                piece = decoder.unconsumed_tail
                _require(not decoder.unused_data, 'trailing or concatenated profile stream.')
                if decoder.eof or not piece and not plain:
                    break
        _require(decoder.eof, 'incomplete compressed profile stream.')
    except zlib.error as exc:
        raise ValueError('PNG ICC profile: invalid compressed profile/checksum.') from exc
    check()
    return _parse(bytes(raw), check)


def _parse(raw, check):
    _require(len(raw) >= 132 and len(raw) % 4 == 0 and int.from_bytes(raw[:4], 'big') == len(raw), 'invalid declared profile size.')
    _require(raw[8] == 4 and raw[9] >> 4 <= 4 and raw[9] & 15 <= 9 and raw[10:12] == b'\0\0', 'only ICC v4.0 through v4.4 profiles are supported.')
    _require(raw[12:16] == b'mntr' and raw[16:20] in (b'RGB ', b'GRAY') and
             raw[20:24] == b'XYZ ' and raw[36:40] == b'acsp',
             'only v4 display RGB/XYZ or GRAY/XYZ profiles are supported.')
    gray = raw[16:20] == b'GRAY'
    _require(raw[68:80] == struct.pack('>3i', 63190,65536,54061) and not any(raw[100:128]), 'invalid PCS illuminant or reserved header.')
    intent = int.from_bytes(raw[64:68], 'big')
    _require(intent <= 3, 'invalid rendering intent.')
    try:
        datetime(*struct.unpack_from('>6H', raw, 24))
    except ValueError as exc:
        raise ValueError('PNG ICC profile: invalid creation date.') from exc
    count = int.from_bytes(raw[128:132], 'big')
    end = 132+12*count
    _require(1 <= count <= MAX_TAGS and end <= len(raw), 'invalid tag count/table.')
    tags, spans = {}, set()
    required = {b'rXYZ',b'gXYZ',b'bXYZ',b'rTRC',b'gTRC',b'bTRC',b'wtpt',b'desc',b'cprt'}
    if gray:
        required = {b'kTRC',b'wtpt',b'desc',b'cprt'}
    allowed = required | {b'chad',b'bkpt',b'lumi'} | (set() if gray else {b'chrm'})
    for i in range(count):
        check()
        name, offset, size = struct.unpack_from('>4sII', raw, 132+12*i)
        _require(name in allowed and name not in tags, 'unsupported or duplicate tag.')
        _require(offset >= end and offset % 4 == 0 and size >= 8 and offset+size <= len(raw), 'invalid tag span.')
        _require(not any(raw[offset+4:offset+8]), 'nonzero tag reserved bytes.')
        tags[name] = (offset, size)
        spans.add((offset, size))
    _require(required <= tags.keys(), 'missing required matrix/profile tag.')
    for offset, size in sorted(spans):
        _require(0 <= offset-end <= 3 and not any(raw[end:offset]), 'overlapping tags or invalid inter-tag padding.')
        end = offset+size
    _require(0 <= len(raw)-end <= 3 and not any(raw[end:]), 'invalid trailing profile padding.')
    def payload(name):
        off,size=tags[name]
        return raw[off:off+size]
    for name in (b'desc',b'cprt'):
        _mluc(payload(name),check)
    white = _xyz(payload(b'wtpt'))
    _require(all(abs(a-b) <= 2/65536 for a,b in zip(white,D50)), 'unsupported media white point.')
    for name in (b'bkpt',b'lumi'):
        if name in tags:
            _require(all(abs(x) <= 16 for x in _xyz(payload(name))), 'optional XYZ exceeds bounds.')
    if b'chad' in tags:
        p=payload(b'chad')
        _require(len(p)==44 and p[:4]==b'sf32', 'invalid chromatic adaptation tag.')
        values=_fixed(p,8,9); matrix=tuple(values[i:i+3] for i in (0,3,6))
        _require(all(abs(x)<=16 for x in values) and abs(_det(matrix))>=1e-8, 'invalid chromatic adaptation matrix.')
    if b'chrm' in tags:
        p=payload(b'chrm')
        _require(len(p)==36 and p[:4]==b'chrm' and int.from_bytes(p[8:10],'big')==3 and int.from_bytes(p[10:12],'big')<=4,
                 'unsupported chromaticity tag.')
        coords=struct.unpack_from('>6I',p,12)
        _require(all(x<=65536 for x in coords) and all(coords[i]+coords[i+1]<=65536 for i in (0,2,4)), 'invalid chromaticity coordinates.')
    if gray:
        # ICC v4.3 Annex F.2: forward kTRC times PCS white, not media wtpt
        # deviations, an RGB matrix, L*, or a second application of chad.
        curves = (_curve(payload(b'kTRC')),)
        check()
        return Profile(hashlib.sha256(raw).hexdigest(),len(raw),intent,(),curves,'GRAY')
    matrix=tuple(zip(*(_xyz(payload(n)) for n in (b'rXYZ',b'gXYZ',b'bXYZ'))))
    _require(all(abs(v)<=4 for row in matrix for v in row) and
             all(abs(sum(row)-w)<=.002 for row,w in zip(matrix,D50)) and abs(_det(matrix))>=1e-8,
             'matrix exceeds the supported numerical subset.')
    curves=tuple(_curve(payload(n)) for n in (b'rTRC',b'gTRC',b'bTRC'))
    check()
    return Profile(hashlib.sha256(raw).hexdigest(),len(raw),intent,matrix,curves)


def table_bytes(depth, profile=None):
    channels = 1 if profile is not None and profile.color_model == 'GRAY' else 3
    return channels*((1<<depth)*array('d').itemsize+sys.getsizeof(array('d')))


def tables(profile, depth, check):
    maximum=(1<<depth)-1
    result=[]
    for curve in profile.curves:
        table=array('d',[0.])* (maximum+1)
        for n in range(maximum+1):
            if n % 256 == 0:
                check()
            table[n]=curve.evaluate(n/maximum)
        result.append(table)
    check()
    return tuple(result)


def convert(values, profile, tables):
    if profile.color_model == 'GRAY':
        connection = tables[0][values[0]]
        xyz = tuple(connection * white for white in D50)
    else:
        linear=tuple(tables[i][v] for i,v in enumerate(values))
        xyz=tuple(sum(c*v for c,v in zip(row,linear)) for row in profile.matrix)
    output=[]
    for row in PCS_TO_SRGB:
        value=sum(c*v for c,v in zip(row,xyz))
        _require(math.isfinite(value), 'non-finite color result.')
        light=min(1.,max(0.,value))
        encoded=12.92*light if light<=.0031308 else 1.055*light**(1/2.4)-.055
        output.append(min(255,max(0,math.floor(encoded*255+.5))))
    return output
