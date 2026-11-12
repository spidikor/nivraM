"""Bound records, preserve MOL isotope/map/charge fields and guard radicals.

Size guards are not chemical validation. The radical guard additionally rejects
explicit singlets before RDKit loses their declaration. RDKit remains responsible
for remaining syntax, valence and stereochemistry, followed by graph checks.
"""
from __future__ import annotations

import re

_RING_LABEL = re.compile(r'%(?:([0-9]{2})|\(([0-9]{1,5})\))')

class ImportLimitError(ValueError):
    pass


SINGLET_MESSAGE = ('Explicit MOL singlet radical declarations are not supported. '
                   'Import cancelled to avoid changing singlet to triplet; the original is unchanged. '
                   'Resolve the intended representation in the source editor before importing.')


class UnsupportedMolRadicalError(ImportLimitError):
    """An explicit spin declaration cannot survive the electron-count graph."""
    def __init__(self):
        super().__init__(SINGLET_MESSAGE)


def _radical_integer(value: str) -> int:
    # Deliberately stricter than RDKit's permissive prefix integer reader.
    if not re.fullmatch(r'-?[0-9]{1,9}', value.strip()):
        raise ImportLimitError('Malformed MOL radical declaration; import cancelled.')
    return int(value)


def _radical(value: str) -> int:
    code = _radical_integer(value)
    if code not in (0, 1, 2, 3):
        raise ImportLimitError('Invalid MOL radical code; import cancelled.')
    if code == 1:
        raise UnsupportedMolRadicalError()
    return {0: 0, 2: 1, 3: 2}[code]


def _charge_integer(value: str) -> int:
    # Fixed-width CTfile fields: ASCII digits, optional sign, outer spaces only.
    # Native prefix parsing can accept malformed forms such as "4 0" as 4.
    if not re.fullmatch(r'[+-]?[0-9]+', value.strip(' ')):
        raise ImportLimitError('Malformed V2000 charge field; import cancelled.')
    return int(value)


def _charge_record(line: str, atoms: int) -> None:
    count = _charge_integer(line[6:9])
    if not 0 <= count <= 8 or len(line) < 9 + 8 * count or line[9 + 8 * count:].strip():
        raise ImportLimitError('Malformed V2000 charge record; import cancelled.')
    for offset in range(9, 9 + 8 * count, 8):
        atom = _charge_integer(line[offset:offset + 4])
        charge = _charge_integer(line[offset + 4:offset + 8])
        if not 1 <= atom <= atoms or not -15 <= charge <= 15:
            raise ImportLimitError('Invalid V2000 charge atom or value; import cancelled.')


def _annotation_integer(value: str, minimum: int, maximum: int, kind: str) -> int:
    # Bound the token before conversion; do not accept native prefix truncation,
    # Unicode numerals, internal spaces or floating-point isotope masses.
    token = value.strip(' ')
    if not re.fullmatch(r'[+-]?[0-9]{1,9}', token):
        raise ImportLimitError(f'Malformed MOL {kind} integer; import cancelled.')
    number = int(token)
    if not minimum <= number <= maximum:
        raise ImportLimitError(f'Unsupported MOL {kind} value; import cancelled.')
    return number


def _v2000_isotope_record(line: str, atoms: int, line_index: int, edits: list) -> int:
    count = _annotation_integer(line[6:9], 0, 8, 'isotope count')
    end = 9 + 8 * count
    if len(line) < end or line[end:].strip():
        raise ImportLimitError('Malformed V2000 isotope record; import cancelled.')
    for offset in range(9, end, 8):
        _annotation_integer(line[offset:offset + 4], 1, atoms, 'isotope atom')
        field = line[offset + 4:offset + 8]
        _annotation_integer(field, 0, 400, 'isotope')
        if '+' in field:
            edits.append((line_index, offset + 4, offset + 8, field.replace('+', ' ')))
    # Count/reference signs already survive native parsing. Validate all entries,
    # retaining pinned-native last assignment for repeated isotope declarations.
    return count


