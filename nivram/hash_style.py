"""Optional spacing for internally generated, semantically identified hashes.

This changes depiction only. The supplied prepared molecule, chemical graph,
atom positions, filled wedges, other SVG paths, and engine color rule stay intact.
"""
from __future__ import annotations

from bisect import bisect_left
import math
import re
from xml.parsers import expat

_SVG = 'http://www.w3.org/2000/svg'
_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
_LINE = re.compile(rf'\s*M\s*({_NUMBER})[\s,]+({_NUMBER})\s+L\s*({_NUMBER})[\s,]+({_NUMBER})\s*')
_WIDTH = re.compile(rf'({_NUMBER})(?:px)?')
_BOND = re.compile(r'(?:^|\s)bond-(\d+)(?=\s|$)')
_PROPERTIES = {'fill', 'fill-rule', 'stroke', 'stroke-width', 'stroke-linecap',
               'stroke-linejoin', 'stroke-opacity'}
_ERROR = ('Thicker, spaced bars could not preserve this hashed stereobond. '
          'Choose Engine spacing for Hashed stereobonds and apply appearance again; '
          'the previous drawing was retained.')
_MAX_BARS = 4096
_ROUNDING = .2  # RDKit's original SVG endpoints are rounded to tenths.


class HashStyleError(ValueError):
    """A fixed public failure category; contains no source data or file paths."""


def _require(condition):
    if not condition:
        raise HashStyleError(_ERROR)


def _center(points):
    return tuple((a + b) / 2 for a, b in zip(*points))


def _interpolate(a, b, fraction):
    return tuple(x + (y - x) * fraction for x, y in zip(a, b))


def _attribute(tag, name, value):
    pattern = re.compile(rf'(?<![\w:.-]){name}\s*=\s*([\'"])(.*?)\1', re.S)
    result, count = pattern.subn(lambda match: name + '=' + match[1] + value + match[1], tag)
    _require(count == 1)
    return result


def _bar(attrs, tag):
    _require(set(attrs) == {'class', 'd', 'style'})
    style = {}
    for declaration in attrs['style'].split(';'):
        if not declaration.strip():
            continue
        key, colon, value = declaration.partition(':')
        key, value = key.strip(), value.strip()
        _require(colon and key in _PROPERTIES and key not in style)
        style[key] = value
    _require(set(style) == _PROPERTIES and style['fill'] == 'none'
             and style['fill-rule'] == 'evenodd' and style['stroke-linecap'] == 'round'
             and style['stroke-linejoin'] == 'round' and style['stroke-opacity'] == '1')
    _require(re.fullmatch(r'#[0-9a-fA-F]{6}', style['stroke']) is not None)
    width = _WIDTH.fullmatch(style['stroke-width'])
    _require(width is not None)
    width = float(width[1])
    _require(math.isfinite(width) and width > 0)
    line = _LINE.fullmatch(attrs['d'])
    _require(line is not None)
    values = tuple(float(value) for value in line.groups())
    _require(all(math.isfinite(value) for value in values))
    points = (values[:2], values[2:])
    length = math.dist(*points)
    _require(math.isfinite(length) and length > 0)
    return {'attrs': attrs, 'tag': tag, 'style': style, 'width': width,
            'points': points, 'center': _center(points), 'length': length}


