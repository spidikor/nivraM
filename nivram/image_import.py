"""Bounded PNG molecular-text recovery. This module does not perform OCR.

Raster pixels are never decoded here. Original PNG/MRV data must remain available
to the caller: selecting supported components creates an explicitly partial new
document, not a lossless conversion of arbitrary Marvin documents.
"""
from __future__ import annotations

import copy
from collections import Counter
import hashlib
import re
import struct
import xml.etree.ElementTree as ET
import zlib

from rdkit import Chem, rdBase

from .png_metadata import KEYWORD, validate_coordinates
from .chemistry import InconsistentMolRadicalError, UnsupportedSingletRadicalError, UnsupportedAtomAnnotationError, MAX_ATOMS, MAX_BONDS, ChemistryError, graph_to_mol, import_structure, mol_to_graph


MAX_PNG_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_TOTAL_METADATA = 4 * 1024 * 1024
MAX_CHUNKS = 4096
MAX_XML_NODES = 50_000
MAX_XML_DEPTH = 64
MAX_SOURCE_ATOMS = 16_384
MAX_SOURCE_BONDS = 32_768
MAX_CANDIDATES = 1024
PREVIEW_PIXELS = 16_000_000
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


class ImageImportError(ValueError):
    """The input must not replace an existing document."""


def _inflate(data: bytes) -> bytes:
    if len(data) > MAX_METADATA_BYTES:
        raise ImageImportError('Compressed molecular metadata exceeds the input limit.')
    try:
        decoder = zlib.decompressobj()
        plain = decoder.decompress(data, MAX_METADATA_BYTES + 1)
    except zlib.error as exc:
        raise ImageImportError('PNG text metadata has invalid compression.') from exc
    if len(plain) > MAX_METADATA_BYTES or decoder.unconsumed_tail:
        raise ImageImportError('Expanded PNG text metadata exceeds 1 MiB.')
    if not decoder.eof or decoder.unused_data:
        raise ImageImportError('PNG text metadata has a truncated or concatenated compressed stream.')
    return plain


def _text_chunk(kind: bytes, data: bytes) -> tuple[str, bytes]:
    keyword, separator, remaining = data.partition(b'\0')
    if not separator or not 1 <= len(keyword) <= 79 or any(x < 32 or 127 <= x < 161 for x in keyword):
        raise ImageImportError('PNG text metadata has an invalid keyword.')
    key = keyword.decode('latin-1')
    if kind == b'tEXt':
        plain = remaining
    elif kind == b'zTXt':
        if not remaining or remaining[0] != 0:
            raise ImageImportError('Unsupported PNG text compression method.')
        plain = _inflate(remaining[1:])
    else:
        if len(remaining) < 4 or remaining[0] not in (0, 1) or remaining[1] != 0:
            raise ImageImportError('Malformed international PNG text metadata.')
        compressed = remaining[0]
        fields = remaining[2:].split(b'\0', 2)
        if len(fields) != 3:
            raise ImageImportError('Incomplete international PNG text metadata.')
        plain = _inflate(fields[2]) if compressed else fields[2]
    if len(plain) > MAX_METADATA_BYTES:
        raise ImageImportError('PNG text metadata exceeds 1 MiB.')
    return key, plain


