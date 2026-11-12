"""Evaluate a verified installer locally, preserving the user's final installation.

The isolated phase refuses any pre-existing product registration, uses a fresh
test directory and no shortcuts, then uninstalls only that exact test instance.
The user-install phase installs per-user and never uninstalls it. Neither phase
claims a clean Windows machine or a cross-version upgrade test.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path, PureWindowsPath
import secrets
import subprocess
import sys
import winreg

from build_evidence import sha256
from build_installer import max_bundle_relative_path_units, utf16_units

ROOT = Path(__file__).resolve().parents[1]
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{4A56E435-742C-4E8C-9CEF-AD78F83B7556}_is1'


def registration():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        return None
    with key:
        try:
            return {name: winreg.QueryValueEx(key, name)[0] for name in ('DisplayVersion', 'InstallLocation', 'UninstallString')}
        except FileNotFoundError as error:
            raise RuntimeError('A present but incomplete nivraM registration must be preserved and reviewed.') from error


def user_shortcut():
    buffer = ctypes.create_unicode_buffer(32768)
    function = ctypes.windll.shell32.SHGetFolderPathW
    function.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
    function.restype = ctypes.c_long
    if function(None, 0x0002, None, 0, buffer) != 0:  # CSIDL_PROGRAMS, current user.
        raise RuntimeError('The current user Programs folder could not be resolved.')
    return Path(buffer.value) / 'nivraM.lnk'


def execute(command, timeout=240, expected_exit_code=0):
    result = subprocess.run([str(value) for value in command], timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    if result.returncode != expected_exit_code:
        raise RuntimeError(f'The owned installer/application process exited with code {result.returncode}.')


def validate_install_path(target, install_subdirectory, files):
    """Reject paths outside the ordinary Windows API limit before installation."""
    target = PureWindowsPath(target)
    relative = PureWindowsPath(install_subdirectory)
    if (not target.is_absolute() or target.drive.startswith(('\\\\?\\', '\\\\.\\')) or
            '\x00' in str(target) or relative.drive or relative.root or '..' in relative.parts):
        raise RuntimeError('Choose a normal absolute installation destination and a contained build directory.')
    prefix = str(target / relative).rstrip('\\') + '\\'
    longest = utf16_units(prefix) + max_bundle_relative_path_units(files)
    if longest >= 260:
        raise RuntimeError('Choose a shorter installation destination: the longest installed file path '
                           f'would use {longest} UTF-16 units; this build requires fewer than 260.')
    return longest


def isolated_target(root, run_id):
    """Resolve only the fresh, short sibling target owned by this check."""
    if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{12}', run_id):
        raise RuntimeError('The isolated installation identifier is invalid.')
    parent = Path(root).parent / 'installer-tests'
    target = parent / run_id
    if parent.resolve() != parent or target.resolve() != target:
        raise RuntimeError('The isolated installation target is redirected; it was retained unchanged.')
    return target


def verify_isolated_target(target, root, run_id):
    expected = isolated_target(root, run_id)
    resolved = Path(target).resolve()
    if Path(target) != expected or resolved != expected or resolved.parent != expected.parent:
        raise RuntimeError('The test uninstall target failed its resolved containment check.')
    return resolved


def long_path_probe_target(target, install_subdirectory, files):
    """An uncreated sibling whose longest installed file is exactly 260 units."""
    target = Path(target)
    probe = target.parent / (target.name + '-rejected-')
    prefix = str(PureWindowsPath(probe) / install_subdirectory).rstrip('\\') + '\\'
    padding = 260 - utf16_units(prefix) - max_bundle_relative_path_units(files)
    if padding < 0:
        raise RuntimeError('The owned rejection-probe parent is too long.')
    probe = probe.with_name(probe.name + 'x' * padding)
    if probe.resolve() != probe or probe.parent.resolve() != target.parent or os.path.lexists(probe):
        raise RuntimeError('The rejection-probe target is not a fresh contained path.')
    return probe


def verify_files(bundle, manifest):
    for row in manifest['files']:
        item = (bundle / row['path']).resolve()
        if not item.is_relative_to(bundle.resolve()) or not item.is_file() or item.stat().st_size != row['bytes'] or sha256(item) != row['sha256']:
            raise RuntimeError('An installed bundle file did not match its build receipt: ' + row['path'])
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual != {row['path'] for row in manifest['files']}:
        raise RuntimeError('The installed build contains unexpected files.')


def validate_receipt(package, manifest):
    """Validate metadata without registry, filesystem, or process side effects.

    Return only the exact relative installation directory derived from the
    inventory. The caller separately verifies the installer and receipt bytes
    and checks resolved containment before executing the installed application.
    """
    if not isinstance(package, dict) or not isinstance(manifest, dict):
        raise RuntimeError('Installer and bundle receipts must be objects.')
    version = package.get('version')
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.]+)?', version):
        raise RuntimeError('The installer version is not a supported safe identifier.')
    source = manifest.get('source')
    files = manifest.get('files')
    if not isinstance(source, dict) or not isinstance(files, list) or not files:
        raise RuntimeError('The bundle receipt is missing its source or file inventory.')
    hashes = [source.get('sha256')]
    hashes.extend(package.get(name) for name in (
        'sourceSha256', 'bundleInventorySha256', 'installerSha256', 'bundleReceiptSha256'))
    if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in hashes):
        raise RuntimeError('The installer receipt contains an invalid SHA-256 identity.')
    seen = set()
    for row in files:
        if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}:
            raise RuntimeError('The bundle receipt contains an invalid file record.')
        path, length, digest = row['path'], row['bytes'], row['sha256']
        if (not isinstance(path, str) or not path or any(char in path for char in '\\:\x00') or
                any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in path.split('/'))):
            raise RuntimeError('The bundle inventory contains an unsafe relative path.')
        if path.casefold() in seen:
            raise RuntimeError('The bundle inventory repeats a Windows file path.')
        seen.add(path.casefold())
        if (isinstance(length, bool) or not isinstance(length, int) or length < 0 or
                not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise RuntimeError('The bundle inventory contains an invalid file size or SHA-256 identity.')
    if not {'nivram.exe', 'nivram-worker.exe'} <= seen:
        raise RuntimeError('The bundle receipt omits a required application executable.')
    inventory_hash = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    expected_subdir = f'versions/{version}-{inventory_hash[:16]}'
    if (package.get('bundleInventorySha256') != inventory_hash or package.get('installSubdirectory') != expected_subdir or
            package.get('sourceSha256') != source['sha256']):
        raise RuntimeError('The installer receipt does not identify the exact source and bundle installation directory.')
    maximum = package.get('maxBundleRelativePathUtf16Units')
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum != max_bundle_relative_path_units(files):
        raise RuntimeError('The installer path-length limit does not match its exact bundle inventory.')
    return expected_subdir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('isolated', 'user-install'), required=True)
    parser.add_argument('--verify-long-path-rejection', action='store_true',
                        help='In the isolated phase, first verify real installer refusal of a fresh overlong target.')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This check requires native Windows.')
    if args.verify_long_path_rejection and args.phase != 'isolated':
        parser.error('The overlong-path rejection probe is restricted to the isolated phase.')
    package = json.loads((ROOT / 'verification' / 'installer-manifest.json').read_text(encoding='utf-8'))
    manifest_path = ROOT / 'verification' / 'bundle-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    expected_subdir = validate_receipt(package, manifest)
    installer = ROOT / 'dist' / 'installer' / f"nivraM-{package['version']}-Windows-x64-setup.exe"
    if sha256(installer) != package['installerSha256'] or sha256(manifest_path) != package['bundleReceiptSha256']:
        raise RuntimeError('Installer/build receipt identity changed; installation refused.')
    previous = registration()
    if previous:
        raise RuntimeError('An existing nivraM registration must be inventoried and backed up before this helper may install. It was retained unchanged.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evaluation = ROOT / 'verification' / f"installer-{args.phase}-{stamp}"
    run_id = secrets.token_hex(6)
    target = isolated_target(ROOT, run_id) if args.phase == 'isolated' else Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'nivraM'
    target = target.resolve()
    if os.path.lexists(target):
        raise RuntimeError('The proposed installation directory already exists; it was retained unchanged.')
    longest_path_units = validate_install_path(target, expected_subdir, manifest['files'])
    shortcut = user_shortcut()
    shortcut_before = shortcut.read_bytes() if shortcut.is_file() else None
    evaluation.mkdir(parents=True, exist_ok=False)
    profile = evaluation / 'test profile ü'
    report = {'status': 'failed', 'phase': args.phase, 'version': package['version'],
              'installerSha256': package['installerSha256'], 'sourceSha256': package['sourceSha256'],
              'checks': [], 'errors': [], 'installationRoot': str(target),
              'longestInstalledPathUtf16Units': longest_path_units,
              'limitations': ['This is the current user laptop, not an isolated clean Windows OS.',
                              'Same-package reinstall is not a cross-version upgrade/rollback test.',
                              'Public redistribution, exact Marvin parity and unavailable scientific features remain separate gates.']}
    output = evaluation / 'receipt.json'
    def check(name):
        report['checks'].append({'id': name, 'status': 'passed'})
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    try:
        if args.phase == 'isolated':
            target.parent.mkdir(parents=True, exist_ok=True)
            verify_isolated_target(target, ROOT, run_id)
            if args.verify_long_path_rejection:
                probe = long_path_probe_target(target, expected_subdir, manifest['files'])
                probe_log = evaluation / 'long-path-rejection.log'
                execute([installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS',
                         '/DIR=' + str(probe), '/LOG=' + str(probe_log)], expected_exit_code=7)
                log_bytes = probe_log.read_bytes()
                log_text = log_bytes.decode('utf-16' if log_bytes.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig', errors='replace')
                if ('NIVRAM_PATH_TOO_LONG' not in log_text or registration() is not None or os.path.lexists(probe) or
                        (shortcut.read_bytes() if shortcut.is_file() else None) != shortcut_before):
                    raise RuntimeError('The overlong-path probe did not demonstrate clean refusal before installation.')
                check('overlong-target-refused-before-files-registration-or-shortcut')
            verify_isolated_target(target, ROOT, run_id)
            target.mkdir(exist_ok=False)
        command = [installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                   '/DIR=' + str(target), '/LOG=' + str(evaluation / 'install.log')]
        if args.phase == 'isolated':
            command.append('/NOICONS')
        execute(command)
        bundle = (target / expected_subdir).resolve()
        if not bundle.is_relative_to(target) or bundle == target:
            raise RuntimeError('The installed executable target left its verified installation root.')
        verify_files(bundle, manifest)
        current = registration()
        if not current or current['DisplayVersion'] != package['version'] or Path(current['InstallLocation']).resolve() != target:
            raise RuntimeError('Per-user registration does not match the exact installed target.')
        check('per-user-install-and-all-file-hashes')
        if args.phase == 'isolated':
            if (shortcut.read_bytes() if shortcut.is_file() else None) != shortcut_before:
                raise RuntimeError('The isolated installer changed the real user shortcut.')
            check('isolated-install-preserves-real-user-shortcut')
        native_report = evaluation / 'native-acceptance.json'
        execute([bundle / 'nivraM.exe', '--acceptance-report', native_report, '--profile', profile], timeout=220)
        native = json.loads(native_report.read_text(encoding='utf-8'))
        if native.get('status') != 'passed':
            raise RuntimeError('The exact installed executable failed native acceptance.')
        check('installed-executable-native-acceptance')
        if args.phase == 'isolated':
            execute(command)
            verify_files(bundle, manifest)
            check('same-package-reinstall-preserves-exact-bundle')
            sentinel = profile / 'uninstall-preservation.json'
            sentinel.write_text('{"synthetic":"user data outside installed files"}', encoding='utf-8')
            sentinel_hash = sha256(sentinel)
            # Resolve and confirm the exact owned test root before invoking its
            # uninstaller. Never run this phase against a final user install.
            verify_isolated_target(target, ROOT, run_id)
            current = registration()
            if not current or Path(current['InstallLocation']).resolve() != target:
                raise RuntimeError('Registration changed before test uninstall; uninstall refused.')
            uninstaller = target / 'unins000.exe'
            if not uninstaller.is_file() or uninstaller.resolve().parent != target:
                raise RuntimeError('The expected test-instance uninstaller is missing.')
            execute([uninstaller, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                     '/LOG=' + str(evaluation / 'uninstall.log')])
            if registration() is not None or (bundle / 'nivraM.exe').exists() or sha256(sentinel) != sentinel_hash:
                raise RuntimeError('Test uninstall did not remove managed files/registration while preserving external user data.')
            if (shortcut.read_bytes() if shortcut.is_file() else None) != shortcut_before:
                raise RuntimeError('The isolated uninstall changed the real user shortcut.')
            check('test-instance-uninstall-preserves-external-profile')
        else:
            report['installedExecutable'] = str(bundle / 'nivraM.exe')
            report['installedExecutableSha256'] = sha256(bundle / 'nivraM.exe')
        report['status'] = 'passed'
    except Exception as error:
        report['errors'].append(type(error).__name__ + ': ' + str(error))
    finally:
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'receipt': str(output)}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
