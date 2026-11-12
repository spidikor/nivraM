"""Reject hostile archive structure and preserve installable wheel records."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signed_wheel', ROOT/'scripts/signed_rdkit_wheel.py')
wheel = importlib.util.module_from_spec(spec); spec.loader.exec_module(wheel)


class SignedWheelTests(unittest.TestCase):
    def setUp(self):
        self.fixture_root = ROOT/'verification/signed-wheel-tests'
        self.fixture_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='case-', dir=self.fixture_root)
        self.root = Path(self.temporary.name)
        self.assertTrue(self.root.resolve().is_relative_to(self.fixture_root.resolve()))
        self.package = {'metadata': {'name': 'rdkit', 'version': 'test'}}

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.fixture_root.resolve()))
        self.temporary.cleanup()

    def archive(self, extras=(), license=True):
        rows = [('info/index.json', json.dumps(self.package['metadata']).encode())]
        if license:
            rows.append(('info/licenses/LICENSE', b'synthetic notice'))
        rows += list(extras)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:bz2') as archive:
            for name, data in rows:
                info = tarfile.TarInfo(name)
                if data is None:
                    info.type = tarfile.SYMTYPE; info.linkname = 'outside'
                    archive.addfile(info)
                else:
                    info.size = len(data); archive.addfile(info, io.BytesIO(data))
        return stream.getvalue()

    def test_selects_runtime_and_licenses_without_fonts_or_install_hooks(self):
        data = self.archive([
            ('Lib/site-packages/rdkit/__init__.py', b'original'),
            ('Library/bin/RDKitDepictor.dll', b'binary'),
            ('Lib/site-packages/rdkit/Data/font.ttf', b'font'),
            ('Lib/site-packages/rdkit/__pycache__/old.pyc', b'cache'),
            ('Scripts/.rdkit-post-link.bat', b'unexecuted')])
        result = wheel.select_archive(data, self.package)
        self.assertEqual(set(result), {'info/licenses/LICENSE', 'Lib/site-packages/rdkit/__init__.py', 'Library/bin/RDKitDepictor.dll'})
        self.assertEqual(result['Library/bin/RDKitDepictor.dll'], b'binary')

    def test_rejects_traversal_absolute_and_windows_alias_names(self):
        for name in ['../outside', '/outside', 'C:/outside', 'info\\outside', 'info/CON.txt', 'info/a.', 'info/a ', 'info/a:stream', 'info/a\nname']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                wheel.select_archive(self.archive([(name, b'bad')]), self.package)

    def test_rejects_links_even_outside_selected_payload(self):
        with self.assertRaises(ValueError):
            wheel.select_archive(self.archive([('unselected/link', None)]), self.package)

    def test_rejects_case_collisions(self):
        with self.assertRaises(ValueError):
            wheel.select_archive(self.archive([('Library/bin/A.dll', b'a'), ('Library/bin/a.dll', b'a')]), self.package)

    def test_requires_upstream_licenses(self):
        with self.assertRaises(ValueError):
            wheel.select_archive(self.archive(license=False), self.package)

    def test_checks_exact_upstream_metadata(self):
        data = self.archive(); self.package['metadata']['version'] = 'changed'
        with self.assertRaises(ValueError):
            wheel.select_archive(data, self.package)

    def test_expanded_limit_is_enforced(self):
        with patch.object(wheel, 'MAX_EXPANDED', 10), self.assertRaises(ValueError):
            wheel.select_archive(self.archive(), self.package)

    def test_corrupt_cache_is_rejected_without_download_or_replacement(self):
        path = self.root/'rdkit-test.tar.bz2'; path.write_bytes(b'wrong')
        package = dict(filename=path.name, bytes=5, sha256=hashlib.sha256(b'right').hexdigest(),
                       url='https://repo.anaconda.com/pkgs/main/win-64/'+path.name)
        with patch.object(wheel.urllib.request, 'urlopen') as network, self.assertRaises(ValueError):
            wheel.fetch(package, self.root)
        network.assert_not_called(); self.assertEqual(path.read_bytes(), b'wrong')

    def test_unexpected_origin_is_rejected_before_network(self):
        package = dict(filename='rdkit-test.tar.bz2', bytes=5, sha256='0'*64, url='https://example.invalid/test')
        with patch.object(wheel.urllib.request, 'urlopen') as network, self.assertRaises(ValueError):
            wheel.fetch(package, self.root)
        network.assert_not_called()

    def test_zip_is_deterministic_and_record_covers_every_file(self):
        files = {'rdkit/a.pyd': b'unchanged-native', 'rdkit/x.py': b'print(1)\n', 'rdkit-test.dist-info/METADATA': b'Name: rdkit\n'}
        first = wheel.wheel_bytes(files, 'rdkit-test.dist-info')
        second = wheel.wheel_bytes(dict(reversed(list(files.items()))), 'rdkit-test.dist-info')
        self.assertEqual(first, second)
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            rows = list(csv.reader(io.StringIO(archive.read('rdkit-test.dist-info/RECORD').decode())))
            self.assertEqual({r[0] for r in rows}, set(archive.namelist()))
            for name, encoded_hash, length in rows:
                if name.endswith('/RECORD'):
                    self.assertEqual((encoded_hash, length), ('', '')); continue
                data = archive.read(name)
                self.assertEqual(int(length), len(data))
                self.assertEqual(base64.urlsafe_b64decode(encoded_hash.split('=', 1)[1]+'='), hashlib.sha256(data).digest())
            self.assertEqual(archive.read('rdkit/a.pyd'), files['rdkit/a.pyd'])

    def test_caller_cannot_supply_record(self):
        with self.assertRaises(ValueError):
            wheel.wheel_bytes({'rdkit-test.dist-info/RECORD': b''}, 'rdkit-test.dist-info')

    def test_invalid_or_wrong_publisher_signature_is_rejected(self):
        for status, publisher in [('NotSigned', ''), ('Valid', 'Unexpected Publisher')]:
            directory = self.root/status/publisher.replace(' ', '_') if publisher else self.root/status
            directory.parent.mkdir(parents=True, exist_ok=True)
            path = str((directory/'rdkit/a.pyd').resolve())
            result = subprocess.CompletedProcess([], 0, json.dumps([dict(path=path, status=status, publisher=publisher)]).encode(), b'')
            with patch.object(wheel.subprocess, 'run', return_value=result), self.assertRaises(RuntimeError):
                wheel.verify_signatures({'rdkit/a.pyd': b'synthetic'}, directory)


if __name__ == '__main__':
    unittest.main()