def _read_png(data: bytes) -> tuple[dict, list[tuple[str, bytes]]]:
    if not isinstance(data, bytes) or len(data) > MAX_PNG_BYTES or not data.startswith(PNG_SIGNATURE):
        raise ImageImportError('Provide a PNG file no larger than 64 MiB.')
    position, chunks, total_text = 8, 0, 0
    image, metadata = None, []
    seen_idat = False
    while position < len(data):
        chunks += 1
        if chunks > MAX_CHUNKS or position + 12 > len(data):
            raise ImageImportError('PNG chunk data is truncated or exceeds its limit.')
        length = struct.unpack_from('>I', data, position)[0]
        kind = data[position + 4:position + 8]
        end = position + 12 + length
        if end > len(data) or not re.fullmatch(b'[A-Za-z]{4}', kind):
            raise ImageImportError('PNG chunk length or type is invalid.')
        # Pixel payloads are checksum-checked in place, never copied or decoded.
        # Text gets its own small allocation only after this size preflight.
        if kind in (b'tEXt', b'zTXt', b'iTXt') and length > MAX_METADATA_BYTES + 256:
            raise ImageImportError('A PNG text chunk exceeds its input limit.')
        content = memoryview(data)[position + 8:position + 8 + length]
        crc = struct.unpack_from('>I', data, position + 8 + length)[0]
        if zlib.crc32(content, zlib.crc32(kind)) != crc:
            raise ImageImportError('PNG chunk checksum failed; import was cancelled.')
        if chunks == 1 and kind != b'IHDR':
            raise ImageImportError('PNG dimensions must be in the first chunk.')
        if kind == b'IHDR':
            if image is not None or length != 13:
                raise ImageImportError('PNG has an invalid or duplicate header.')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', content)
            valid_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (not 0 < width <= 2**31 - 1 or not 0 < height <= 2**31 - 1
                    or depth not in valid_depths.get(color, ()) or compression or filtering or interlace not in (0, 1)):
                raise ImageImportError('PNG dimensions or encoding are unsupported.')
            pixels = width * height
            image = {'width': width, 'height': height, 'pixelCount': pixels,
                     'estimatedRgbaBytes': pixels * (8 if depth == 16 else 4),
                     'metadataOnly': True,
                     'previewMode': 'crop_required' if pixels > PREVIEW_PIXELS else 'bounded_preview'}
        elif kind in (b'tEXt', b'zTXt', b'iTXt'):
            item = _text_chunk(kind, bytes(content))
            total_text += len(item[1])
            if total_text > MAX_TOTAL_METADATA:
                raise ImageImportError('Total expanded PNG metadata exceeds 4 MiB.')
            metadata.append(item)
        elif kind == b'IDAT':
            seen_idat = True
        elif kind == b'IEND':
            if length or end != len(data) or not seen_idat:
                raise ImageImportError('PNG end marker or image-data sequence is invalid.')
            return image, metadata
        elif kind[0] < 97 and kind != b'PLTE':
            raise ImageImportError('PNG contains an unsupported critical chunk.')
        position = end
    raise ImageImportError('PNG end marker is missing.')


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit('}', 1)[-1]


def _safe_xml(payload: bytes) -> ET.Element:
    if len(payload) > MAX_METADATA_BYTES or b'\x00' in payload:
        raise ImageImportError('Embedded XML exceeds its limit or uses an unsupported encoding.')
    # Older Marvin exports declare Windows-1252. Honor a small, explicit set of
    # single-byte/UTF-8 encodings rather than guessing or resolving external data.
    declaration = re.match(br'\s*<\?xml[^?]*encoding\s*=\s*[\'"]([^\'"]+)', payload.lstrip(b'\xef\xbb\xbf'), re.IGNORECASE)
    encoding = declaration.group(1).decode('ascii', errors='replace').lower() if declaration else 'utf-8'
    encodings = {'utf-8': 'utf-8-sig', 'utf8': 'utf-8-sig', 'us-ascii': 'ascii',
                 'windows-1252': 'cp1252', 'cp1252': 'cp1252',
                 'iso-8859-1': 'latin-1'}
    if encoding not in encodings:
        raise ImageImportError('The XML declares an unsupported character encoding.')
    try:
        text = payload.decode(encodings[encoding])
    except UnicodeDecodeError as exc:
        raise ImageImportError('Marvin XML does not match its declared character encoding.') from exc
    if '\x00' in text or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.IGNORECASE):
        raise ImageImportError('DTD/entity declarations are forbidden.')
    parser = ET.XMLPullParser(events=('start', 'end'))
    root, depth, count = None, 0, 0
    try:
        for offset in range(0, len(text), 4096):
            parser.feed(text[offset:offset + 4096])
            for event, node in parser.read_events():
                if event == 'start':
                    count += 1
                    depth += 1
                    if root is None:
                        root = node
                    if count > MAX_XML_NODES or depth > MAX_XML_DEPTH:
                        raise ImageImportError('Embedded XML exceeds its node/depth limit.')
                else:
                    depth -= 1
        parser.close()
    except ET.ParseError as exc:
        raise ImageImportError('Embedded Marvin XML is malformed.') from exc
    if root is None:
        raise ImageImportError('Embedded Marvin XML is empty.')
    return root


