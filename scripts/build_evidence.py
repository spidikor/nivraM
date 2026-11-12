"""Record source identity, installed package provenance, and bundled notices."""
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import shutil
import sys
import uuid

EXCLUDED = {'.git', '__pycache__', 'build', 'dist', 'verification', '.pytest_cache'}
FONTS = {'.ttf', '.otf', '.ttc', '.otc', '.woff', '.woff2', '.eot'}
BROWSER_ASSET = 'chart.umd.min.js'
BROWSER_PINS = {
    BROWSER_ASSET: '48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a',
    'Chart.js-LICENSE.md': '41a84aa2caba645f966a18d9c2056b73e6d3a81d80bc0046bc0011a2634d4cce',
    'kurkle-color-LICENSE.md': '2859c50313bad2ba77b081410c477beaf92b60ca13a77a248d89859a6dd6ac81',
    'chartjs-stage-receipt.json': '6afde99144f02526919a11d58c5f8cf7652c084e436da63d71525b21b9c82e36',
    'color-stage-receipt.json': 'd4e57becef487cf02a91ce830d2749e6c817e269f4b8ce7e9dc72ee6c963d8f8',
}
BROWSER_COLOR_SOURCE_SHA256 = 'fbbc5d795f48a3ce6cafd17c75e0e1239d16de919b83e31150cedc8770aa5427'


