"""Discover the evaluated app-local Java/OPSIN bundle without executing it.

No PATH/JAVA_HOME lookup, installation, download, or network access occurs.
The manifest hash is part of application code, not trusted from adjacent files.
Checks detect missing/modified package contents; they are not OS code signing.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import sysconfig

from .naming import OpsinConfig, OPSIN_VERSION


JAVA_VERSION = '25.0.4.1+1'
JAVA_ARCHIVE_SHA256 = '4c95451cea98556def2c54f7782933f52a26d4a36bd85e1d59f0364464828b07'
OPSIN_SHA256 = 'c2e29326c281f87b59a05d934d8589adac6e9d17b95b984931b3e739111b360f'
MANIFEST_SHA256 = '4a0e4e2564d9209725d32581348528e2459edb0a21e5624ab8cbb1f28ab75532'
MAX_MANIFEST_BYTES = 512 * 1024
MAX_ENTRIES = 2048
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
_REQUIRED = {
    'jre/bin/java.exe', 'jre/bin/server/jvm.dll', 'jre/lib/modules',
    'jre/release', 'jre/NOTICE', 'jre/legal/java.base/LICENSE',
    'jre/legal/java.base/ASSEMBLY_EXCEPTION', 'opsin.jar',
    'legal/OPSIN-LICENSE.txt',
}


@dataclass(frozen=True)
class NamingRuntime:
    status: str
    config: OpsinConfig | None
    message: str
    java_version: str = JAVA_VERSION


def default_bundle_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'naming-runtime'
    return Path(__file__).resolve().parents[2] / 'naming-runtime' / 'bundle'


def _is_reparse(path: Path) -> bool:
    details = path.lstat()
    return path.is_symlink() or bool(
        getattr(details, 'st_file_attributes', 0)
        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    )


def _safe_relative(name: str) -> bool:
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        return False
    parts = PurePosixPath(name)
    return (not parts.is_absolute() and parts.as_posix() == name
            and all(p not in ('.', '..') for p in parts.parts))


def _verify_files(root: Path, files: dict) -> None:
    if not isinstance(files, dict) or not _REQUIRED.issubset(files) or len(files) > MAX_ENTRIES:
        raise ValueError('The naming manifest has an invalid file inventory.')
    total = 0
    for name, record in files.items():
        if not _safe_relative(name) or not isinstance(record, dict):
            raise ValueError('The naming manifest contains an invalid path.')
        size, digest = record.get('size'), record.get('sha256')
        if (isinstance(size, bool) or not isinstance(size, int) or size < 0
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('The naming manifest contains invalid file metadata.')
        total += size
    if total > MAX_BUNDLE_BYTES or files['opsin.jar']['sha256'] != OPSIN_SHA256:
        raise ValueError('The naming bundle does not match the evaluated dependency set.')

    actual, stack, entries = set(), [root], 0
    while stack:
        with os.scandir(stack.pop()) as children:
            for entry in children:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ValueError('The naming bundle exceeds its file/directory limit.')
                path = Path(entry.path)
                if _is_reparse(path):
                    raise ValueError('Links or redirected files are not allowed in the naming bundle.')
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    name = path.relative_to(root).as_posix()
                    if name != 'manifest.json':
                        actual.add(name)
                else:
                    raise ValueError('The naming bundle contains an unsupported filesystem object.')
    if actual != set(files):
        raise ValueError('The naming bundle contains missing or unexpected files.')

    for name, record in files.items():
        path = root / name
        if path.stat().st_size != record['size']:
            raise ValueError('A bundled naming runtime file has changed size: ' + name)
        digest, count = hashlib.sha256(), 0
        with path.open('rb') as source:
            while block := source.read(1024 * 1024):
                count += len(block)
                if count > record['size']:
                    raise ValueError('A naming runtime file changed during validation.')
                digest.update(block)
        if count != record['size'] or digest.hexdigest() != record['sha256']:
            raise ValueError('A bundled naming runtime file failed its checksum: ' + name)


def discover_naming_runtime(bundle_root: str | Path | None = None) -> NamingRuntime:
    """Accept only a trusted app-selected root, never an HTTP/user-supplied path."""
    if (sys.platform != 'win32' or sysconfig.get_platform() != 'win-amd64'
            or struct.calcsize('P') != 8):
        return NamingRuntime('unavailable', None, 'The bundled verifier requires Windows x64.')
    root = Path(bundle_root) if bundle_root is not None else default_bundle_root()
    manifest_path = root / 'manifest.json'
    try:
        if not root.is_dir() or not manifest_path.is_file():
            return NamingRuntime('unavailable', None, 'The optional bundled name verifier is not installed.')
        if _is_reparse(root) or _is_reparse(manifest_path):
            raise ValueError('Links or redirected files are not allowed in the naming bundle.')
        root = root.resolve()
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError('The naming runtime manifest exceeds its size limit.')
        raw = manifest_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
            raise ValueError('The naming runtime manifest does not match the application checksum.')
        manifest = json.loads(raw)
        if (not isinstance(manifest, dict) or manifest.get('schemaVersion') != 1
                or manifest.get('javaVersion') != JAVA_VERSION
                or manifest.get('javaVendor') != 'Eclipse Adoptium'
                or manifest.get('opsinVersion') != OPSIN_VERSION):
            raise ValueError('The naming runtime manifest has an unsupported version.')
        _verify_files(root, manifest.get('files'))
        config = OpsinConfig(root / 'jre/bin/java.exe', root / 'opsin.jar', OPSIN_SHA256)
        return NamingRuntime('available', config, 'The bundled Temurin/OPSIN verifier passed integrity checks.')
    except OSError as exc:
        return NamingRuntime(
            'invalid', None,
            'The bundled name verifier is unavailable: a runtime file could not be read ('
            + type(exc).__name__ + ').',
        )
    except (ValueError, TypeError, UnicodeError) as exc:
        return NamingRuntime('invalid', None, 'The bundled name verifier is unavailable: ' + str(exc).split('\n')[0])
