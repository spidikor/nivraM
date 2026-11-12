"""Build a local Windows wheel from hash-pinned, signed Anaconda artifacts.

No archive hooks or native modules execute here. Public redistribution clearance
is separate. The wheel identifies this adaptation with a local version suffix.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
FONTS = {'.ttf', '.otf', '.ttc', '.otc', '.woff', '.woff2', '.eot'}
MAX_EXPANDED = 512 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def regular_bytes(path, limit=MAX_EXPANDED):
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or getattr(before, 'st_file_attributes', 0) & 0x400 or before.st_size > limit:
        raise ValueError('Expected a bounded ordinary file: ' + str(path))
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
        held = os.fstat(stream.fileno())
    after = path.stat(follow_symlinks=False)
    identity = lambda value: (value.st_ino, value.st_size, value.st_mtime_ns)
    if identity(before) != identity(held) or identity(before) != identity(after) or len(data) != before.st_size:
        raise ValueError('File changed while reading: ' + str(path))
    return data


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or any(ord(c) < 32 or c in '\\:*?"<>|' for c in name)
            or any(part in ('', '.', '..') for part in name.split('/'))
            or any(part.endswith(('.', ' ')) for part in path.parts)
            or any(re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])', part.split('.')[0], re.I) for part in path.parts)):
        raise ValueError('Unsafe archive member name')
    return path


def fetch(package, cache):
    filename = package['filename']
    if safe_name(filename).name != filename or not filename.endswith('.tar.bz2'):
        raise ValueError('Unexpected artifact name')
    expected_url = 'https://repo.anaconda.com/pkgs/main/win-64/' + filename
    if package['url'] != expected_url or not 0 < package['bytes'] <= 64 * 1024 * 1024:
        raise ValueError('Unexpected artifact origin or size')
    path = cache / filename
    if path.exists():
        data = regular_bytes(path, package['bytes'])
    else:
        with urllib.request.urlopen(expected_url, timeout=40) as response:
            if response.url != expected_url:
                raise ValueError('Artifact redirect is not allowed')
            data = response.read(package['bytes'] + 1)
        if len(data) != package['bytes'] or digest(data) != package['sha256']:
            raise ValueError('Downloaded artifact differs from its pin')
        with path.open('xb') as output:
            output.write(data)
    if len(data) != package['bytes'] or digest(data) != package['sha256']:
        raise ValueError('Cached artifact differs from its pin')
    return data


def select_archive(data, package):
    """Return regular selected members, never extract paths or run install hooks."""
    selected = {}; seen = set(); total = 0; actual_metadata = None
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:bz2') as archive:
        for member in archive:
            name = member.name.rstrip('/') if member.isdir() else member.name
            path = safe_name(name)
            key = name.casefold()
            if key in seen:
                raise ValueError('Duplicate or case-colliding archive member')
            seen.add(key)
            if member.isdir():
                continue
            if not member.isfile() or member.size > 64 * 1024 * 1024:
                raise ValueError('Nonregular or oversized archive member')
            total += member.size
            if total > MAX_EXPANDED:
                raise ValueError('Expanded archive limit exceeded')
            chosen = (name == 'info/index.json' or name.startswith('info/licenses/')
                or (package['metadata']['name'] == 'rdkit' and name.startswith('Lib/site-packages/rdkit/'))
                or (len(path.parts) == 3 and name.startswith('Library/bin/') and path.suffix.lower() == '.dll'))
            if not chosen or path.suffix.lower() in FONTS or path.suffix == '.pyc' or '__pycache__' in path.parts:
                continue
            content = archive.extractfile(member).read(member.size + 1)
            if len(content) != member.size:
                raise ValueError('Truncated member')
            if name == 'info/index.json':
                actual_metadata = json.loads(content)
            else:
                selected[name] = content
    if actual_metadata != package['metadata']:
        raise ValueError('Upstream package metadata differs from its pin')
    if not any(name.startswith('info/licenses/') for name in selected):
        raise ValueError('Upstream license files are missing')
    return selected


def verify_signatures(native, directory):
    """Use Windows Authenticode; never substitute hashes for publisher trust."""
    if sys.platform != 'win32':
        raise RuntimeError('Windows signature verification is required')
    directory.mkdir(exist_ok=False)
    names = []
    for name, data in sorted(native.items()):
        path = directory.joinpath(*safe_name(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(data)
        names.append(str(path.resolve()))
    manifest = directory/'paths.json'
    manifest.write_bytes(encoded(names))
    script = """$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$paths=Get-Content -LiteralPath $env:NIVRAM_SIGNATURE_PATHS -Raw | ConvertFrom-Json