def write_browser_dependency_evidence(root, bundle):
    """Require reviewed browser bytes in source and actual bundle before inventory."""
    root, bundle = Path(root), Path(bundle)
    source_vendor = root / 'public/vendor'
    bundled_vendor = bundle / '_internal/public/vendor'
    for filename, expected in BROWSER_PINS.items():
        for directory in (source_vendor, bundled_vendor):
            path = directory / filename
            if not path.is_file() or path.is_symlink() or sha256(path) != expected:
                raise RuntimeError('Browser dependency is missing, redirected, or differs from its reviewed pin: ' + str(path))
    chart = json.loads((source_vendor / 'chartjs-stage-receipt.json').read_text(encoding='utf-8'))
    color = json.loads((source_vendor / 'color-stage-receipt.json').read_text(encoding='utf-8'))
    if ((chart['name'], chart['version']) != ('chart.js', '4.5.1')
            or (color['name'], color['version']) != ('@kurkle/color', '0.3.2')
            or chart['integrity_verified'] is not True or color['integrity_verified'] is not True
            or color['embedded_identity']['source_sha256'] != BROWSER_COLOR_SOURCE_SHA256
            or color['embedded_identity']['source_byte_equality'] is not True):
        raise RuntimeError('Browser package identity or embedded-source correspondence is inconsistent.')
    artifact_path = (bundled_vendor / BROWSER_ASSET).relative_to(bundle).as_posix()
    components, records = [], []
    chart_ref, color_ref = 'pkg:npm/chart.js@4.5.1', 'pkg:npm/%40kurkle/color@0.3.2'
    for record, reference, legal_name, folder, receipt_name in (
            (chart, chart_ref, 'Chart.js-LICENSE.md', 'chart.js', 'chartjs-stage-receipt.json'),
            (color, color_ref, 'kurkle-color-LICENSE.md', 'kurkle-color', 'color-stage-receipt.json')):
        legal_destination = bundle / 'licenses/browser' / folder / 'LICENSE.md'
        legal_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_vendor / legal_name, legal_destination)
        receipt_destination = bundle / 'licenses/browser' / receipt_name
        shutil.copyfile(source_vendor / receipt_name, receipt_destination)
        legal_path = legal_destination.relative_to(bundle).as_posix()
        receipt_path = receipt_destination.relative_to(bundle).as_posix()
        embedded = record['name'] == '@kurkle/color'
        archive = next(item for item in record['artifacts'] if item['path'].endswith('.tgz'))
        component = {
            'type': 'library', 'name': record['name'], 'version': record['version'],
            'bom-ref': reference, 'purl': reference, 'licenses': [{'license': {'id': 'MIT'}}],
            'externalReferences': [{'type': 'distribution', 'url': record['artifact_url'],
                                    'hashes': [{'alg': 'SHA-256', 'content': archive['sha256']}]}],
            'properties': [
                {'name': 'nivram:inventory-scope', 'value': 'embedded-browser-library' if embedded else 'bundled-browser-asset'},
                {'name': 'nivram:bundled-asset-path', 'value': artifact_path},
                {'name': 'nivram:bundled-asset-sha256', 'value': BROWSER_PINS[BROWSER_ASSET]},
                {'name': 'nivram:license-path', 'value': legal_path},
                {'name': 'nivram:provenance-path', 'value': receipt_path},
            ],
        }
        if embedded:
            component['properties'].extend([
                {'name': 'nivram:embedded-in', 'value': chart_ref},
                {'name': 'nivram:embedded-source-sha256', 'value': BROWSER_COLOR_SOURCE_SHA256},
            ])
        else:
            component['hashes'] = [{'alg': 'SHA-256', 'content': BROWSER_PINS[BROWSER_ASSET]}]
        components.append(component)
        records.append({
            'name': record['name'], 'version': record['version'], 'purl': reference, 'license': 'MIT',
            'embeddedIn': chart_ref if embedded else None,
            'licenseFile': {'path': legal_path, 'sha256': sha256(legal_destination)},
            'provenanceFile': {'path': receipt_path, 'sha256': sha256(receipt_destination)},
            'archive': {'url': record['artifact_url'], 'sha256': archive['sha256'],
                        'registryIntegrity': record['published_integrity']},
            'embeddedSourceCorrespondence': record['embedded_identity'] if embedded else None,
        })
    browser = {
        'asset': {'path': artifact_path, 'bytes': (bundled_vendor / BROWSER_ASSET).stat().st_size,
                  'sha256': sha256(bundled_vendor / BROWSER_ASSET)},
        'components': records,
        'scope': 'Exact local browser asset present in this bundle, including its embedded color library; no CDN runtime dependency.',
        'limitations': ['Registry integrity and source correspondence were verified during staging; this build checks the reviewed exact bytes.',
                        'No independent minifier rebuild, formal equivalence proof, or complete supply-chain/security clearance is claimed.'],
    }
    dependencies = [{'ref': chart_ref, 'dependsOn': [color_ref]}, {'ref': color_ref, 'dependsOn': []}]
    return components, dependencies, browser


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(root):
    rows = []
    for path in sorted(Path(root).rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in EXCLUDED for part in relative.parts):
            continue
        rows.append({'path': relative.as_posix(), 'sha256': sha256(path), 'bytes': path.stat().st_size})
    encoded = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    return {'sha256': hashlib.sha256(encoded).hexdigest(), 'files': rows}


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def verify_lock(root):
    expected = {}
    for line in (Path(root) / 'requirements-windows-lock.txt').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            name, version = line.split('==')
            expected[normalized(name)] = version
    actual = {normalized(d.metadata['Name']): d.version for d in metadata.distributions()}
    if actual != expected:
        differences = sorted(name for name in actual.keys() | expected.keys() if actual.get(name) != expected.get(name))
        raise RuntimeError('Build environment differs from the checked lock: ' + ', '.join(differences))


def package_origin(distribution, signed_rdkit=None):
    """Keep artifact identity without exporting the local wheel-installation path."""
    if normalized(distribution.metadata['Name']) == 'rdkit' and '+nivram.' in distribution.version:
        if signed_rdkit is None or distribution.version != signed_rdkit['pin']['version']:
            raise RuntimeError('Local RDKit provenance must be verified before reporting its origin.')
        return {'directUrl': None, 'localWheel': {
            'filename': signed_rdkit['pin']['wheelFilename'],
            'sha256': signed_rdkit['pin']['wheelSha256'], 'installationLocationOmitted': True}}
    return {'directUrl': distribution.read_text('direct_url.json')}