def _v2000_radicals(lines: list[str], atoms: int, bonds: int, edits: list) -> dict[int, int]:
    # Walk only property records, not header/atom/bond text or alias payloads.
    # A/G and S SKP consume subsequent physical lines in RDKit 2025.09.4.
    legacy = {}
    mass_fields = []
    for index, line in enumerate(lines[4:4 + atoms]):
        mass = line[34:36]
        if len(mass) == 1:
            raise ImportLimitError('Incomplete V2000 mass-difference field; import cancelled.')
        if mass.strip(' '):
            _annotation_integer(mass, -3, 4, 'legacy mass difference')
        mass_fields.append((index + 4, mass))
        # Native treats a complete noncanonical zero field as a present map0
        # property, which cannot survive graph rebuilding. Canonicalize only
        # this validated default; leave absent short-record fields absent.
        if len(line) > 60:
            field = line[60:63]
            if len(field) != 3:
                raise ImportLimitError('Incomplete V2000 atom-map field; import cancelled.')
            number = _annotation_integer(field, 0, 999999, 'atom map') if field.strip(' ') else 0
            replacement = '  0' if number == 0 else field.replace('+', ' ')
            if replacement != field:
                edits.append((index + 4, 60, 63, replacement))
        # Native records shorter than this have no charge field.
        field = line[36:39] if len(line) >= 39 else ''
        code = _charge_integer(field) if field.strip(' ') else 0
        if not 0 <= code <= 7:
            raise ImportLimitError('Invalid V2000 legacy charge code; import cancelled.')
        if '+' in field:
            edits.append((index + 4, 36, 39, field.replace('+', ' ')))
        if code == 4:
            legacy[index] = 1
    i = 4 + atoms + bonds
    seen = set()
    declared = {}
    overrides_legacy = False
    overrides_mass = False
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith('M  END') or line.startswith('$$$$'):
            break
        if line.startswith(('A', 'G')):
            i += 1
        elif line.startswith('S  SKP'):
            count = _radical_integer(line[6:9])
            if count < 0 or i + count > len(lines):
                raise ImportLimitError('Invalid V2000 skipped-record count.')
            i += count
        elif line.startswith('M  CHG'):
            _charge_record(line, atoms)
            count = _charge_integer(line[6:9])
            # Validation above covers the entire record before any edits are used.
            end = 9 + 8 * count
            if '+' in line[6:end]:
                edits.append((i - 1, 6, end, line[6:end].replace('+', ' ')))
            overrides_legacy = True
        elif line.startswith('M  ISO'):
            if _v2000_isotope_record(line, atoms, i - 1, edits):
                overrides_mass = True
        elif line.startswith('M  RAD'):
            overrides_legacy = True
            count = _radical_integer(line[6:9])
            if not 0 <= count <= 8 or len(line) < 9 + 8 * count or line[9 + 8 * count:].strip():
                raise ImportLimitError('Malformed V2000 radical record; import cancelled.')
            for offset in range(9, 9 + 8 * count, 8):
                atom = _radical_integer(line[offset:offset + 4])
                if not 1 <= atom <= atoms or atom in seen:
                    raise ImportLimitError('Invalid or duplicate MOL radical atom declaration; import cancelled.')
                seen.add(atom)
                electrons = _radical(line[offset + 4:offset + 8])
                if electrons:
                    declared[atom - 1] = electrons
    # Actual CHG/RAD records (even zero-entry records) globally supersede
    # atom-block charge/radical codes. ISO supersedes only isotope fields.
    if not overrides_legacy:
        declared.update(legacy)
    for line_index, field in mass_fields:
        # A nonempty effective ISO property supersedes ALL atom-block dd fields,
        # not D/T symbols. Zero-entry compatibility is a no-op. Clearing source
        # fields before parsing implements precedence without mutating atoms or
        # choosing a different periodic-table isotope base.
        replacement = ' 0' if overrides_mass and field else field.replace('+', ' ')
        if replacement != field:
            edits.append((line_index, 34, 36, replacement))
    return declared