_ATTRIBUTES = {
    'molecule': {'molID', 'id', 'title', 'role', 'atomRefs', 'bondList', 'connect', 'correspondence'},
    'atomArray': {'atomID', 'elementType', 'x2', 'y2', 'x3', 'y3', 'z3', 'formalCharge', 'isotope', 'sgroupRef', 'hydrogenCount', 'radical', 'mrvMap', 'mrvValence'},
    'atom': {'id', 'elementType', 'x2', 'y2', 'x3', 'y3', 'z3', 'formalCharge', 'isotope', 'sgroupRef', 'hydrogenCount', 'radical', 'mrvMap', 'mrvValence', 'mrvStereoGroup'},
    'bondArray': set(), 'bond': {'id', 'atomRefs2', 'order'},
    'bondStereo': {'convention', 'conventionValue', 'atomRefs4'},
    'atomParity': {'atomRefs4'},
}
_INERT_TAGS = {'propertyList', 'property', 'scalar', 'MBracket', 'MPoint'}


def _mrv_preflight(node: ET.Element) -> list[str]:
    flags = []
    for item in node.iter():
        tag = _tag(item)
        if item.tag.startswith('{') and not item.tag.startswith('{http://www.chemaxon.com}'):
            flags.append('Unsupported XML namespace in a molecular record.')
        if tag in _INERT_TAGS:
            continue
        if tag not in _ATTRIBUTES:
            flags.append('Unsupported chemical XML element: ' + tag)
            continue
        unknown = set(item.attrib) - _ATTRIBUTES[tag]
        if unknown:
            flags.append('Unsupported ' + tag + ' fields: ' + ', '.join(sorted(unknown)))
        if tag in ('atom', 'atomArray') and any(key in item.attrib for key in ('x3', 'y3', 'z3')):
            flags.append('3D molecular coordinates are not supported by this 2D importer.')
        if tag == 'atom' and ('x2' not in item.attrib or 'y2' not in item.attrib):
            flags.append('An MRV atom lacks explicit 2D coordinates.')
        if tag == 'atomArray' and 'atomID' in item.attrib and ('x2' not in item.attrib or 'y2' not in item.attrib):
            flags.append('A compact MRV atom array lacks explicit 2D coordinates.')
    return sorted(set(flags))


def _wrapped_molecule(node: ET.Element) -> str:
    # RDKit's MRV parser expects unprefixed element names. Retain all selected
    # chemical content, including S-groups; they are never stripped to force a fit.
    clone = copy.deepcopy(node)
    for item in clone.iter():
        item.tag = _tag(item)
    root = ET.Element('cml')
    document = ET.SubElement(root, 'MDocument')
    structure = ET.SubElement(document, 'MChemicalStruct')
    structure.append(clone)
    return ET.tostring(root, encoding='unicode')


