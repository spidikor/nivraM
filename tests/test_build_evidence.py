"""Browser inventory gates exercised against synthetic bundles, without packaging."""
import importlib.util
from email.message import Message
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import shutil
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('tested_build_evidence', ROOT / 'scripts/build_evidence.py')
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


class BrowserEvidenceTests(unittest.TestCase):
    def test_local_rdkit_origin_omits_private_installation_path(self):
        distribution = SimpleNamespace(metadata={'Name': 'rdkit'}, version='2025.9.6+nivram.2',
            read_text=lambda name: '{"url":"file:///C:/private/build/rdkit.whl"}')
        proof = {'pin': {'version': distribution.version, 'wheelFilename': 'rdkit.whl', 'wheelSha256': 'a'*64}}
        result = evidence.package_origin(distribution, proof)
        self.assertIsNone(result['directUrl'])
        self.assertNotIn('C:/private', json.dumps(result))
        self.assertEqual(result['localWheel']['sha256'], 'a'*64)

    def test_local_rdkit_origin_requires_verified_provenance(self):
        distribution = SimpleNamespace(metadata={'Name': 'rdkit'}, version='2025.9.6+nivram.2')
        with self.assertRaises(RuntimeError):
            evidence.package_origin(distribution)

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='nivram-build-evidence-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'source'
        self.bundle = Path(self.temporary.name) / 'bundle'
        (self.root / 'licenses').mkdir(parents=True)
        self.bundle.mkdir()
        for name in ('LICENSE.txt', 'THIRD-PARTY-NOTICES.md'):
            (self.root / name).write_text('Synthetic unrelated fixture\n', encoding='utf-8')
        sdk = self.root / 'licenses/WebView2-SDK-LICENSE.txt'
        sdk.write_text('Synthetic SDK fixture\n', encoding='utf-8')
        (self.root / 'licenses/WebView2-SDK-provenance.json').write_text(json.dumps({
            'licenseSha256': evidence.sha256(sdk), 'package': 'Microsoft.Web.WebView2', 'version': 'test'}), encoding='utf-8')
        runtime = self.bundle / '_internal/naming-runtime'
        runtime.mkdir(parents=True)
        (runtime / 'opsin.jar').write_bytes(b'synthetic nonexecutable JAR fixture')
        (runtime / 'manifest.json').write_text(json.dumps({
            'javaVersion': 'test', 'javaVendor': 'test', 'javaArchiveSha256': '0' * 64,
            'opsinVersion': 'test'}), encoding='utf-8')

    def write_evidence(self):
        with patch.object(evidence.metadata, 'distributions', return_value=[]), \
                patch('nivram.naming_runtime.discover_naming_runtime', return_value=SimpleNamespace(status='available')):
            evidence.write_dependency_evidence(self.root, self.bundle, {'sha256': '1' * 64})

    def test_browser_dependency_cannot_be_silently_omitted(self):
        with self.assertRaisesRegex(RuntimeError, '[Bb]rowser'):
            self.write_evidence()
        self.assertFalse((self.bundle / 'build-environment.cdx.json').exists())

    def copy_browser_inputs(self):
        for parent in (self.root / 'public/vendor', self.bundle / '_internal/public/vendor'):
            parent.mkdir(parents=True, exist_ok=True)
            for name in evidence.BROWSER_PINS:
                shutil.copyfile(ROOT / 'public/vendor' / name, parent / name)

    def test_pinned_source_bytes_and_original_mit_notices_are_present(self):
        for name, expected in evidence.BROWSER_PINS.items():
            self.assertEqual(evidence.sha256(ROOT / 'public/vendor' / name), expected)
        for name, copyright_holder in (('Chart.js-LICENSE.md', 'Chart.js Contributors'),
                                        ('kurkle-color-LICENSE.md', 'Jukka Kurkela')):
            legal = (ROOT / 'public/vendor' / name).read_text(encoding='utf-8')
            self.assertIn('The MIT License (MIT)', legal)
            self.assertIn(copyright_holder, legal)
            self.assertIn('The above copyright notice and this permission notice shall be included', legal)
            self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', legal)

    def test_actual_bundle_browser_components_relationship_and_notices_are_recorded(self):
        self.copy_browser_inputs()
        self.write_evidence()
        bom = json.loads((self.bundle / 'build-environment.cdx.json').read_text(encoding='utf-8'))
        inventory = json.loads((self.bundle / 'dependency-provenance.json').read_text(encoding='utf-8'))
        npm = {component['name']: component for component in bom['components'] if component.get('purl', '').startswith('pkg:npm/')}
        self.assertEqual(set(npm), {'chart.js', '@kurkle/color'})
        self.assertEqual(npm['chart.js']['version'], '4.5.1')
        self.assertEqual(npm['@kurkle/color']['version'], '0.3.2')
        self.assertEqual(npm['chart.js']['hashes'], [{'alg': 'SHA-256', 'content': evidence.BROWSER_PINS[evidence.BROWSER_ASSET]}])
        self.assertNotIn('hashes', npm['@kurkle/color'], 'The complete UMD digest must not masquerade as a standalone color artifact.')
        for component in npm.values():
            self.assertEqual(component['licenses'], [{'license': {'id': 'MIT'}}])
            properties = {item['name']: item['value'] for item in component['properties']}
            self.assertEqual(properties['nivram:bundled-asset-path'], '_internal/public/vendor/chart.umd.min.js')
            self.assertTrue((self.bundle / properties['nivram:license-path']).is_file())
            self.assertTrue((self.bundle / properties['nivram:provenance-path']).is_file())
        self.assertIn({'ref': 'pkg:npm/chart.js@4.5.1', 'dependsOn': ['pkg:npm/%40kurkle/color@0.3.2']}, bom['dependencies'])
        browser = inventory['browserDependencies']
        self.assertEqual(browser['asset']['bytes'], 208522)
        self.assertEqual(browser['asset']['sha256'], evidence.BROWSER_PINS[evidence.BROWSER_ASSET])
        self.assertEqual(len(browser['components']), 2)
        for component in browser['components']:
            for key in ('licenseFile', 'provenanceFile'):
                self.assertEqual(evidence.sha256(self.bundle / component[key]['path']), component[key]['sha256'])
        color = next(component for component in browser['components'] if component['name'] == '@kurkle/color')
        self.assertEqual(color['embeddedIn'], 'pkg:npm/chart.js@4.5.1')
        self.assertEqual(color['embeddedSourceCorrespondence']['source_sha256'], evidence.BROWSER_COLOR_SOURCE_SHA256)
        self.assertIn('not by themselves', inventory['namingRuntime']['limitation'])
        self.assertTrue(any('test/build tools' in text for text in inventory['limitations']))

    def test_each_missing_browser_input_fails_in_source_or_bundle(self):
        self.copy_browser_inputs()
        for parent in (self.root / 'public/vendor', self.bundle / '_internal/public/vendor'):
            for name in evidence.BROWSER_PINS:
                with self.subTest(parent=parent.name, name=name):
                    path = parent / name
                    original = path.read_bytes()
                    path.unlink()
                    try:
                        with self.assertRaisesRegex(RuntimeError, '[Bb]rowser'):
                            self.write_evidence()
                    finally:
                        path.write_bytes(original)
        self.assertFalse((self.bundle / 'build-environment.cdx.json').exists())

    def test_each_altered_browser_input_fails_in_source_or_bundle(self):
        self.copy_browser_inputs()
        for parent in (self.root / 'public/vendor', self.bundle / '_internal/public/vendor'):
            for name in evidence.BROWSER_PINS:
                with self.subTest(parent=parent.name, name=name):
                    path = parent / name
                    original = path.read_bytes()
                    path.write_bytes(original + b'\nchanged')
                    try:
                        with self.assertRaisesRegex(RuntimeError, '[Bb]rowser'):
                            self.write_evidence()
                    finally:
                        path.write_bytes(original)

    def test_matching_tampered_source_and_bundle_cannot_replace_reviewed_pin(self):
        self.copy_browser_inputs()
        for parent in (self.root / 'public/vendor', self.bundle / '_internal/public/vendor'):
            (parent / evidence.BROWSER_ASSET).write_bytes(b'matching but unreviewed replacement')
        with self.assertRaisesRegex(RuntimeError, '[Bb]rowser'):
            self.write_evidence()

    def test_python_inventory_still_identifies_build_environment_scope(self):
        self.copy_browser_inputs()
        record = Message()
        record['Name'] = 'synthetic-build-tool'
        record['License-Expression'] = 'MIT'
        distribution = SimpleNamespace(metadata=record, version='1.0', files=[], read_text=lambda name: None)
        with patch.object(evidence.metadata, 'distributions', return_value=[distribution]), \
                patch('nivram.naming_runtime.discover_naming_runtime', return_value=SimpleNamespace(status='available')):
            evidence.write_dependency_evidence(self.root, self.bundle, {'sha256': '1' * 64})
        bom = json.loads((self.bundle / 'build-environment.cdx.json').read_text(encoding='utf-8'))
        component = next(c for c in bom['components'] if c['name'] == 'synthetic-build-tool')
        self.assertEqual(component['properties'], [{'name': 'nivram:inventory-scope',
            'value': 'pinned-build-environment; inclusion in runtime must be cross-checked against bundled files'}])


if __name__ == '__main__':
    unittest.main()