$rows=@(foreach($p in $paths){$s=Get-AuthenticodeSignature -LiteralPath $p
[pscustomobject]@{path=$p;status=[string]$s.Status;publisher=$(if($s.SignerCertificate){$s.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false)}else{''})}})
ConvertTo-Json -InputObject $rows -Compress
"""
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    env = dict(os.environ, NIVRAM_SIGNATURE_PATHS=str(manifest.resolve()))
    command = [str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', base64.b64encode(script.encode('utf-16-le')).decode()]
    result = subprocess.run(command, env=env, capture_output=True, timeout=120, check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (directory/'signature-output.log').write_bytes(result.stdout + result.stderr)
    if result.returncode != 0:
        raise RuntimeError('Windows signature verification failed')
    rows = json.loads(result.stdout.decode('utf-8-sig'))
    if (type(rows) is not list or len(rows) != len(names) or {r['path'] for r in rows} != set(names)
            or any(r['status'] != 'Valid' or r['publisher'] != 'Anaconda, Inc.' for r in rows)):
        raise RuntimeError('A native component lacks the expected valid Anaconda signature')
    if any(regular_bytes(Path(path)) != native[Path(path).relative_to(directory.resolve()).as_posix()] for path in names):
        raise RuntimeError('Native bytes changed during signature verification')
    return len(rows)


def wheel_bytes(files, dist_info):
    """Stable archive order, timestamps, attributes and complete SHA256 RECORD."""
    files = dict(files)
    record_name = dist_info + '/RECORD'
    if record_name in files:
        raise ValueError('RECORD is generated, not accepted as input')
    record = io.StringIO(newline='')
    writer = csv.writer(record, lineterminator='\n')
    for name, data in sorted(files.items()):
        writer.writerow([name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(), len(data)])
    writer.writerow([record_name, '', ''])
    files[record_name] = record.getvalue().encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            safe_name(name)
            entry = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            entry.create_system = 3; entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    return stream.getvalue()


def build(lock, cache, output):
    output.mkdir(exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    if cache.is_symlink() or cache.is_junction() or output.is_symlink() or output.is_junction():
        raise ValueError('Redirected cache or output directory')
    version = lock['version']; dist_info = 'rdkit-' + version + '.dist-info'
    if not re.fullmatch(r'2025\.9\.6\+nivram\.2', version) or lock['nativeCount'] != 194:
        raise ValueError('Unexpected adaptation identity')
    files = {}; owners = {}; dlls = []; licenses = []
    for package in lock['packages']:
        selected = select_archive(fetch(package, cache), package)
        for name, data in selected.items():
            if name.startswith('Lib/site-packages/'):
                destination = name.removeprefix('Lib/site-packages/')
            elif name.startswith('Library/bin/'):
                destination = 'rdkit/_nivram_native/' + PurePosixPath(name).name
                dlls.append(dict(name=PurePosixPath(name).name, sha256=digest(data), bytes=len(data), package=package['filename']))
            else:
                relative = package['metadata']['name'] + '/' + name.removeprefix('info/licenses/')
                destination = dist_info + '/licenses/' + relative
                licenses.append(relative)
            if destination.casefold() in owners:
                raise ValueError('Colliding wheel destination')
            owners[destination.casefold()] = {'package': package['filename'], 'archiveMember': name}
            files[destination] = data
    native = {name: data for name, data in files.items() if PurePosixPath(name).suffix.lower() in ('.dll', '.pyd')}
    if len(native) != lock['nativeCount']:
        raise ValueError('Unexpected native component count')
    signature_count = verify_signatures(native, output/'signature-check')
    manifest = json.dumps(dict(schemaVersion=1, rdkitVersion=lock['rdkitVersion'], dlls=sorted(dlls, key=lambda r:r['name'])), sort_keys=True, separators=(',', ':')).encode()
    files['rdkit/_nivram_native/manifest.json'] = manifest
    template_path = HERE/'signed_rdkit_bootstrap.py.in'
    template = regular_bytes(template_path).decode()
    if template.count('MANIFEST_PIN') != 1:
        raise ValueError('Invalid bootstrap template')
    original_init = files['rdkit/__init__.py']
    files['rdkit/__init__.py'] = template.replace('MANIFEST_PIN', repr(digest(manifest))).encode() + original_init
    metadata = ['Metadata-Version: 2.4', 'Name: rdkit', 'Version: '+version,
        'Summary: nivraM local adaptation of signed Anaconda RDKit 2025.09.6',
        'Requires-Python: >=3.12,<3.13', 'Requires-Dist: numpy>=2.1.3,<3', 'Requires-Dist: Pillow',
        'License: See bundled upstream notices; redistribution clearance remains open']
    metadata += ['License-File: '+name for name in sorted(licenses)]
    files[dist_info+'/METADATA'] = ('\n'.join(metadata)+'\n\nLocal adaptation, not an official PyPI wheel or complete conda environment.\n').encode()
    files[dist_info+'/WHEEL'] = b'Wheel-Version: 1.0\nGenerator: nivram-signed-rdkit 1\nRoot-Is-Purelib: false\nTag: cp312-cp312-win_amd64\n'
    provenance = dict(schemaVersion=1, version=version, rdkitVersion=lock['rdkitVersion'],
        origin='local-adaptation-of-anaconda-artifacts', upstreamPackages=lock['packages'],
        recipeSha256=digest(regular_bytes(Path(__file__))), templateSha256=digest(regular_bytes(template_path)),
        lockSha256=digest(encoded(lock)), originalInitSha256=digest(original_init),
        signedNativeComponents=signature_count, redistributionQualified=False, limitations=lock['limitations'],
        files=[dict(path=name, sha256=digest(data), bytes=len(data), origin=owners.get(name.casefold(), {'generated': True})) for name,data in sorted(files.items())])
    files[dist_info+'/nivram-provenance.json'] = encoded(provenance)
    wheel = output/('rdkit-'+version+'-cp312-cp312-win_amd64.whl')
    data = wheel_bytes(files, dist_info)
    with wheel.open('xb') as stream:
        stream.write(data)
    result = dict(status='passed', exitCode=0, wheel=str(wheel.resolve()), sha256=digest(data), bytes=len(data),
        files=len(files)+1, version=version, signaturesValid=signature_count, upstreamArtifacts=len(lock['packages']),
        licenseFiles=len(licenses), redistributionQualified=False, recipeSha256=provenance['recipeSha256'], lockSha256=provenance['lockSha256'])
    with (output/'result.json').open('xb') as stream:
        stream.write(encoded(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        raise SystemExit('Use CPython 3.12 on Windows for this local build recipe.')
    lock = json.loads(regular_bytes(HERE/'signed-rdkit-lock.json'))
    print(json.dumps(build(lock, args.cache.absolute(), args.output.absolute()), indent=2))


if __name__ == '__main__':
    main()
