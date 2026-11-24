"""Anchor single-glyph atom labels to the coordinate shown in the editor."""
import re
from xml.etree import ElementTree as ET

# Only RDKit-generated path elements with a sole atom class are candidates.
_ATOM_PATH = re.compile(r"""<path\b(?=[^>]*\bclass=(['"])atom-(\d+)\1)[^>]*?/>""", re.DOTALL)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


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
        outline = attrs.get("d", "")
        # The system Arial N and O outlines have absolute move/line/quadratic
        # coordinates whose axis extrema are present in the coordinate pairs.
        if "transform" in attrs or not outline or re.search(r"[^0-9.,+\sMLQZ\-]", outline):
            continue
        values = [float(v) for v in _NUMBER.findall(outline)]
        if len(values) < 6 or len(values) % 2:
            continue
        xs, ys = values[::2], values[1::2]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        pos = positions[idx]
        dx = pos["x"] - (left + right) / 2
        dy = pos["y"] + (bottom - top) / 8 - (top + bottom) / 2
        changes.append((tag.end() - 2, f' transform="translate({dx:.4f} {dy:.4f})"'))
    for offset, transform in sorted(changes, reverse=True):
        svg = svg[:offset] + transform + svg[offset:]
    return svg