def _v3000_atom_fields(record: str) -> list[tuple[str, int]]:
    # Split at whitespace outside quoted strings/parenthesized values. Keep
    # unrelated values opaque, including literal RAD=1 inside their text.
    fields = []
    start = 0
    quoted = False
    depth = 0
    i = 0
    while i < len(record):
        c = record[i]
        if c == '"' and not depth:
            if i + 1 < len(record) and record[i + 1] == '"':
                i += 2
                continue
            if quoted:
                # Match the pinned native token boundary at a closing quote,
                # even with no following space (X="a"RAD=1). Native tokenization
                # strips the first/closing characters of this token.
                key = record[start:i].partition('=')[0].upper()
                if key in ('RAD', 'CHG', 'MASS'):
                    kind = {'RAD': 'radical', 'CHG': 'charge', 'MASS': 'isotope'}[key]
                    raise ImportLimitError(f'Quoted V3000 {kind} values are unsupported; import cancelled.')
                fields.append((record[start + 1:i], start + 1))
                start = i + 1
            quoted = not quoted
        elif c == '(' and not quoted:
            depth += 1
        elif c == ')' and not quoted:
            depth -= 1
            if depth < 0:
                raise ImportLimitError('Unbalanced V3000 atom property.')
        elif c in ' \t' and not quoted and not depth:
            if start < i:
                fields.append((record[start:i], start))
            start = i + 1
        i += 1
    if quoted or depth:
        raise ImportLimitError('Unbalanced V3000 atom property.')
    if start < len(record):
        fields.append((record[start:], start))
    return fields


def _v3000_atom_declarations(record: str, offsets: list[tuple[int, int]], edits: list) -> int:
    seen = False
    electrons = 0
    fields = _v3000_atom_fields(record)
    if len(fields) < 6:
        raise ImportLimitError('Incomplete V3000 atom record; import cancelled.')
    _annotation_integer(fields[5][0], 0, 999999, 'atom map')
    for token, start in fields[6:]:
        # RDKit also recognizes a fully quoted assignment token.
        if token.startswith('"') and token.endswith('"'):
            token = token[1:-1]
            start += 1
        key, sep, value = token.partition('=')
        if key.upper() == 'MASS':
            if not sep:
                raise ImportLimitError('Malformed V3000 isotope declaration; import cancelled.')
            _annotation_integer(value, 0, 400, 'isotope')
            # A fully quoted assignment can retain outer ASCII spaces around
            # its integer. Locate the validated sign, not the untrimmed value's
            # first character; offsets also cover physical continuations.
            leading_spaces = len(value) - len(value.lstrip(' '))
            if value[leading_spaces:].startswith('+'):
                line, column = offsets[start + len(key) + 1 + leading_spaces]
                edits.append((line, column, column + 1, ''))
        if key.upper() == 'CHG':
            if not sep or not re.fullmatch(r'[+-]?[0-9]{1,9}', value):
                raise ImportLimitError('Malformed V3000 charge declaration; import cancelled.')
            if not -15 <= int(value) <= 15:
                raise ImportLimitError('Invalid V3000 charge value; import cancelled.')
            if value.startswith('+'):
                line, column = offsets[start + len(key) + 1]
                edits.append((line, column, column + 1, ''))
        if key.upper() != 'RAD':
            continue
        if not sep or seen:
            raise ImportLimitError('Malformed or duplicate V3000 radical declaration; import cancelled.')
        seen = True
        electrons = _radical(value)
    return electrons


def _check(atoms: int, bonds: int, max_atoms: int, max_bonds: int) -> None:
    if atoms > max_atoms or bonds > max_bonds:
        raise ImportLimitError(
            f'This alpha supports at most {max_atoms} atoms and {max_bonds} bonds.')