def write_dependency_evidence(root, bundle, source, signed_rdkit=None):
    """Inventory all build packages; native transitive-license review is explicit."""
    root, bundle = Path(root), Path(bundle)
    notices = bundle / 'licenses'
    notices.mkdir(exist_ok=True)
    components, browser_relationships, browser_provenance = write_browser_dependency_evidence(root, bundle)
    provenance = []
    signed_runtime = None
    signed_components = None
    if signed_rdkit is not None:
        from signed_rdkit_evidence import bundled_evidence, components as signed_components_for
        signed_runtime = bundled_evidence(bundle, signed_rdkit)
        signed_reference, signed_components, signed_relationship = signed_components_for(signed_rdkit)
        browser_relationships.append(signed_relationship)
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name, version = distribution.metadata['Name'], distribution.version
        safe_name = normalized(name)
        license_paths = []
        for member in distribution.files or []:
            member_path = Path(str(member))
            if not any(token in member_path.name.lower() for token in ('license', 'licence', 'copying', 'notice')):
                continue
            if not any(part.endswith(('.dist-info', '.egg-info')) for part in member_path.parts):
                continue
            source_path = Path(distribution.locate_file(member))
            if not source_path.is_file():
                continue
            destination = notices / safe_name / member_path.name
            if destination.exists() and sha256(destination) != sha256(source_path):
                destination = destination.with_name(sha256(source_path)[:12] + '-' + destination.name)
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(source_path, destination)
            license_paths.append({'path': destination.relative_to(bundle).as_posix(), 'sha256': sha256(destination)})
        component = {'type': 'library', 'name': name, 'version': version,
                     'bom-ref': f'pkg:pypi/{safe_name}@{version}', 'purl': f'pkg:pypi/{safe_name}@{version}',
                     'properties': [{'name': 'nivram:inventory-scope', 'value': 'pinned-build-environment; inclusion in runtime must be cross-checked against bundled files'}]}
        if safe_name == 'rdkit' and '+nivram.' in version:
            if signed_components is None or version != signed_rdkit['pin']['version']:
                raise RuntimeError('Local signed RDKit adaptation lacks verified upstream provenance.')
            component = signed_components[0]
            components.extend(signed_components[1:])
        expression = distribution.metadata.get('License-Expression')
        if expression:
            component['licenses'] = [{'expression': expression}]
        components.append(component)
        provenance.append({'name': name, 'version': version, 'licenseExpression': expression,
                           'declaredLicense': distribution.metadata.get('License'),
                           'projectUrls': distribution.metadata.get_all('Project-URL', []),
                           'licenseFiles': license_paths,
                           **package_origin(distribution, signed_rdkit)})
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license, notices / 'PYTHON-LICENSE.txt')
    for filename in ('LICENSE.txt', 'THIRD-PARTY-NOTICES.md'):
        shutil.copyfile(root / filename, bundle / filename)
    sdk_license = root / 'licenses' / 'WebView2-SDK-LICENSE.txt'
    sdk_provenance = root / 'licenses' / 'WebView2-SDK-provenance.json'
    if not sdk_license.is_file() or not sdk_provenance.is_file():
        raise RuntimeError('The pinned WebView2 SDK license and provenance are required.')
    sdk_record = json.loads(sdk_provenance.read_text(encoding='utf-8'))
    if sdk_record['licenseSha256'] != sha256(sdk_license):
        raise RuntimeError('WebView2 SDK license identity does not match its recorded source.')
    shutil.copyfile(sdk_license, notices / sdk_license.name)
    shutil.copyfile(sdk_provenance, notices / sdk_provenance.name)
    components.append({'type': 'library', 'name': sdk_record['package'], 'version': sdk_record['version'],
                       'bom-ref': 'pkg:nuget/Microsoft.Web.WebView2@' + sdk_record['version'],
                       'purl': 'pkg:nuget/Microsoft.Web.WebView2@' + sdk_record['version'],
                       'licenses': [{'license': {'id': 'BSD-3-Clause'}}]})
    from nivram.naming_runtime import discover_naming_runtime
    naming_root = bundle / '_internal' / 'naming-runtime'
    naming_runtime = discover_naming_runtime(naming_root)
    if naming_runtime.status != 'available':
        raise RuntimeError('The copied naming runtime failed validation before evidence generation.')
    naming_manifest = json.loads((naming_root / 'manifest.json').read_text(encoding='utf-8'))
    runtime_provenance = {
        'manifestSha256': sha256(naming_root / 'manifest.json'),
        'java': {'name': 'Eclipse Temurin JRE', 'version': naming_manifest['javaVersion'],
                 'vendor': naming_manifest['javaVendor'], 'archiveSha256': naming_manifest['javaArchiveSha256'],
                 'upstream': 'https://github.com/adoptium/temurin25-binaries/releases/tag/jdk-25.0.4.1%2B1',
                 'notices': '_internal/naming-runtime/jre/legal',
                 'sourceDistributionStatus': 'public redistribution clearance unmet; font-bearing source archives excluded'},
        'opsin': {'name': 'OPSIN', 'version': naming_manifest['opsinVersion'],
                  'jarSha256': sha256(naming_root / 'opsin.jar'),
                  'upstream': 'https://github.com/dan2097/opsin/releases/tag/2.9.0',
                  'license': '_internal/naming-runtime/legal/OPSIN-LICENSE.txt'},
        'limitation': 'Runtime and JAR inventories do not by themselves establish complete transitive licensing clearance.'}
    components.extend([
        {'type': 'framework', 'name': 'Eclipse Temurin JRE', 'version': naming_manifest['javaVersion'],
         'bom-ref': 'nivram:temurin:' + naming_manifest['javaVersion'],
         'licenses': [{'expression': 'GPL-2.0-only WITH Classpath-exception-2.0'}],
         'properties': [{'name': 'nivram:runtime-manifest-sha256', 'value': runtime_provenance['manifestSha256']},
                        {'name': 'nivram:release-clearance', 'value': 'unmet; see dependency-provenance.json'}]},
        {'type': 'library', 'name': 'OPSIN', 'version': naming_manifest['opsinVersion'],
         'bom-ref': 'nivram:opsin:' + naming_manifest['opsinVersion'], 'licenses': [{'expression': 'MIT'}],
         'hashes': [{'alg': 'SHA-256', 'content': runtime_provenance['opsin']['jarSha256']}]},
    ])
    native_files = [{'path': p.relative_to(bundle).as_posix(), 'sha256': sha256(p)}
                    for p in sorted(bundle.rglob('*')) if p.is_file() and p.suffix.lower() in ('.exe', '.dll', '.pyd')]
    inventory = {'schemaVersion': 1, 'sourceSha256': source['sha256'], 'packages': provenance,
                 'nativeFiles': native_files, 'namingRuntime': runtime_provenance,
                 'browserDependencies': browser_provenance,
                 'signedRdkit': signed_runtime,
                 'limitations': ['This package inventory covers the build environment, including test/build tools.',
                                 'Native/transitive components and model licenses require a separate release review; this inventory alone is not license clearance.']}
    (bundle / 'dependency-provenance.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    bom = {'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'serialNumber': 'urn:uuid:' + str(uuid.uuid4()), 'version': 1,
           'metadata': {'timestamp': datetime.now(timezone.utc).isoformat(),
                        'component': {'type': 'application', 'name': 'nivraM',
                                      'properties': [{'name': 'nivram:source-sha256', 'value': source['sha256']},
                                                     {'name': 'nivram:scope', 'value': 'build-environment plus verified bundled-browser inventory; incomplete runtime transitive dependency graph'}]}},
           'components': components, 'dependencies': browser_relationships}
    (bundle / 'build-environment.cdx.json').write_text(json.dumps(bom, indent=2), encoding='utf-8')