def _candidates_from_mol(mol: Chem.Mol, source: str, warnings: list[str]) -> list[dict]:
    fragments = Chem.GetMolFrags(mol)
    if len(fragments) > MAX_CANDIDATES:
        raise ImageImportError('The metadata contains more than 1024 disconnected components.')
    # Associate unsupported groups before fragment extraction, which can otherwise
    # lose groups spanning multiple components in some toolkit conversions.
    grouped_atoms = set()
    for group in Chem.GetMolSubstanceGroups(mol):
        grouped_atoms.update(group.GetAtoms())
    pieces = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
    candidates = []
    for index, (indices, piece) in enumerate(zip(fragments, pieces)):
        cid = f'{source}-component-{index + 1}'
        candidate = {'id': cid, 'kind': 'disconnected_component',
                     'label': f'Disconnected component {index + 1} ({piece.GetNumAtoms()} atoms)',
                     'source': source, 'status': 'unsupported', 'graph': None,
                     'atomCount': piece.GetNumAtoms(), 'formalCharge': Chem.GetFormalCharge(piece),
                     'requiresReview': True, 'warnings': list(warnings), 'unsupportedFeatures': []}
        if any(b.GetStereo() == Chem.BondStereo.STEREOANY
               or b.GetBondDir() in (Chem.BondDir.UNKNOWN, Chem.BondDir.EITHERDOUBLE)
               for b in piece.GetBonds()):
            candidate['warnings'].append('This component contains an explicitly unresolved stereochemical bond; review it against the source.')
        try:
            if grouped_atoms.intersection(indices):
                raise ChemistryError('This component intersects a polymer/S-group annotation; lossless editing is unsupported.')
            if piece.GetNumAtoms() > MAX_ATOMS or piece.GetNumBonds() > MAX_BONDS:
                raise ChemistryError('This component exceeds the editor atom/bond limit.')
            if not piece.GetNumConformers() or piece.GetConformer().Is3D():
                raise ChemistryError('This component does not have supported 2D coordinates.')
            graph = mol_to_graph(piece,
                                 ids=[f'{cid}-a{i}' for i in range(piece.GetNumAtoms())],
                                 bond_ids=[f'{cid}-b{i}' for i in range(piece.GetNumBonds())])
            rebuilt = graph_to_mol(graph)
            if Chem.MolToSmiles(rebuilt, isomericSmiles=True) != Chem.MolToSmiles(piece, isomericSmiles=True):
                raise ChemistryError('The supported graph did not preserve parsed chemical identity.')
            candidate.update(status='supported', graph=graph)
        except (ChemistryError, ValueError, RuntimeError) as exc:
            candidate['unsupportedFeatures'].append(str(exc).split('\n')[0])
        candidates.append(candidate)
    return candidates


def _recover_mrv(payload: bytes, source: str) -> tuple[list[dict], list[str]]:
    root = _safe_xml(payload)
    if root.tag not in ('cml', '{http://www.chemaxon.com}cml'):
        raise ImageImportError('The molecular metadata is not a supported Marvin CML document.')
    nodes = list(root.iter())
    if any('reaction' in _tag(node).lower() for node in nodes):
        raise ImageImportError('Reaction metadata is not flattened into ordinary molecules.')
    atom_count = sum(len(node.attrib.get('atomID', '').split()) for node in nodes if _tag(node) == 'atomArray')
    atom_count += sum(_tag(node) == 'atom' for node in nodes)
    bond_count = sum(_tag(node) == 'bond' for node in nodes)
    if not atom_count or atom_count > MAX_SOURCE_ATOMS or bond_count > MAX_SOURCE_BONDS:
        raise ImageImportError('MRV metadata exceeds the 16,384-atom/32,768-bond source limit or contains no atoms.')
    parents = {child: node for node in nodes for child in node}
    molecules, context_issues = [], set()
    for node in nodes:
        if _tag(node) != 'molecule' or node.attrib.get('role'):
            continue
        parent = parents.get(node)
        inside_molecule, unsupported_context = False, False
        while parent is not None:
            if _tag(parent) == 'molecule':
                inside_molecule = True
                break
            if (_tag(parent) not in ('cml', 'MDocument', 'MChemicalStruct')
                    or (parent.tag.startswith('{') and not parent.tag.startswith('{http://www.chemaxon.com}'))):
                unsupported_context = True
                context_issues.add('Molecular records in unsupported document containers were not flattened: ' + _tag(parent) + '.')
            parent = parents.get(parent)
        if not inside_molecule and not unsupported_context:
            molecules.append(node)
    annotations = Counter(_tag(node) for node in nodes if _tag(node).startswith('M')
                          and _tag(node) not in ('MDocument', 'MChemicalStruct'))
    warnings = sorted(context_issues)
    if annotations:
        warnings.append('Document annotations are retained only in the original PNG/MRV: ' + ', '.join(f'{key} ({value})' for key, value in sorted(annotations.items())) + '.')
    if any(_tag(node) == 'propertyList' for node in nodes):
        warnings.append('Arbitrary MRV property metadata remains in the original source and is not copied into the editable graph.')
    if any(_tag(node) == 'molecule' and node.attrib.get('role') for node in nodes):
        warnings.append('The MRV document contains chemical groups; affected components may be unsupported.')
    candidates = []
    for index, node in enumerate(molecules):
        flags = _mrv_preflight(node)
        if flags:
            warnings.extend(flags)
            continue
        with rdBase.BlockLogs():
            mol = Chem.MolFromMrvBlock(_wrapped_molecule(node), sanitize=True, removeHs=False)
        if mol is None:
            warnings.append('A molecular record could not be parsed without changing its chemistry.')
            continue
        # Sanitization may move explicit MRV wedge/unknown marks into MolFile
        # properties. Restore those source marks; do not infer missing stereo.
        Chem.ReapplyMolBlockWedging(mol)
        candidates.extend(_candidates_from_mol(mol, f'{source}-record-{index + 1}', warnings))
        if len(candidates) > MAX_CANDIDATES:
            raise ImageImportError('The metadata contains more than 1024 candidate components.')
    if not molecules:
        warnings.append('No supported top-level molecular record was found.')
    return candidates, warnings


