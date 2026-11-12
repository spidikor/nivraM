"""Semantic failure checks for the focused native drawing acceptance helpers.

These pure checks do not claim native UI, downloads, profiles or package success.
"""
import copy
import io
import unittest

from PIL import Image

from nivram.acceptance_drawing_png import (assert_preserved, assert_rgba_equal,
    assert_transition, assert_displayed_pixels_saved, preview_pixel_observation,
    validate_png_bytes, _assert_hash_style)


def png(*, width=640, ink=True, alpha=255, border=False, compression=6, mode='RGBA'):
    with Image.new('RGBA', (width, 60), (255, 255, 255, 255)) as image:
        if ink:
            image.putpixel((25, 25), (17, 45, 120, alpha))
        if border:
            image.putpixel((11, 25), (1, 2, 3, 255))
        output = io.BytesIO()
        if mode == 'RGB':
            with image.convert('RGB') as rgb:
                rgb.save(output, format='PNG', compress_level=compression)
        else:
            image.save(output, format='PNG', compress_level=compression)
    return output.getvalue()


def state():
    return {'snapshot': {'document': {'graph': {'atoms': [{'charge': 0, 'isotope': 13}], 'bonds': []},
        'style': {'hashBondStyle': 'engine'}, 'export': {'width': 640, 'marginMode': 'pixels', 'margin': 12}},
        'revision': 8, 'dirty': True, 'recoveryGeneration': 'a' * 32},
        'drawing': '<svg/>', 'undoDisabled': False, 'redoDisabled': True}


