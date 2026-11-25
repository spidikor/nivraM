"""Anchor single-glyph atom labels to the coordinate shown in the editor."""
import math
import re
from xml.etree import ElementTree as ET

# Only RDKit-generated path elements with a sole atom class are candidates.
_ATOM_PATH = re.compile(r"""<path\b(?=[^>]*\bclass=(['"])atom-(\d+)\1)[^>]*?/>""", re.DOTALL)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")

_DATA = re.compile(r"""\s+d\s*=\s*(['"])(.*?)\1""", re.DOTALL)
_SEGMENT = re.compile(r"([MLQZ])([^MLQZ]*)")
_ARITY = {"M": 2, "L": 2, "Q": 4, "Z": 0}
_PARAMETERS = re.compile(rf"\s*{_NUMBER.pattern}(?:(?:\s*,\s*|\s+|(?=[+-])){_NUMBER.pattern})*\s*")


def _absolute_outline(data):
    """Read only the absolute commands emitted for the supported glyphs."""
    if not data.lstrip().startswith("M") or re.search(r"[^0-9.,+\sMLQZ\-]", data):
        return []
    segments = []
    for command, parameters in _SEGMENT.findall(data):
        arity = _ARITY[command]
        if arity == 0:
            if parameters.strip():
                return []
            numbers = []
        else:
            if not _PARAMETERS.fullmatch(parameters):
                return []
            numbers = [float(n) for n in _NUMBER.findall(parameters)]
            if len(numbers) % arity or not all(math.isfinite(n) for n in numbers):
                return []
        segments.append((command, numbers))
    return segments


def anchor_element_labels(svg, mol, positions):
    # Charged/isotopic atoms, explicit H, radicals, mapped atoms and multiglyph
    # labels have separate typographic layouts. Keep those layouts intact.
    targets = {a.GetIdx() for a in mol.GetAtoms()
               if (a.GetAtomicNum() == 8 or (a.GetAtomicNum() == 7 and a.GetDegree() == 2))
               and not (a.GetTotalNumHs(includeNeighbors=True) or a.GetFormalCharge()
                        or a.GetIsotope() or a.GetAtomMapNum() or a.GetNumRadicalElectrons())}
    if not targets:
        return svg
    matches = {}
    for match in _ATOM_PATH.finditer(svg):
        idx = int(match[2])
        if idx in targets:
            matches.setdefault(idx, []).append(match)
    changes = []
    for idx, glyphs in matches.items():
        if len(glyphs) != 1:
            continue
        tag = glyphs[0]
        attrs = ET.fromstring(tag[0]).attrib
        data = _DATA.search(tag[0])
        if "transform" in attrs or data is None:
            continue
        segments = _absolute_outline(data[2])
        values = [v for _, coordinates in segments for v in coordinates]
        if len(values) < 6:
            continue
        xs, ys = values[::2], values[1::2]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        pos = positions[idx]
        dx = pos["x"] - (left + right) / 2
        dy = pos["y"] + (bottom - top) / 8 - (top + bottom) / 2
        # Translate absolute path coordinates directly so standalone SVG export
        # can retain the outline without accepting transformed source elements.
        moved = []
        for command, coordinates in segments:
            shifted = [f"{value + (dy if i % 2 else dx):.4f}".rstrip("0").rstrip(".")
                       for i, value in enumerate(coordinates)]
            moved.append(" ".join([command, *shifted]))
        changes.append((tag.start() + data.start(2), tag.start() + data.end(2), " ".join(moved)))
    for start, end, replacement in sorted(changes, reverse=True):
        svg = svg[:start] + replacement + svg[end:]
    return svg