def _resample(bars, bond_width):
    _require(3 <= len(bars) <= _MAX_BARS)
    # RDKit 2025.09.4 keeps widths below one pixel whole and halves the rest.
    # SVG stroke serialization rounds that engine width to one decimal place.
    expected_engine_width = bond_width if bond_width < 1 else bond_width / 2
    _require(abs(bars[0]['width'] - expected_engine_width) <= .051)
    base = {key: value for key, value in bars[0]['style'].items() if key != 'stroke'}
    colors = []
    for bar in bars:
        _require({key: value for key, value in bar['style'].items() if key != 'stroke'} == base)
        color = bar['style']['stroke'].upper()
        if not colors or colors[-1] != color:
            colors.append(color)
    _require(len(colors) <= 2)  # A color cannot switch back within a hashed bond.
    first, last = bars[0]['center'], bars[-1]['center']
    span = math.dist(first, last)
    _require(math.isfinite(span) and span > 0)
    ux, uy = ((last[i] - first[i]) / span for i in (0, 1))
    along = lambda point: (point[0] - first[0]) * ux + (point[1] - first[1]) * uy
    across = lambda point: (point[0] - first[0]) * uy - (point[1] - first[1]) * ux
    positions, sides = [], []
    for index, bar in enumerate(bars):
        center, points = bar['center'], bar['points']
        position = along(center)
        _require(math.isfinite(position) and (index == 0 or position > positions[-1]))
        _require(abs(across(center)) <= _ROUNDING
                 and abs(along(points[0]) - along(points[1])) <= _ROUNDING)
        _require(index == 0 or bar['length'] > bars[index - 1]['length'])
        positions.append(position)
        sides.append(tuple(sorted(points, key=across)))
    # Reject a bent, twisted, or nonlinear taper before replacing any SVG bytes.
    for position, endpoints in zip(positions, sides):
        for side in (0, 1):
            predicted = _interpolate(sides[0][side], sides[-1][side], position / span)
            _require(math.dist(endpoints[side], predicted) <= _ROUNDING)
    # The nearest achievable uniform spacing preserves the original first/last
    # bars. A short bond must still fit three separated full-width stereo marks.
    _require(span / 2 > bond_width + _ROUNDING)
    target = 3.4 * bond_width
    maximum = min(_MAX_BARS - 1, math.ceil(span / (bond_width + _ROUNDING)) - 1)
    candidates = {max(2, min(maximum, count)) for count in
                  (math.floor(span / target), math.ceil(span / target))}
    gaps = min(candidates, key=lambda count: (abs(span / count - target), count))
    step = span / gaps
    _require(step > bond_width + _ROUNDING)
    output = []
    for index in range(gaps + 1):
        fraction, position = index / gaps, step * index
        if index == 0:
            chosen, path = bars[0], bars[0]['attrs']['d']
        elif index == gaps:
            chosen, path = bars[-1], bars[-1]['attrs']['d']
        else:
            right = bisect_left(positions, position)
            nearest = min((max(0, right - 1), min(len(bars) - 1, right)),
                          key=lambda item: (abs(positions[item] - position), item))
            chosen = bars[nearest]
            a, b = (_interpolate(sides[0][side], sides[-1][side], fraction) for side in (0, 1))
            _require(all(math.isfinite(value) for point in (a, b) for value in point))
            path = f'M {a[0]!r},{a[1]!r} L {b[0]!r},{b[1]!r}'
        # Choose the existing engine color at the nearest old bar center. This
        # preserves its order/boundary sampling, without inventing a gradient.
        style = re.sub(r'(^|;)(\s*stroke-width\s*:)\s*[^;]*',
                       lambda match: match[1] + match[2] + str(bond_width) + 'px',
                       chosen['attrs']['style'])
        output.append(_attribute(_attribute(chosen['tag'], 'd', path), 'style', style).encode('utf-8'))
    return b'\n'.join(output)


def spaced_hashes(svg: str, drawing, bond_width: float) -> str:
    """Adjust only prepared BEGINDASH groups; validate all groups before patching."""
    _require(isinstance(svg, str) and len(svg) <= 4_000_000)
    _require(type(bond_width) in (int, float) and math.isfinite(bond_width) and .5 <= bond_width <= 5)
    from rdkit import Chem
    semantic = {bond.GetIdx(): f'bond-{bond.GetIdx()} atom-{bond.GetBeginAtomIdx()} atom-{bond.GetEndAtomIdx()}'
                for bond in drawing.GetBonds() if bond.GetBondDir() == Chem.BondDir.BEGINDASH}
    if not semantic:
        return svg
    _require(re.search(r'<!\s*(?:DOCTYPE|ENTITY)', svg, re.I) is None)
    encoded = svg.encode('utf-8')
    _require(len(encoded) <= 4_000_000)
    parser = expat.ParserCreate(namespace_separator='}')
    groups = {index: [] for index in semantic}
    depth, child_index = 0, -1

    def start(name, attrs):
        nonlocal depth, child_index
        if depth == 0:
            _require(name == _SVG + '}svg')
            _require(set(attrs) <= {'version', 'baseProfile', 'width', 'height', 'viewBox',
                                   'http://www.w3.org/XML/1998/namespace}space'})
        if depth == 1:
            child_index += 1
        matches = list(_BOND.finditer(attrs.get('class', '')))
        _require(all(len(match[1]) <= 4 for match in matches))
        indexes = [int(match[1]) for match in matches]
        targets = [index for index in indexes if index in semantic]
        if targets:
            _require(len(indexes) == len(targets) == 1)
            index = targets[0]
            _require(name == _SVG + '}path' and depth == 1 and attrs['class'] == semantic[index])
            begin = parser.CurrentByteIndex
            end = encoded.find(b'>', begin) + 1
            _require(end > begin and re.search(br'/\s*>$', encoded[begin:end]) is not None)
            tag = encoded[begin:end].decode('utf-8')
            groups[index].append({'begin': begin, 'end': end, 'child': child_index,
                                  'bar': _bar(dict(attrs), tag)})
        depth += 1

    def end(_name):
        nonlocal depth
        depth -= 1

    parser.StartElementHandler, parser.EndElementHandler = start, end
    try:
        parser.Parse(encoded, True)
    except (expat.ExpatError, UnicodeError, OverflowError):
        raise HashStyleError(_ERROR) from None
    patches = []
    for group in groups.values():
        _require(3 <= len(group) <= _MAX_BARS)
        for first, second in zip(group, group[1:]):
            _require(second['child'] == first['child'] + 1
                     and not encoded[first['end']:second['begin']].strip())
        replacement = _resample([item['bar'] for item in group], bond_width)
        patches.append((group[0]['begin'], group[-1]['end'], replacement))
    for begin, end, replacement in sorted(patches, reverse=True):
        encoded = encoded[:begin] + replacement + encoded[end:]
    _require(len(encoded) <= 4_000_000)
    return encoded.decode('utf-8')