def _native_candidates(payload, source):
    record, graph = validate_coordinates(payload)
    if record['dimension'] == 3:
        raise ImageImportError('Open this PNG in the 3D viewer. Its coordinates are not a 2D drawing and will not be flattened.')
    mol = graph_to_mol(graph)
    warnings = ['Embedded nivraM coordinates are untrusted historical claims. A matching checksum proves integrity only, not authorship, chemical truth or experimental validity. Coordinates are 2D drawing units, not 3D geometry.']
    candidates = []
    for index, indices in enumerate(Chem.GetMolFrags(mol)):
        atoms = [copy.deepcopy(graph['atoms'][i]) for i in indices]
        ids = {atom['id'] for atom in atoms}
        bonds = [copy.deepcopy(bond) for bond in graph['bonds'] if bond['begin'] in ids and bond['end'] in ids]
        piece = {'atoms': atoms, 'bonds': bonds}
        graph_to_mol(piece)
        cid = f'{source}-component-{index + 1}'
        candidates.append({'id': cid, 'kind': 'disconnected_component',
            'label': f'Disconnected component {index + 1} ({len(atoms)} atoms)',
            'source': source, 'status': 'supported', 'graph': piece,
            'atomCount': len(atoms), 'formalCharge': sum(a.get('charge', 0) for a in atoms),
            'requiresReview': True, 'warnings': list(warnings), 'unsupportedFeatures': []})
    return candidates, warnings