class DrawingPngSemanticTests(unittest.TestCase):
    def test_full_rgba_equality_ignores_png_compression_and_rgb_alpha_encoding(self):
        a = validate_png_bytes(png(compression=0))
        b = validate_png_bytes(png(compression=9, mode='RGB'))
        self.assertNotEqual(a[0]['sha256'], b[0]['sha256'])
        assert_rgba_equal(a, b)
        self.assertEqual(a[0]['width'], 640)
        self.assertEqual(a[0]['padding'], 12)
        self.assertTrue(a[0]['opaque'] and a[0]['whiteBorders'] and a[0]['nonwhiteInk'])

    def test_a_single_interior_pixel_difference_fails_full_rgba_equality(self):
        original = png()
        with Image.open(io.BytesIO(original)) as image:
            image.putpixel((30, 30), (0, 0, 0, 255))
            output = io.BytesIO(); image.save(output, format='PNG')
        a, b = validate_png_bytes(original), validate_png_bytes(output.getvalue())
        with self.assertRaises(AssertionError):
            assert_rgba_equal(a, b)

    def test_saved_preview_requires_matching_displayed_dimensions_and_full_rgba_digest(self):
        decoded = validate_png_bytes(png())
        identity = {'status': 'ready', 'width': 640, 'height': 60, 'rgbaSha256': decoded[0]['rgbaSha256']}
        assert_displayed_pixels_saved(identity, decoded)
        for field, changed in (('width', 639), ('height', 61), ('rgbaSha256', '0' * 64),
                               ('status', 'failed'), ('width', True), ('height', '60')):
            bad = {**identity, field: changed}
            with self.subTest(field=field, value=changed), self.assertRaises(AssertionError):
                assert_displayed_pixels_saved(bad, decoded)

    def test_preview_observation_has_only_bounded_typed_status_dimensions_and_digest(self):
        malformed = {'status': 'failed', 'width': '640', 'height': 4097, 'rgbaSha256': 'f' * 10000,
                     'pixels': b'unreported', 'message': 'unreported'}
        expected = {'status': 'failed', 'width': None, 'height': None, 'rgbaSha256': None}
        self.assertEqual(preview_pixel_observation(malformed), expected)
        self.assertEqual(preview_pixel_observation(None), {**expected, 'status': 'invalid'})

    def test_nonopaque_pixel_bad_border_blank_image_and_wrong_width_are_rejected(self):
        for data in (png(alpha=254), png(border=True), png(ink=False), png(width=639)):
            with self.subTest(bytes=len(data)), self.assertRaises(AssertionError):
                validate_png_bytes(data)

    def test_signature_only_and_truncated_png_are_not_decoder_success(self):
        for data in (b'\x89PNG\r\n\x1a\n', png()[:40]):
            with self.subTest(bytes=len(data)), self.assertRaises(AssertionError):
                validate_png_bytes(data)

    def test_complete_state_comparison_rejects_changes_outside_graph(self):
        original = state()
        changes = [lambda value: value['snapshot'].__setitem__('revision', 9),
            lambda value: value['snapshot'].__setitem__('dirty', False),
            lambda value: value['snapshot'].__setitem__('recoveryGeneration', 'b' * 32),
            lambda value: value['snapshot']['document']['export'].__setitem__('margin', 0),
            lambda value: value['snapshot']['document']['graph']['atoms'][0].__setitem__('isotope', 0),
            lambda value: value.__setitem__('drawing', '<svg><path/></svg>'),
            lambda value: value.__setitem__('redoDisabled', False)]
        assert_preserved(original, copy.deepcopy(original))
        for mutate in changes:
            changed = copy.deepcopy(original); mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                assert_preserved(original, changed)

    def test_intentional_style_transaction_requires_exact_document_and_one_revision(self):
        before = state(); expected = copy.deepcopy(before['snapshot']['document'])
        expected['style']['hashBondStyle'] = 'spaced'
        after = copy.deepcopy(before); after['snapshot']['document'] = copy.deepcopy(expected); after['snapshot']['revision'] += 1
        assert_transition(before, after, expected)
        undone = copy.deepcopy(after); undone['snapshot']['document'] = copy.deepcopy(before['snapshot']['document'])
        undone['snapshot']['revision'] += 1; undone['redoDisabled'] = False
        assert_transition(after, undone, before['snapshot']['document'], redo_disabled=False)
        with self.assertRaises(AssertionError):
            assert_transition(after, undone, before['snapshot']['document'])
        for mutate in (lambda value: value['snapshot'].__setitem__('revision', 10),
                       lambda value: value['snapshot']['document']['graph']['atoms'][0].__setitem__('charge', 1),
                       lambda value: value.__setitem__('undoDisabled', True)):
            changed = copy.deepcopy(after); mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                assert_transition(before, changed, expected)

    def test_hash_evidence_requires_preserved_semantic_endpoints_and_full_width(self):
        engine = [{'cls': 'bond-0 atom-1 atom-0', 'count': 7, 'first': 'M 0,0 L 0,1',
                   'last': 'M 8,0 L 8,9', 'widths': [2.1] * 7, 'solid': True}]
        spaced = [{**engine[0], 'count': 3, 'widths': [4.2] * 3}]
        self.assertTrue(_assert_hash_style(engine, spaced)['endpointsPreserved'])
        for mutate in (lambda value: value[0].__setitem__('first', 'M 1,0 L 1,1'),
                       lambda value: value[0].__setitem__('widths', [2.1] * 3),
                       lambda value: value[0].__setitem__('solid', False)):
            changed = copy.deepcopy(spaced); mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(AssertionError):
                _assert_hash_style(engine, changed)


class NativeFixtureUnicodeTests(unittest.TestCase):
    def test_exact_unicode_filenames_and_preserved_legacy_progress_labels(self):
        import ast
        from pathlib import Path
        from nivram import acceptance, acceptance_drawing_png
        self.assertEqual(acceptance_drawing_png.PNG_FILENAMES, {
            'main': 'spaced main \u00fc.png', 'preview': 'spaced preview \u00fc.png',
            'theme': 'spaced other theme \u00fc.png'})
        tree = ast.parse(Path(acceptance.__file__).read_text(encoding='utf-8'))
        constants = [node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        for filename in ('ethanol \u00fc.nivram', 'reviewed diagnostics \u00fc.json',
                         'synthetic ethanol metadata \u00fc.png', 'chain 9999 \u00fc.png'):
            self.assertIn(filename, constants)
        for progress in ('Choose a PNG to inspect locally\u2026',
                         'Preparing the selected components\u2026',
                         'Verifying the preserved source and importing\u2026'):
            self.assertTrue(any(progress in value for value in constants))


if __name__ == '__main__':
    unittest.main()
