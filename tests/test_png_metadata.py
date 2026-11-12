import base64
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from nivram import __version__
from nivram.png_metadata import KEYWORD, validate_coordinates, validate_export_text
from nivram.image_import import inspect_png_metadata, select_metadata_candidates
from nivram.batch_export import _decode_png
from test_image_import import png

GRAPH = {'atoms': [{'id': 'a', 'symbol': 'C', 'x': 1.2345678901234567, 'y': -0.0}, {'id': 'b', 'symbol': 'O', 'x': 2.2345678901234567, 'y': 0.125}], 'bonds': [{'id': 'bond', 'begin': 'a', 'end': 'b', 'order': 1}]}


def record(graph=GRAPH):
    raw = json.dumps(graph, separators=(',', ':'))
    return dict(schema='nivram-png-coordinates/1', producer='nivraM', version=__version__, dimension=2,
                origin='drawing-2d', units='drawing', graphJson=raw, graphSha256=hashlib.sha256(raw.encode()).hexdigest())


def payload(value=None):
    return json.dumps(record() if value is None else value).encode()


def texts(value=None):
    return [(b'tEXt', b'Software\0nivraM ' + __version__.encode()),
            (b'iTXt', KEYWORD.encode() + b'\0\0\0\0\0' + payload(value))]


class NativePngMetadataTests(unittest.TestCase):
    def test_exact_numeric_roundtrip_without_layout(self):
        with patch('nivram.image_import.import_structure', side_effect=AssertionError('No layout')):
            result = inspect_png_metadata(png(texts()))
        self.assertEqual(result['status'], 'metadata_available')
        graph = select_metadata_candidates(result, [result['candidates'][0]['id']])
        self.assertEqual(graph, GRAPH)
        self.assertEqual(json.dumps(graph), json.dumps(GRAPH))
        self.assertTrue(any('not authorship' in warning for warning in result['warnings']))

    def test_narrow_batch_allowlist_preserves_bytes(self):
        data = png(texts())
        with patch('nivram.chemistry.graph_to_mol', side_effect=AssertionError('No native engine in bridge')):
            decoded, _ = _decode_png(base64.b64encode(data).decode(), {'width': 1, 'marginMode': 'pixels', 'margin': 0}, threading.Event())
        self.assertEqual(decoded, data)
        for metadata in [texts() + texts(), texts() + [(b'tEXt', b'Comment\0private')], [(b'zTXt', b'Software\0\0no')]]:
            with self.assertRaises(ValueError):
                _decode_png(base64.b64encode(png(metadata)).decode(), {'width': 1, 'marginMode': 'pixels', 'margin': 0}, threading.Event())

    def test_malicious_hash_schema_private_fields_and_limits(self):
        for update in [{'graphSha256': '0'*64}, {'origin': 'generated-3d'}, {'dimension': 3}, {'analysis': {}}, {'version': 'x\nsecret'}, {'dimension': True}, {'version': '\ud800'}]:
            value = record(); value.update(update)
            with self.assertRaises(ValueError): validate_coordinates(payload(value))
        for graph in [dict(GRAPH, analysis={}), {'atoms': [dict(GRAPH['atoms'][0], z=1)], 'bonds': []}, {'atoms': [dict(GRAPH['atoms'][0], x=float('inf'))], 'bonds': []}, {'atoms': [dict(GRAPH['atoms'][0], x=10**1000)], 'bonds': []}]:
            with self.assertRaises(ValueError): validate_coordinates(payload(record(graph)))
        with self.assertRaises(ValueError): validate_coordinates(b'x' * (1024*1024+1))
        value = payload().replace(b'"dimension": 2', b'"dimension": 2, "dimension": 2')
        with self.assertRaises(ValueError): validate_coordinates(value)
        value = record(); value['graphJson'] = '{"atoms":[],"atoms":[],"bonds":[]}'
        value['graphSha256'] = hashlib.sha256(value['graphJson'].encode()).hexdigest()
        with self.assertRaises(ValueError): validate_coordinates(payload(value))

    def test_graph_utf8_byte_limit_matches_browser_contract(self):
        value = record()
        # Valid graph plus insignificant whitespace fits the outer 1 MiB limit,
        # but its exact hashed UTF-8 graph payload exceeds the separate bound.
        value['graphJson'] += ' ' * (512 * 1024 + 1 - len(value['graphJson'].encode()))
        value['graphSha256'] = hashlib.sha256(value['graphJson'].encode()).hexdigest()
        self.assertLess(len(payload(value)), 1024 * 1024)
        with self.assertRaisesRegex(ValueError, '512 KiB'):
            validate_coordinates(payload(value))

    def test_corruption_never_becomes_candidate(self):
        value = record(); value['graphSha256'] = '0'*64
        result = inspect_png_metadata(png(texts(value)))
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['documentStatus'], 'partial_or_unsupported')

    def test_chart_software_has_no_coordinate_candidate(self):
        result = inspect_png_metadata(png(texts()[:1]))
        self.assertEqual(result['candidates'], [])
        self.assertEqual(validate_export_text(*texts()[0]), ('Software', __version__))

    def test_version_conflict_and_duplicate_native_rejected(self):
        data = png([(b'tEXt', b'Software\0nivraM different')] + texts()[1:])
        with self.assertRaises(ValueError):
            _decode_png(base64.b64encode(data).decode(), {'width': 1, 'marginMode': 'pixels', 'margin': 0}, threading.Event())
        with self.assertRaises(ValueError): inspect_png_metadata(png(texts()+texts()[1:]))
