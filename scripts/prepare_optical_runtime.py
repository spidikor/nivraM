"""Explicit, reversible preparation of the pinned external optical runtime.

Defers drawing and unused training imports in three fixed files. No dependency imports, model
loading, inference, downloads, or application configuration occur here. The
original wheel RECORD is retained; the local modification has its own receipt.
"""
from __future__ import annotations

import argparse
import ast
import base64
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

PATCH_ID = 'optical-runtime-lazy-optional-imports-v1'
DISTRIBUTIONS = {
 'molscribe-1.1.1.dist-info': {'package': 'molscribe', 'pythonFiles': 23, 'metadata': {
    'METADATA': '3255440c1d3634ef5c34dc796c9083bd04f81a82f08145e8dff951a85fb07185',
    'direct_url.json': 'ee5c0790539f391f58f53e16d99811e6c2f3444979d866e5d140569bc95c2786',
    'RECORD': '4c3fc61e606f5d8db15a1ce093640692a3c34286bc40b793fa55dd8c1d731131',
 }},
 'albumentations-1.1.0.dist-info': {'package': 'albumentations', 'pythonFiles': 28, 'metadata': {
    'METADATA': '5ed11565063de7b32cf86d28ebec624d1e2207b7a5efff47ac16323922643395',
    'RECORD': 'f06e62ee6ec07979e420e88f7e41ab35c9cdbd0658aa7d482e2f90333509267e',
 }},
}
UPSTREAM_COMMIT = '7296a30413eb55436702011efdff78131f66d162'
UPSTREAM_ARCHIVE_SHA256 = '88fca9e652933943cf107648e266905fd23c87be9371a1309f90e254a47cd60e'
PATCHES = {
 'molscribe/interface.py': {
    'before': '3b81bcd29473e1e05e69964abebfd36d33313b7ff49f02d3f4b033634555b10f',
    'after': '6e06fb75b535a82b7d2d7dd5e31c89c60783ee438514a7ff9bec7076eded89d8',
    'imports': (b'import matplotlib.pyplot as plt\n', b'from matplotlib.backends.backend_agg import FigureCanvasAgg\n'),
    'anchor': b'        h, w, _ = image.shape\n', 'indent': b'        ', 'scope': ('MolScribe', 'draw_prediction'),
 },
 'molscribe/dataset.py': {
    'before': '2783ca47c729c67065032f8f58710d9bc747c60ef62933d5abdb0390d99a51c9',
    'after': 'a0f9e864e5be35e6c85a15b250f945dfeac5a1b0dd60568b0eb656a906a26b5c',
    'imports': (b'import pandas as pd\n',),
    'anchor': b'                self.coords_df = pd.read_csv(args.coords_file)\n',
    'indent': b'                ', 'scope': ('TrainDataset', '__init__'),
 },
 'albumentations/augmentations/domain_adaptation.py': {
    'before': '289220740f27f0676096e92164a8dfade8459861e2d79afeda9e76ba317c8394',
    'after': '6b861bd327c8528eae798582ef5ceb927443e78aa32a1f74864cca51f8b422c3',
    'imports': (b'from qudida import DomainAdapter\n', b'from sklearn.decomposition import PCA\n',
                b'from sklearn.preprocessing import MinMaxScaler, StandardScaler\n'),
    'anchor': b'    initial_type = img.dtype\n', 'indent': b'    ', 'scope': ('adapt_pixel_distribution',),
 },
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_path(path):
    """Reject links/junctions in the full path before resolving or writing it."""
    path = Path(os.path.abspath(path))
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'Links and reparse points are not allowed: ' + str(candidate))
    return path


def child(root, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and not relative.drive and '..' not in relative.parts,
            'A package path is not a bounded relative path.')
    result = checked_path(root / relative)
    require(result.is_relative_to(root), 'A package path escapes its selected runtime.')
    return result


def function_scope(tree, names):
    for name in names:
        nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == name]
        require(len(nodes) == 1, 'Optional-import function scope changed.')
        tree = nodes[0]
    return tree


def transformed(name, original):
    change = PATCHES[name]
    require(digest(original) == change['before'], 'Optional-import source differs from its exact pin: ' + name)
    candidate = original
    for statement in change['imports']:
        require(candidate.count(statement) == 1, 'Optional-import statement changed.')
        candidate = candidate.replace(statement, b'', 1)
    require(candidate.count(change['anchor']) == 1, 'Optional-import insertion anchor changed.')
    local = b''.join(change['indent'] + statement for statement in change['imports']) + b'\n'
    candidate = candidate.replace(change['anchor'], local + change['anchor'], 1)
    require(digest(candidate) == change['after'], 'Prepared source differs from its exact output pin.')
    before, after = ast.parse(original), ast.parse(candidate)
    signatures = {ast.dump(n, include_attributes=False) for n in ast.parse(b''.join(change['imports'])).body}
    removed = [n for n in before.body if ast.dump(n, include_attributes=False) in signatures]
    scope = function_scope(after, change['scope'])
    inserted = [n for n in ast.walk(scope) if ast.dump(n, include_attributes=False) in signatures]
    require(len(removed) == len(inserted) == len(change['imports']), 'Optional imports were not moved exactly once.')
    before.body = [n for n in before.body if n not in removed]
    for node in ast.walk(scope):
        for field in ('body', 'orelse', 'finalbody'):
            if isinstance(getattr(node, field, None), list):
                setattr(node, field, [n for n in getattr(node, field) if n not in inserted])
    require(ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False),
            'Preparation changed computation beyond the fixed optional imports.')
    compile(candidate, name, 'exec')
    return candidate


