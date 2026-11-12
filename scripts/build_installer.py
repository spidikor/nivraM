"""Compile only an unchanged, hash-inventoried Windows bundle; no install claim."""
import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from build_evidence import sha256, source_manifest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nivram import __version__


def utf16_units(value):
    """Windows/Inno string length, excluding the terminating NUL."""
    return len(value.encode('utf-16-le')) // 2


def max_bundle_relative_path_units(files):
    if not files:
        raise RuntimeError('A nonempty bundle inventory is required for the installation path limit.')
    return max(utf16_units(row['path'].replace('/', '\\')) for row in files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('A Windows-native compiler is required.')
    compiler = args.compiler.resolve(strict=True)
    receipt_path = ROOT / 'verification' / 'bundle-manifest.json'
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    source = source_manifest(ROOT)
    if receipt['source'] != source:
        raise RuntimeError('Source differs from the bundle receipt. Rebuild before compiling the installer.')
    bundle = ROOT / 'dist' / 'nivraM'
    observed = [{'path': p.relative_to(bundle).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha256(p)}
                for p in sorted(bundle.rglob('*')) if p.is_file()]
    if observed != receipt['files']:
        raise RuntimeError('Bundle contents differ from the build receipt. Installer compilation refused.')
    bundle_sha = hashlib.sha256(json.dumps(observed, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    build_id = bundle_sha[:16]
    maximum_relative_units = max_bundle_relative_path_units(observed)
    subprocess.run([str(compiler), '/DBuildId=' + build_id, '/DAppVersion=' + __version__,
                    '/DMaxBundleRelativePathUnits=' + str(maximum_relative_units),
                    str(ROOT / 'installer' / 'nivram.iss')], cwd=ROOT, check=True)
    installer = ROOT / 'dist' / 'installer' / f'nivraM-{__version__}-Windows-x64-setup.exe'
    if not installer.is_file():
        raise RuntimeError('The compiler returned without the expected installer.')
    after = [{'path': p.relative_to(bundle).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha256(p)}
             for p in sorted(bundle.rglob('*')) if p.is_file()]
    if source_manifest(ROOT) != source or after != observed or receipt_path.read_bytes() != receipt_bytes:
        raise RuntimeError('Source, bundle, or receipt changed during compilation. The installer has no valid receipt; rebuild settled inputs.')
    report = {'schemaVersion': 1, 'builtAt': datetime.now(timezone.utc).isoformat(),
              'version': __version__, 'sourceSha256': source['sha256'],
              'bundleReceiptSha256': hashlib.sha256(receipt_bytes).hexdigest(), 'bundleInventorySha256': bundle_sha,
              'compilerSha256': sha256(compiler), 'installerSha256': sha256(installer),
              'bytes': installer.stat().st_size, 'signing': 'unsigned', 'installed': False,
              'installSubdirectory': f'versions/{__version__}-{build_id}',
              'maxBundleRelativePathUtf16Units': maximum_relative_units,
              'limitations': ['Compilation alone does not establish installation or clean-machine acceptance.',
                              'Previous managed build directories are retained for explicit rollback.',
                              'Public redistribution clearance remains a separate release gate.']}
    (ROOT / 'verification' / 'installer-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
