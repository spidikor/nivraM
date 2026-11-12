"""Installed artifacts and bundled DLLs must retain their pinned identity."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signed_evidence', ROOT/'scripts/signed_rdkit_evidence.py')
evidence = importlib.util.module_from_spec(spec); spec.loader.exec_module(evidence)


class SignedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT/'verification/signed-evidence-tests'; self.parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.parent)
        self.root = Path(self.temp.name); self.site = self.root/'site'; self.site.mkdir()
        (self.root/'scripts').mkdir(); (self.site/'rdkit').mkdir()
        (self.root/'scripts/signed_rdkit_wheel.py').write_bytes(b'synthetic recipe')
        (self.root/'scripts/signed-rdkit-lock.json').write_text('{}\n')
        payload = {'rdkit/__init__.py': b'unchanged Python', 'rdkit/native.pyd': b'unchanged native'}
        self.provenance = dict(recipeSha256=hashlib.sha256(b'synthetic recipe').hexdigest(),
            lockSha256=hashlib.sha256(b'{}\n').hexdigest(), signedNativeComponents=1,
            origin='local-adaptation-of-anaconda-artifacts', upstreamPackages=[], limitations=[], files=[])
        rows = io.StringIO(newline=''); writer = csv.writer(rows, lineterminator='\n')
        for name, data in payload.items():
            (self.site/name).write_bytes(data); digest = hashlib.sha256(data)
            self.provenance['files'].append(dict(path=name, bytes=len(data), sha256=digest.hexdigest()))
            writer.writerow([name, 'sha256='+base64.urlsafe_b64encode(digest.digest()).rstrip(b'=').decode(), str(len(data))])
        raw = json.dumps(self.provenance)
        self.text = {'nivram-provenance.json': raw, 'RECORD': rows.getvalue()}
        self.pin = dict(version='2025.9.6+nivram.2', provenanceSha256=hashlib.sha256(raw.encode()).hexdigest(),
            recipeSha256=self.provenance['recipeSha256'], lockSha256=self.provenance['lockSha256'], nativeCount=1, wheelSha256='a'*64)
        (self.root/'scripts/signed-rdkit-wheel-pin.json').write_text(json.dumps(self.pin))
        self.dist = SimpleNamespace(version=self.pin['version'], read_text=lambda n:self.text.get(n), locate_file=lambda n:self.site/n)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.parent.resolve()))
        self.temp.cleanup()

    def test_exact_installed_payload_is_bound_to_generic_not_pypi_origin(self):
        proof = evidence.verify_installed(self.root, self.dist)
        reference, components, relation = evidence.components(proof)
        self.assertTrue(reference.startswith('pkg:generic/'))
        self.assertNotIn('pkg:pypi/', json.dumps(components))
        self.assertEqual(len(proof['nativeFiles']), 1)
        self.assertEqual(relation['ref'], reference)

    def test_changed_installed_binary_is_rejected(self):
        (self.site/'rdkit/native.pyd').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):evidence.verify_installed(self.root, self.dist)

    def test_record_hash_cannot_disagree_with_pinned_contents(self):
        self.text['RECORD'] = self.text['RECORD'].replace('sha256=', 'sha512=')
        with self.assertRaises(RuntimeError):evidence.verify_installed(self.root, self.dist)

    def test_extra_package_file_is_rejected(self):
        (self.site/'rdkit/unreviewed.py').write_bytes(b'unexpected')
        with self.assertRaises(RuntimeError):evidence.verify_installed(self.root, self.dist)

    def test_altered_provenance_is_rejected_even_if_it_claims_success(self):
        self.text['nivram-provenance.json'] += ' '
        with self.assertRaises(RuntimeError):evidence.verify_installed(self.root, self.dist)

    def test_version_recipe_and_lock_changes_are_rejected(self):
        self.dist.version = '2025.9.4'
        with self.assertRaises(RuntimeError):evidence.verify_installed(self.root, self.dist)
        self.dist.version = self.pin['version']
        (self.root/'scripts/signed_rdkit_wheel.py').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):evidence.verify_installed(self.root, self.dist)
        (self.root/'scripts/signed_rdkit_wheel.py').write_bytes(b'synthetic recipe')
        (self.root/'scripts/signed-rdkit-lock.json').write_text('{"changed":true}')
        with self.assertRaises(RuntimeError):evidence.verify_installed(self.root, self.dist)

    def test_packaging_cannot_alter_a_signed_library(self):
        proof = evidence.verify_installed(self.root, self.dist)
        bundle = self.root/'bundle'; (bundle/'_internal/rdkit').mkdir(parents=True)
        (bundle/'_internal/rdkit/native.pyd').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):evidence.bundled_evidence(bundle, proof)

    def test_bundle_rejects_private_local_installation_metadata(self):
        proof = evidence.verify_installed(self.root, self.dist)
        bundle = self.root/'bundle'; (bundle/'_internal/rdkit').mkdir(parents=True)
        (bundle/'_internal/rdkit/native.pyd').write_bytes((self.site/'rdkit/native.pyd').read_bytes())
        info = bundle/'_internal'/('rdkit-'+self.pin['version']+'.dist-info'); info.mkdir()
        (info/'nivram-provenance.json').write_text(self.text['nivram-provenance.json'])
        private = info/'direct_url.json'; private.write_text('{"url":"file:///C:/private/build/wheel.whl"}')
        with self.assertRaisesRegex(RuntimeError, 'Private local installation'):
            evidence.bundled_evidence(bundle, proof)
        private.unlink()
        self.assertEqual(len(evidence.bundled_evidence(bundle, proof)['nativeFiles']), 1)


if __name__ == '__main__':
    unittest.main()