def inspect_png_metadata(data: bytes) -> dict:
    """Inspect bytes locally and return selectable component candidates.

    The caller must retain ``data`` and disclose warnings before committing a
    user-selected reconstruction. This function makes no network calls.
    """
    image, records = _read_png(data)
    result = {'schemaVersion': 1, 'status': 'no_supported_metadata',
              'originalSha256': hashlib.sha256(data).hexdigest(), 'image': image,
              'engine': {'name': 'RDKit', 'version': rdBase.rdkitVersion},
              'candidates': [], 'unsupportedSources': [], 'warnings': [],
              'recognition': {'status': 'not_run', 'method': None},
              'preservation': 'The original PNG/MRV must be retained; component selection is a partial reconstruction, not a lossless document conversion.'}
    if sum(key == KEYWORD for key, _payload in records) > 1:
        raise ImageImportError('Duplicate native coordinate metadata is ambiguous.')
    for index, (key, payload) in enumerate(records):
        source = f'metadata-{index + 1}'
        lower = key.lower()
        if 'pkl' in lower or 'pickle' in lower:
            result['unsupportedSources'].append({'source': source, 'keyword': key,
                'features': ['Binary molecular serialization is not deserialized.']})
            continue
        supported = key == KEYWORD or lower == 'molsource' or lower in ('smiles', 'mol', 'molfile') or lower.startswith(('smiles rdkit ', 'mol rdkit '))
        if not supported:
            continue
        try:
            warnings = []
            if key == KEYWORD:
                native_record, _ = validate_coordinates(payload)
                if native_record['dimension'] == 3:
                    result['status'] = 'three_d_coordinates'
                    result['documentStatus'] = 'three_d_viewer_required'
                    result['warnings'] = ['Open this PNG in the 3D viewer. Its coordinates are not a 2D drawing and will not be flattened.']
                    result['candidates'] = []
                    return result
                candidates, warnings = _native_candidates(payload, source)
            elif lower == 'molsource' and (payload.lstrip().startswith(b'<') or payload.startswith((b'\xff\xfe', b'\xfe\xff', b'\xef\xbb\xbf'))):
                candidates, warnings = _recover_mrv(payload, source)
            else:
                text = payload.decode('utf-8')
                if '$$$$' in text or ('V2000' not in text and 'V3000' not in text and any(c.isspace() for c in text.strip())):
                    raise ImageImportError('Multiple records or additional text require a separately supported import format.')
                graph = import_structure(text)
                if 'V2000' not in text and 'V3000' not in text:
                    warnings.append('SMILES metadata has no drawing coordinates; the existing local RDKit import creates a 2D layout.')
                candidates = _candidates_from_mol(graph_to_mol(graph), source, warnings)
            result['candidates'].extend(candidates)
            if warnings:
                result['warnings'].extend(warnings)
                result['unsupportedSources'].append({'source': source, 'keyword': key,
                                                     'features': warnings})
            blocked = [c for c in candidates if c['status'] != 'supported']
            if blocked:
                result['unsupportedSources'].append({'source': source, 'keyword': key,
                    'features': sorted({flag for c in blocked for flag in c['unsupportedFeatures']})})
        except (UnsupportedAtomAnnotationError, UnsupportedSingletRadicalError, InconsistentMolRadicalError) as error:
            result['unsupportedSources'].append({'source': source, 'keyword': key,
                'features': [str(error)]})
        except (ImageImportError, ChemistryError, UnicodeError, ValueError, RuntimeError):
            result['unsupportedSources'].append({'source': source, 'keyword': key,
                'features': ['Molecular metadata was rejected: unsupported/malformed content or a resource limit. The original source is unchanged.']})
        if len(result['candidates']) > MAX_CANDIDATES:
            raise ImageImportError('All molecular metadata together exceeds 1024 candidate components.')
    if result['candidates']:
        result['status'] = 'metadata_available'
        result['warnings'].append('Candidates are disconnected graph components, not verified compounds. Salts or mixtures can require selecting several components together; grouping is an explicit user decision.')
    if result['unsupportedSources']:
        result['documentStatus'] = 'partial_or_unsupported'
    else:
        result['documentStatus'] = 'component_reconstruction_only'
    result['warnings'] = sorted(set(result['warnings']))
    return result


def select_metadata_candidates(result: dict, selected_ids: list[str]) -> dict:
    """Combine only the explicit selection, preserving its coordinates and IDs."""
    if not isinstance(selected_ids, list) or not selected_ids or any(not isinstance(x, str) for x in selected_ids) or len(set(selected_ids)) != len(selected_ids):
        raise ImageImportError('Select one or more distinct supported components explicitly.')
    choices = {candidate['id']: candidate for candidate in result.get('candidates', [])}
    graph = {'atoms': [], 'bonds': []}
    for cid in selected_ids:
        candidate = choices.get(cid)
        if candidate is None or candidate.get('status') != 'supported' or candidate.get('graph') is None:
            raise ImageImportError('A selected component is missing or unsupported; none were imported.')
        if len(graph['atoms']) + len(candidate['graph']['atoms']) > MAX_ATOMS or len(graph['bonds']) + len(candidate['graph']['bonds']) > MAX_BONDS:
            raise ImageImportError('The combined selection exceeds the editor limit; choose a smaller explicit group.')
        graph['atoms'].extend(copy.deepcopy(candidate['graph']['atoms']))
        graph['bonds'].extend(copy.deepcopy(candidate['graph']['bonds']))
    graph_to_mol(graph)
    return graph
