"""Explicit, non-destructive setup/launch of the Windows desktop source candidate.

Not an installer, wheel builder, browser launcher, or Windows acceptance test.
Use trusted local directories which other processes cannot replace during setup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from check_environment import require_environment

ROOT = Path(__file__).resolve().parents[1]
READY = 'nivram-source-ready.json'
# Exact delivered lock bytes, including line endings. Package versions are unchanged.
WINDOWS_LOCK_SHA256 = '095ca209d4bc98eb266fd999ce8aa181e1a39bb56654ba2176003b51f4f16a91'


def ordinary_sha256(path):
    if path.is_symlink() or path.is_junction():
        raise RuntimeError('Input must be an ordinary file.')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError('Input must be an ordinary file.')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        opened = os.fstat(handle.fileno())
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
        after = os.fstat(handle.fileno())
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if identity(before) != identity(opened) or identity(opened) != identity(after) or identity(after) != identity(path.stat()):
        raise RuntimeError('Input changed while being verified.')
    return digest.hexdigest()


def verify_windows_lock(path):
    if ordinary_sha256(path) != WINDOWS_LOCK_SHA256:
        raise RuntimeError('Complete Windows dependency lock differs from reviewed bytes.')


def pin_inputs(root, wheel, dependency_lock=None):
    pin = json.loads((root / 'scripts/signed-rdkit-wheel-pin.json').read_text(encoding='utf-8'))
    if wheel.name != pin['wheelFilename'] or wheel.is_symlink() or wheel.is_junction() or not wheel.is_file():
        raise RuntimeError('Supply the exact ordinary local signed RDKit wheel; see docs/SIGNED-RDKIT-BUILD.md.')
    if ordinary_sha256(wheel) != pin['wheelSha256']:
        raise RuntimeError('Local signed RDKit wheel SHA-256 differs; do not change the pin.')
    if hashlib.sha256((root / 'scripts/signed_rdkit_wheel.py').read_bytes()).hexdigest() != pin['recipeSha256']:
        raise RuntimeError('Signed RDKit recipe differs from its pin.')
    lock = json.loads((root / 'scripts/signed-rdkit-lock.json').read_text(encoding='utf-8'))
    if hashlib.sha256((json.dumps(lock, sort_keys=True, separators=(',', ':')) + '\n').encode()).hexdigest() != pin['lockSha256']:
        raise RuntimeError('Signed RDKit upstream lock differs from its pin.')
    verify_windows_lock(dependency_lock if dependency_lock is not None else root / 'requirements-windows-lock.txt')
    return pin


def outside_source(root, environment):
    path = Path(os.path.abspath(environment))
    resolved = path.resolve()
    if resolved != path or resolved.is_relative_to(root.resolve()) or root.resolve().is_relative_to(resolved):
        raise RuntimeError('Choose a new ordinary environment outside the source tree (not its ancestor).')
    return resolved


def run(command, root):
    # Isolate pip from user/global configuration as well as PIP_* environment settings.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('PIP_', 'PYTHON'))}
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    subprocess.run([str(part) for part in command], cwd=root, env=env, check=True)


def check_runtime(root, environment):
    require_environment()
    if Path(sys.prefix).resolve() != environment.resolve() or sys.prefix == sys.base_prefix:
        raise RuntimeError('The selected interpreter must belong to the isolated environment; global Python is refused.')
    verify_windows_lock(root / 'requirements-windows-lock.txt')
    from build_evidence import verify_lock
    from signed_rdkit_evidence import verify_installed
    verify_lock(root)
    verify_installed(root)


def setup(root, environment, wheel):
    require_environment()
    environment = outside_source(root, environment)
    pin = pin_inputs(root, wheel)
    # Atomic exclusive reservation: never clear, repair or reuse an existing folder.
    environment.mkdir()
    python = environment / 'Scripts/python.exe'
    run([sys.executable, '-I', '-B', '-m', 'venv', environment], root)
    stage = environment / 'nivram-setup-input'
    stage.mkdir()
    staged_wheel = stage / wheel.name
    with wheel.open('rb') as source, staged_wheel.open('xb') as target:
        shutil.copyfileobj(source, target)
    staged_lock = stage / 'requirements-windows-lock.txt'
    with (root / 'requirements-windows-lock.txt').open('rb') as source, staged_lock.open('xb') as target:
        shutil.copyfileobj(source, target)
    pin_inputs(root, staged_wheel, staged_lock)
    run([python, '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
         'install', '--require-virtualenv', '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
         staged_wheel, '-r', staged_lock], root)
    run([python, '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'check'], root)
    run([python, '-B', root / 'scripts/windows_source.py', 'check', '--environment', environment], root)
    with (environment / READY).open('x', encoding='utf-8') as receipt:
        json.dump({'schemaVersion': 1, 'source': str(root.resolve()), 'wheelSha256': pin['wheelSha256']}, receipt)
    print('Desktop source dependencies verified. Native startup and Windows acceptance remain untested.')


def launch(root, environment):
    require_environment()
    environment = outside_source(root, environment)
    verify_windows_lock(root / 'requirements-windows-lock.txt')
    receipt = json.loads((environment / READY).read_text(encoding='utf-8'))
    pin = json.loads((root / 'scripts/signed-rdkit-wheel-pin.json').read_text(encoding='utf-8'))
    if receipt != {'schemaVersion': 1, 'source': str(root.resolve()), 'wheelSha256': pin['wheelSha256']}:
        raise RuntimeError('Environment setup receipt does not match this source location and wheel.')
    python = environment / 'Scripts/python.exe'
    run([python, '-B', root / 'scripts/windows_source.py', 'check', '--environment', environment], root)
    run([python, '-B', root / 'run_desktop.py'], root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('setup', 'launch', 'check'))
    parser.add_argument('--environment', required=True, type=Path)
    parser.add_argument('--wheel', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'setup' and args.wheel is None:
        parser.error('setup requires --wheel PATH-TO-EXACT-LOCAL-WHEEL')
    try:
        if args.action == 'setup':
            setup(ROOT, args.environment, args.wheel)
        elif args.action == 'launch':
            launch(ROOT, args.environment)
        else:
            check_runtime(ROOT, args.environment)
    except subprocess.CalledProcessError as error:
        print(f'Command failed with exit {error.returncode}; no successful setup/launch is claimed.', file=sys.stderr)
        return error.returncode if error.returncode > 0 else 1
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f'Stopped: {error}. Existing data is not removed; failed setup folders are retained.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
