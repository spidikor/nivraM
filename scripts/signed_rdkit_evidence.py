"""Bind the installed adaptation and packaged native files to reviewed wheel bytes."""
import csv
import base64
import hashlib
import importlib.metadata as metadata
import io
import json
from pathlib import Path, PurePosixPath
import shutil
from urllib.parse import quote


def sha(path):
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise RuntimeError('Signed RDKit evidence needs an ordinary file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_installed(root, distribution=None):
    root = Path(root)
    pin = json.loads((root/'scripts/signed-rdkit-wheel-pin.json').read_text(encoding='utf-8'))
    dist = distribution if distribution is not None else metadata.distribution('rdkit')
    if dist.version != pin['version']:
        raise RuntimeError('Signed RDKit adaptation version differs')
    raw = dist.read_text('nivram-provenance.json')
    if raw is None or hashlib.sha256(raw.encode()).hexdigest() != pin['provenanceSha256']:
        raise RuntimeError('Signed RDKit provenance differs from the reviewed wheel')
    provenance = json.loads(raw)
    if (provenance['recipeSha256'] != pin['recipeSha256'] or provenance['lockSha256'] != pin['lockSha256']
            or provenance['signedNativeComponents'] != pin['nativeCount']):
        raise RuntimeError('Signed RDKit recipe identity differs')
    if sha(root/'scripts/signed_rdkit_wheel.py') != pin['recipeSha256']:
        raise RuntimeError('Signed RDKit wheel recipe changed after its pin')
    lock = json.loads((root/'scripts/signed-rdkit-lock.json').read_text(encoding='utf-8'))
    canonical = (json.dumps(lock, sort_keys=True, separators=(',', ':'))+'\n').encode()
    if hashlib.sha256(canonical).hexdigest() != pin['lockSha256']:
        raise RuntimeError('Signed RDKit upstream lock differs')
    site = Path(dist.locate_file('')).resolve()
    record_text = dist.read_text('RECORD')
    if not record_text:
        raise RuntimeError('Signed RDKit installed RECORD is missing')
    rows = list(csv.reader(io.StringIO(record_text)))
    if any(len(row) != 3 for row in rows) or len({r[0].casefold() for r in rows}) != len(rows):
        raise RuntimeError('Signed RDKit installed RECORD is malformed')
    recorded = {row[0]: row[1:] for row in rows}
    native = []; expected = set()
    for row in provenance['files']:
        relative = PurePosixPath(row['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in row['path'] or ':' in row['path']:
            raise RuntimeError('Invalid signed RDKit provenance path')
        path = site.joinpath(*relative.parts)
        if path.resolve() != path.absolute() or not path.resolve().is_relative_to(site):
            raise RuntimeError('Redirected signed RDKit package path')
        record_hash = 'sha256=' + base64.urlsafe_b64encode(bytes.fromhex(row['sha256'])).rstrip(b'=').decode()
        if (recorded.get(row['path']) != [record_hash, str(row['bytes'])]
                or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']):
            raise RuntimeError('Installed signed RDKit file differs: ' + row['path'])
        expected.add(row['path'])
        if path.suffix.lower() in ('.dll', '.pyd'):
            native.append(row)
    actual = {p.relative_to(site).as_posix() for p in (site/'rdkit').rglob('*')
              if p.is_file() and p.suffix != '.pyc' and '__pycache__' not in p.parts}
    if actual != {name for name in expected if name.startswith('rdkit/')}:
        raise RuntimeError('Unexpected signed RDKit package files')
    if len(native) != pin['nativeCount']:
        raise RuntimeError('Incomplete signed RDKit native inventory')
    return dict(pin=pin, provenance=provenance, nativeFiles=native)


def bundled_evidence(bundle, proof):
    bundle = Path(bundle)
    native = []
    for row in proof['nativeFiles']:
        path = bundle/'_internal'/row['path']
        if sha(path) != row['sha256']:
            raise RuntimeError('Bundled signed RDKit native bytes differ: ' + row['path'])
        native.append({'path': path.relative_to(bundle).as_posix(), 'sha256': row['sha256']})
    provenance = proof['provenance']; version = proof['pin']['version']
    info = 'rdkit-'+version+'.dist-info'
    if (bundle/'_internal'/info/'direct_url.json').exists():
        raise RuntimeError('Private local installation metadata must not be bundled')
    copied = bundle/'_internal'/info/'nivram-provenance.json'
    if sha(copied) != proof['pin']['provenanceSha256']:
        raise RuntimeError('Bundled signed RDKit provenance is missing or changed')
    for row in provenance['files']:
        if row['path'].startswith(info+'/licenses/') or row['path'] == 'rdkit/_nivram_native/manifest.json':
            if sha(bundle/'_internal'/row['path']) != row['sha256']:
                raise RuntimeError('Bundled signed RDKit notice or native manifest differs')
    destination = bundle/'licenses/rdkit/nivram-provenance.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(copied, destination)
    return dict(origin=provenance['origin'], version=version, wheelSha256=proof['pin']['wheelSha256'],
        provenanceSha256=proof['pin']['provenanceSha256'], nativeFiles=native,
        upstreamPackages=provenance['upstreamPackages'], redistributionQualified=False,
        limitations=provenance['limitations'])


def components(proof):
    """The local adaptation is generic provenance, never mislabelled as PyPI."""
    version = proof['pin']['version']
    reference = 'pkg:generic/nivram-rdkit@'+quote(version, safe='')
    result = [dict(type='library', name='rdkit (nivraM local adaptation)', version=version,
        **{'bom-ref': reference}, purl=reference,
        properties=[dict(name='nivram:origin', value='local-adaptation-of-anaconda-artifacts'),
            dict(name='nivram:wheel-sha256', value=proof['pin']['wheelSha256']),
            dict(name='nivram:redistribution-clearance', value='unmet')])]
    dependencies = []
    for package in proof['provenance']['upstreamPackages']:
        info = package['metadata']; ref = 'nivram:anaconda:'+package['filename']
        dependencies.append(ref)
        result.append(dict(type='library', name=info['name'], version=info['version'],
            **{'bom-ref': ref}, licenses=[{'license': {'name': info['license']}}],
            externalReferences=[dict(type='distribution', url=package['url'],
                hashes=[{'alg': 'SHA-256', 'content': package['sha256']}])],
            properties=[dict(name='nivram:conda-build', value=info['build']),
                dict(name='nivram:inventory-scope', value='selected files from pinned upstream archive')]))
    return reference, result, {'ref': reference, 'dependsOn': dependencies}
