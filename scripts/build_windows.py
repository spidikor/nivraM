"""Build an application-directory bundle; this does not assert installation acceptance."""
from pathlib import Path
import hashlib
import json
import os
import platform
import subprocess
import sys
import sysconfig
import struct
from datetime import datetime, timezone
from build_evidence import source_manifest, verify_lock, write_dependency_evidence, FONTS
from signed_rdkit_evidence import verify_installed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows-native builds require Windows.')
    if sys.implementation.name != 'cpython' or sys.version_info[:3] != (3, 12, 14) or sysconfig.get_platform() != 'win-amd64' or struct.calcsize('P') != 8:
        raise SystemExit('This candidate requires the tested CPython 3.12.14 Windows x64 build runtime.')
    verify_lock(ROOT)
    signed_before = verify_installed(ROOT)
    source_before = source_manifest(ROOT)
    subprocess.run([sys.executable, '-m', 'pip', 'check'], cwd=ROOT, check=True)
    build_environment = dict(os.environ, NIVRAM_SOURCE_SHA256=source_before['sha256'])
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', 'nivram.spec'], cwd=ROOT, check=True, env=build_environment)
    bundle = ROOT / 'dist' / 'nivraM'
    if not all((bundle / name).is_file() for name in ('nivraM.exe', 'nivram-worker.exe')):
        raise RuntimeError('Expected desktop/worker executables were not built.')
    if any(p.suffix.lower() in FONTS for p in bundle.rglob('*') if p.is_file()):
        raise RuntimeError('Font files must not be redistributed in this application.')
    source_after = source_manifest(ROOT)
    if source_before != source_after:
        raise RuntimeError('Source changed during the build. Candidate has no valid build receipt; rebuild the settled revision.')
    signed_after = verify_installed(ROOT)
    if signed_before != signed_after:
        raise RuntimeError('Signed RDKit changed during the build.')
    write_dependency_evidence(ROOT, bundle, source_after, signed_rdkit=signed_after)
    manifest = [{'path': p.relative_to(bundle).as_posix(), 'bytes': p.stat().st_size,
                 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in sorted(bundle.rglob('*')) if p.is_file()]
    (ROOT / 'verification').mkdir(exist_ok=True)
    (ROOT / 'verification' / 'bundle-manifest.json').write_text(json.dumps({
        'builtAt': datetime.now(timezone.utc).isoformat(), 'source': source_after,
        'platform': platform.platform(), 'python': sys.version, 'signing': 'unsigned',
        'native_smoke': 'not run by build', 'installed': False, 'files': manifest}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
