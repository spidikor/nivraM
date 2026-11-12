"""Stage the pinned Java/OPSIN runtime from local files; never download or install.

Requires the checked Windows x64 build environment. The destination must not
exist. Only this invocation's new directory may be removed after a failed stage.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import sys
import sysconfig
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nivram.naming_runtime import (JAVA_ARCHIVE_SHA256, JAVA_VERSION, MANIFEST_SHA256,
                                   OPSIN_SHA256, OPSIN_VERSION, discover_naming_runtime)

SUPPORT = ROOT / 'build-support' / 'naming-runtime'
ARCHIVE_ROOT = 'jdk-25.0.4.1+1-jre'
ARCHIVE_BYTES = 58_475_080
OPSIN_BYTES = 14_335_652
MAX_ENTRIES = 1000
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
FONTS = {'.ttf', '.otf', '.ttc', '.otc', '.woff', '.woff2', '.eot'}
RESERVED_NAME = re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$', re.I)


def _sha256_stream(handle, *, expected_bytes: int) -> str:
    digest, count = hashlib.sha256(), 0
    while block := handle.read(1024 * 1024):
        count += len(block)
        if count > expected_bytes:
            raise ValueError('A runtime input exceeds its pinned size.')
        digest.update(block)
    if count != expected_bytes:
        raise ValueError('A runtime input does not match its pinned size.')
    handle.seek(0)
    return digest.hexdigest()


def _local_input(stack: ExitStack, path: Path, size: int, digest: str, label: str):
    handle = stack.enter_context(Path(path).open('rb'))
    if os.fstat(handle.fileno()).st_size != size:
        raise ValueError(label + ' does not match the pinned archive size.')
    if _sha256_stream(handle, expected_bytes=size) != digest:
        raise ValueError(label + ' does not match the pinned SHA-256 checksum.')
    return handle


def _build_support() -> tuple[bytes, dict, bytes]:
    manifest_path = SUPPORT / 'manifest.json'
    if manifest_path.stat().st_size > 512 * 1024:
        raise ValueError('The source-owned naming manifest exceeds its size limit.')
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('The source-owned naming manifest has changed.')
    manifest = json.loads(raw)
    license_path = SUPPORT / 'OPSIN-LICENSE.txt'
    if license_path.stat().st_size > 16_384:
        raise ValueError('The source-owned OPSIN license exceeds its size limit.')
    license_bytes = license_path.read_bytes()
    record = manifest['files']['legal/OPSIN-LICENSE.txt']
    if (len(license_bytes) != record['size']
            or hashlib.sha256(license_bytes).hexdigest() != record['sha256']):
        raise ValueError('The source-owned OPSIN license has changed.')
    if manifest['files']['opsin.jar'] != {'size': OPSIN_BYTES, 'sha256': OPSIN_SHA256}:
        raise ValueError('The source-owned manifest does not match the pinned OPSIN archive.')
    return raw, manifest, license_bytes


def _ordinary_part(part: str) -> bool:
    return (bool(part) and part not in ('.', '..') and not part.endswith((' ', '.'))
            and not RESERVED_NAME.fullmatch(part)
            and not any(ord(char) < 32 or char in '<>:"|?*\\' for char in part))


def _preflight(archive: zipfile.ZipFile, manifest: dict) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise ValueError('The Java archive exceeds its entry-count limit.')
    expected = {name: record for name, record in manifest['files'].items() if name.startswith('jre/')}
    observed, names, expanded, selected = set(), set(), 0, []
    for entry in entries:
        name = entry.filename
        trimmed = name[:-1] if entry.is_dir() else name
        path = PurePosixPath(trimmed)
        if (entry.orig_filename != name or not trimmed or path.is_absolute() or path.as_posix() != trimmed
                or not all(_ordinary_part(part) for part in path.parts)
                or not path.parts or path.parts[0] != ARCHIVE_ROOT):
            raise ValueError('The Java archive contains an unsafe or unexpected path.')
        key = trimmed.casefold()
        if key in names:
            raise ValueError('The Java archive contains duplicate or case-conflicting paths.')
        names.add(key)
        kind = stat.S_IFMT(entry.external_attr >> 16)
        if (entry.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                or (kind == stat.S_IFDIR and not entry.is_dir())
                or (kind == stat.S_IFREG and entry.is_dir())):
            raise ValueError('Links, special files, and encrypted entries are not permitted in the Java archive.')
        relative = PurePosixPath(*path.parts[1:])
        if entry.is_dir():
            if entry.file_size:
                raise ValueError('The Java archive contains an invalid directory entry.')
        else:
            destination = 'jre/' + relative.as_posix()
            if (destination not in expected or entry.file_size != expected[destination]['size']
                    or Path(relative.name).suffix.lower() in FONTS):
                raise ValueError('A Java archive entry does not match the pinned font-free inventory.')
            expanded += entry.file_size
            if expanded > MAX_EXPANDED_BYTES:
                raise ValueError('The Java archive exceeds its expanded-size limit.')
            observed.add(destination)
        selected.append((entry, relative))
    if observed != set(expected):
        raise ValueError('The Java archive has missing or unexpected runtime files.')
    return selected


def _copy_exact(source, destination: Path, record: dict) -> None:
    digest, count = hashlib.sha256(), 0
    with destination.open('xb') as target:
        while block := source.read(1024 * 1024):
            count += len(block)
            if count > record['size']:
                raise ValueError('A staged runtime file exceeds its pinned size.')
            target.write(block)
            digest.update(block)
    if count != record['size'] or digest.hexdigest() != record['sha256']:
        raise ValueError('A staged runtime file failed its pinned size/checksum validation.')


def _is_reparse(path: Path) -> bool:
    details = path.lstat()
    return path.is_symlink() or bool(
        getattr(details, 'st_file_attributes', 0)
        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    )


def _remove_failed_new_directory(destination: Path, expected_parent: Path) -> None:
    """Remove only this run's new directory after an absolute containment check."""
    try:
        resolved = destination.resolve(strict=True)
        if (_is_reparse(destination) or resolved != destination
                or resolved.parent != expected_parent
                or not resolved.is_relative_to(expected_parent)):
            raise ValueError('The failed staging directory no longer has its original identity.')
        # On Python 3.12/Windows, rmtree does not follow directory junctions.
        shutil.rmtree(resolved)
    except (OSError, ValueError):
        print('Incomplete staging was retained because safe cleanup could not be confirmed.', file=sys.stderr)