def inspect_runtime(runtime_root):
    runtime_root = checked_path(runtime_root)
    require(child(runtime_root, 'pyvenv.cfg').is_file(), 'Select an existing isolated Python virtual environment.')
    site = child(runtime_root, 'Lib/site-packages')
    for distribution, info in DISTRIBUTIONS.items():
        package, metadata = child(site, info['package']), child(site, distribution)
        for name, expected in info['metadata'].items():
            require(digest(child(metadata, name).read_bytes()) == expected, 'Installed distribution provenance differs: ' + name)
        recorded = {}
        for name, encoded, size in csv.reader(child(metadata, 'RECORD').read_text(encoding='utf-8').splitlines()):
            if name.startswith(info['package'] + '/') and name.endswith('.py'):
                require(name not in recorded and encoded.startswith('sha256='), 'Invalid package source RECORD.')
                recorded[name] = (encoded, int(size))
        observed = set()
        for index, path in enumerate(package.rglob('*')):
            require(index < 4096, 'Package inventory exceeds its bounded preparation scope.')
            checked_path(path)
            if path.is_file() and path.suffix == '.py':
                observed.add(path.relative_to(site).as_posix())
        require(len(recorded) == info['pythonFiles'] and observed == set(recorded), 'Installed Python source inventory differs.')
        for name, (encoded, size) in recorded.items():
            data = child(site, name).read_bytes()
            if name in PATCHES and digest(data) == PATCHES[name]['after']:
                continue
            actual = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            require(encoded == actual and len(data) == size, 'Installed Python source drift: ' + name)
    targets = {name: child(site, name) for name in PATCHES}
    require(all(digest(path.read_bytes()) in {PATCHES[name]['before'], PATCHES[name]['after']} for name, path in targets.items()),
            'Optional-import source has unsupported drift.')
    return runtime_root, targets


def publish(path, data):
    path = checked_path(path)
    temporary = checked_path(path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending'))
    with temporary.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    checked_path(path)
    os.replace(temporary, path)


def save_receipt(path, receipt, state):
    receipt['state'] = state
    receipt['events'].append({'state': state, 'utc': datetime.now(timezone.utc).isoformat()})
    publish(path, (json.dumps(receipt, indent=2) + '\n').encode('utf-8'))


def prepare(runtime_root, *, rollback=False):
    runtime_root, targets = inspect_runtime(runtime_root)
    state_dir = child(runtime_root, 'nivram-runtime-preparation/' + PATCH_ID)
    receipt_path = child(state_dir, 'receipt.json')
    backup_paths = {name: child(state_dir, f'original-{index + 1}.py') for index, name in enumerate(PATCHES)}
    current = {name: path.read_bytes() for name, path in targets.items()}
    pins = {name: {'target': str(targets[name]), 'before': change['before'], 'after': change['after']} for name, change in PATCHES.items()}
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        require(receipt['patchId'] == PATCH_ID and receipt['runtimeRoot'] == str(runtime_root)
                and receipt['files'] == pins
                and receipt['state'] in {'prepared', 'applied', 'rolled-back'}, 'Preparation receipt does not bind this runtime.')
        original = {name: path.read_bytes() for name, path in backup_paths.items()}
        require(all(digest(data) == PATCHES[name]['before'] for name, data in original.items()), 'Original backup changed.')
    else:
        require(not rollback, 'No preparation receipt exists for rollback.')
        require(all(digest(data) == PATCHES[name]['before'] for name, data in current.items()), 'Prepared source has no original backup/receipt.')
        require(not state_dir.exists(), 'Incomplete preparation directory requires inspection.')
        state_dir.mkdir(parents=True, exist_ok=False)
        checked_path(state_dir)
        original = current
        for name, path in backup_paths.items():
            with path.open('xb') as stream:
                stream.write(original[name])
                stream.flush()
                os.fsync(stream.fileno())
        receipt = {'schemaVersion': 1, 'patchId': PATCH_ID, 'runtimeRoot': str(runtime_root), 'files': pins, 'events': [],
                   'distributions': DISTRIBUTIONS,
                   'provisionedUpstreamCommit': UPSTREAM_COMMIT, 'provisionedArchiveSha256': UPSTREAM_ARCHIVE_SHA256,
                   'scope': 'Only drawing, CSV training and pixel adaptation imports moved; all other Python AST nodes unchanged.',
                   'wheelRecord': 'Preserved verbatim as original-install evidence; this receipt records the local modification.'}
        save_receipt(receipt_path, receipt, 'prepared')
    candidate = {name: transformed(name, data) for name, data in original.items()}
    desired, state = (original, 'rolled-back') if rollback else (candidate, 'applied')
    for name, target in targets.items():
        if current[name] != desired[name]:
            inspect_runtime(runtime_root)
            require(target.read_bytes() == current[name], 'Source changed during preparation; no replacement made.')
            publish(target, desired[name])
    require(all(targets[name].read_bytes() == desired[name] and backup_paths[name].read_bytes() == original[name] for name in PATCHES),
            'Prepared/backup bytes did not verify.')
    inspect_runtime(runtime_root)
    if receipt['state'] != state:
        save_receipt(receipt_path, receipt, state)
    return {**receipt, 'receipt': str(receipt_path), 'changed': current != desired,
            'limitations': ['No model, dependency import, inference or native acceptance is performed by this preparation helper.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('apply', 'rollback'))
    parser.add_argument('--runtime-root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.runtime_root, rollback=args.action == 'rollback'), indent=2))


if __name__ == '__main__':
    main()