def check_smiles(source: str, max_atoms: int, max_bonds: int) -> None:
    """Bound atom tokens, branch depth and prospective edges in one pass.

    Bracket contents count as one atom. Ring labels can be reused after closing,
    and may use RDKit's %(N) extension. A dot starts a new component; closing a
    branch restores its attachment atom. Unsupported syntax is rejected rather
    than skipped, so it cannot hide extra atoms from the preflight.
    """
    atoms = bonds = i = 0
    current = False
    branches: list[bool] = []
    rings: set[int] = set()
    while i < len(source):
        c = source[i]
        if c == '[':
            end = source.find(']', i + 1)
            if end < 0 or '[' in source[i + 1:end]:
                raise ImportLimitError('Invalid bracketed atom in SMILES.')
            i = end + 1
        elif source[i:i + 2] in ('Cl', 'Br'):
            i += 2
        elif c in 'BCNOPSFIbcnops*':
            i += 1
        elif c == '(':
            if not current or len(branches) >= max_atoms:
                raise ImportLimitError('Invalid or excessively nested SMILES branch.')
            branches.append(current)
            i += 1
            continue
        elif c == ')':
            if not branches:
                raise ImportLimitError('Unbalanced SMILES branch.')
            current = branches.pop()
            i += 1
            continue
        elif c == '.':
            current = False
            i += 1
            continue
        elif c in '-=#:$/\\~':
            i += 1
            continue
        elif c in '0123456789%':
            if not current:
                raise ImportLimitError('A SMILES ring label must follow an atom.')
            if c != '%':
                label = int(c)
                i += 1
            else:
                match = _RING_LABEL.match(source, i)
                if match is None:
                    raise ImportLimitError('Invalid SMILES ring label.')
                label = int(match.group(1) or match.group(2))
                i += len(match.group())
            if label in rings:
                rings.remove(label)
                bonds += 1
            else:
                rings.add(label)
            # Each still-open label would need one more edge to become valid.
            _check(atoms, bonds + len(rings), max_atoms, max_bonds)
            continue
        else:
            raise ImportLimitError('Unsupported SMILES token; nothing was imported.')
        bonds += int(current)
        atoms += 1
        current = True
        _check(atoms, bonds + len(rings), max_atoms, max_bonds)
    if branches or rings:
        raise ImportLimitError('Unclosed SMILES branch or ring label.')


def _count(value: str) -> int:
    if not re.fullmatch(r'[0-9]{1,9}', value):
        raise ImportLimitError('Invalid MOL atom or bond count.')
    return int(value)


def _inspect_mol(source: str, max_atoms: int, max_bonds: int, edits: list) -> dict[int, int]:
    """Preflight and return positive declarations by retained native atom index.

    RDKit with removeHs=False retains atom record order, not V3000 atom IDs.
    Zero/absent declarations impose no constraint on native radical inference.
    """
    # Native MOL records are LF/CRLF physical lines. Python splitlines also
    # treats VT/FF/bare CR and Unicode separators as new records, which can hide
    # a trailing native RAD assignment from the preflight. Reject those forms.
    physical = source.replace('\r\n', '\n')
    if any(c in physical for c in '\r\v\f\x1c\x1d\x1e\x85\u2028\u2029'):
        raise ImportLimitError('Unsupported MOL line separator; use LF or CRLF records. Import cancelled.')
    lines = physical.split('\n')
    if len(lines) < 5:
        raise ImportLimitError('The MOL header is incomplete.')
    header = lines[3]
    if header.rstrip().endswith('V2000'):
        atoms, bonds = _count(header[:3].strip()), _count(header[3:6].strip())
        _check(atoms, bonds, max_atoms, max_bonds)
        return _v2000_radicals(lines, atoms, bonds, edits)
    if not header.rstrip().endswith('V3000'):
        raise ImportLimitError('A V2000 or V3000 MOL counts line is required.')

    declared = None
    section = None
    seen: set[str] = set()
    actual = {'ATOM': 0, 'BOND': 0}
    radicals = {}
    atom_labels = set()
    continuation: list[str] = []
    offsets: list[tuple[int, int]] = []
    for line_index, line in enumerate(lines[4:], 4):
        if line.startswith(('M  END', '$$$$')):
            break
        if not line.startswith('M  V30 '):
            if continuation:
                raise ImportLimitError('Incomplete V3000 continuation record.')
            continue
        content = line[7:]
        part = content[:-1] if content.endswith('-') else content
        continuation.append(part)
        offsets.extend((line_index, column) for column in range(7, 7 + len(part)))
        if content.endswith('-'):
            continue
        joined = ''.join(continuation)
        record = joined.strip()
        record_offsets = offsets[len(joined) - len(joined.lstrip()):]
        offsets = []
        continuation.clear()
        if record == 'END CTAB':
            break
        fields = record.split(None, 3)
        if not fields:
            continue
        if fields[0] == 'COUNTS':
            if declared is not None or len(fields) < 3:
                raise ImportLimitError('Invalid V3000 counts record.')
            declared = (_count(fields[1]), _count(fields[2]))
            _check(*declared, max_atoms, max_bonds)
        elif fields[0] == 'BEGIN' and len(fields) == 2 and fields[1] in actual:
            if section is not None or fields[1] in seen:
                raise ImportLimitError('Duplicate or nested V3000 structure block.')
            section = fields[1]
            seen.add(section)
        elif fields[0] == 'END' and len(fields) == 2 and fields[1] in actual:
            if section != fields[1]:
                raise ImportLimitError('Unbalanced V3000 structure block.')
            section = None
        elif section is not None:
            if section == 'ATOM':
                label = _count(fields[0])
                if not label or label in atom_labels:
                    raise ImportLimitError('Invalid or duplicate V3000 atom label; import cancelled.')
                atom_labels.add(label)
                electrons = _v3000_atom_declarations(record, record_offsets, edits)
                if electrons:
                    radicals[actual['ATOM']] = electrons
            actual[section] += 1
            _check(actual['ATOM'], actual['BOND'], max_atoms, max_bonds)
    if continuation or section is not None or declared is None:
        raise ImportLimitError('Incomplete V3000 structure records.')
    if declared != (actual['ATOM'], actual['BOND']):
        raise ImportLimitError('V3000 declared and actual atom/bond counts differ.')

    return radicals