def stage_runtime(jre_zip: Path, opsin_jar: Path, destination: Path) -> dict:
    if (sys.platform != 'win32' or sysconfig.get_platform() != 'win-amd64'
            or struct.calcsize('P') != 8):
        raise ValueError('Runtime staging requires the checked Windows x64 Python environment.')
    requested = Path(destination)
    if not _ordinary_part(requested.name):
        raise ValueError('The destination must name an ordinary new directory.')
    parent = requested.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError('Create the destination parent directory before staging.')
    target = parent / requested.name
    if os.path.lexists(target):
        raise ValueError('The staging destination already exists; choose a new directory.')
    manifest_bytes, manifest, license_bytes = _build_support()
    with ExitStack() as stack:
        jre = _local_input(stack, jre_zip, ARCHIVE_BYTES, JAVA_ARCHIVE_SHA256, 'The Java ZIP')
        jar = _local_input(stack, opsin_jar, OPSIN_BYTES, OPSIN_SHA256, 'The OPSIN JAR')
        archive = stack.enter_context(zipfile.ZipFile(jre))
        entries = _preflight(archive, manifest)
        target.mkdir()  # Atomic refusal if another process created it meanwhile.
        complete = False
        try:
            for entry, relative in entries:
                output = target / 'jre' / Path(relative)
                if not output.resolve().is_relative_to(target):
                    raise ValueError('A staging path escaped the new destination.')
                if entry.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry, 'r') as source:
                        _copy_exact(source, output, manifest['files']['jre/' + relative.as_posix()])
            _copy_exact(jar, target / 'opsin.jar', manifest['files']['opsin.jar'])
            (target / 'legal').mkdir()
            with (target / 'legal' / 'OPSIN-LICENSE.txt').open('xb') as handle:
                handle.write(license_bytes)
            with (target / 'manifest.json').open('xb') as handle:
                handle.write(manifest_bytes)
            runtime = discover_naming_runtime(target)
            if runtime.status != 'available':
                raise ValueError('Staged runtime validation failed: ' + runtime.message)
            complete = True
            return {'status': 'available', 'javaVersion': JAVA_VERSION,
                    'opsinVersion': OPSIN_VERSION, 'manifestSha256': MANIFEST_SHA256,
                    'files': len(manifest['files']),
                    'bytes': sum(record['size'] for record in manifest['files'].values()),
                    'publicRedistributionCleared': False}
        finally:
            if not complete:
                _remove_failed_new_directory(target, parent)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jre-zip', required=True, type=Path)
    parser.add_argument('--opsin-jar', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = stage_runtime(args.jre_zip, args.opsin_jar, args.destination)
    except (OSError, zipfile.BadZipFile) as error:
        parser.exit(1, 'Local runtime staging could not read or write a required file (' + type(error).__name__ + ').\n')
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