def check_mol(source: str, max_atoms: int, max_bonds: int) -> dict[int, int]:
    """Validate raw declarations without changing the caller's source."""
    return _inspect_mol(source, max_atoms, max_bonds, [])


def prepare_mol(source: str, max_atoms: int, max_bonds: int) -> tuple[str, dict[int, int]]:
    """Return a transient native-input copy and unchanged radical guards.

    RDKit 2025.09.4 loses leading-plus integers before sanitization. Edit only
    validated charge/isotope/map fields. Effective V2000 ISO entries supersede
    legacy mass differences, leaving D/T symbols intact; validated map zero is
    canonicalized to native absence. All unrelated bytes and physical line endings
    survive. No molecular roundtrip or post-parse atom mutation occurs. V3000 integer
    spelling is a bounded compatibility contract, not general CTfile conformance.
    """
    edits: list[tuple[int, int, int, str]] = []
    radicals = _inspect_mol(source, max_atoms, max_bonds, edits)
    if not edits:
        return source, radicals
    lines = source.split('\n')  # retain CR in CRLF, unlike the inspection copy
    for line, start, end, replacement in sorted(edits, reverse=True):
        lines[line] = lines[line][:start] + replacement + lines[line][end:]
    return '\n'.join(lines), radicals


RADICAL_CONSISTENCY_MESSAGE = (
    'Declared radical electron counts change during MOL sanitization. '
    'Import cancelled; the original is unchanged. Resolve the intended hydrogen, '
    'valence and radical declarations in the source editor before importing.')


class MolRadicalConsistencyError(ImportLimitError):
    """Positive source counts differ from the existing native interpretation."""
    def __init__(self):
        super().__init__(RADICAL_CONSISTENCY_MESSAGE)


def check_radical_consistency(declarations: dict[int, int], mol) -> None:
    """Reject, never repair, a changed positive declaration before adoption.

    This is electron-count consistency only, not spin multiplicity or complete
    MOL fidelity. The caller must retain atoms and its native sanitize lifecycle.
    """
    for index, electrons in declarations.items():
        if index >= mol.GetNumAtoms() or mol.GetAtomWithIdx(index).GetNumRadicalElectrons() != electrons:
            raise MolRadicalConsistencyError()
