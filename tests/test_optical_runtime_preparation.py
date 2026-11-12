"""Local runtime preparation transactions; no ML libraries or models are loaded."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('optical_runtime_preparation', ROOT / 'scripts/prepare_optical_runtime.py')
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
ORIGINALS = {'molscribe/interface.py': b'''import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg

class MolScribe:
    def predict_image(self, image):
        return image

    def draw_prediction(self, prediction, image, notebook=False):
        if "atoms" not in prediction or "bonds" not in prediction:
            return
        h, w, _ = image.shape
        fig, ax = plt.subplots(1, 1)
        return FigureCanvasAgg(fig)
''', 'molscribe/dataset.py': b'''import pandas as pd

def get_transforms(input_size, augment=False):
    return (input_size, augment)

class TrainDataset:
    def __init__(self, args):
        if args.labelled:
            if args.from_file:
                self.coords_df = pd.read_csv(args.coords_file)
''', 'albumentations/augmentations/domain_adaptation.py': b'''from qudida import DomainAdapter
from sklearn.decomposition import PCA
from sklearn.preprocessing import MinMaxScaler, StandardScaler

def adapt_pixel_distribution(img, ref, transform_type="pca", weight=0.5):
    initial_type = img.dtype
    transformer = {"pca": PCA, "standard": StandardScaler, "minmax": MinMaxScaler}[transform_type]()
    adapter = DomainAdapter(transformer=transformer, ref_img=ref)
    return adapter(img).astype(initial_type)
'''}


def fixture_transformation(name, original):
    change = helper.PATCHES[name]
    for statement in change['imports']:
        original = original.replace(statement, b'')
    return original.replace(change['anchor'], b''.join(change['indent'] + s for s in change['imports']) + b'\n' + change['anchor'])


PREPARED = {name: fixture_transformation(name, source) for name, source in ORIGINALS.items()}


class OpticalRuntimePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='nivram-runtime-preparation-')
        self.addCleanup(self.temporary.cleanup)
        self.runtime = Path(self.temporary.name) / 'venv'
        site = self.runtime / 'Lib/site-packages'
        self.targets = {name: site / name for name in ORIGINALS}
        for name, path in self.targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(ORIGINALS[name])
        self.target = self.targets['molscribe/interface.py']
        (self.runtime / 'pyvenv.cfg').write_text('synthetic fixture only\n')
        distributions = {}
        for distribution, info in helper.DISTRIBUTIONS.items():
            metadata = site / distribution
            metadata.mkdir()
            sources = {name: data for name, data in ORIGINALS.items() if name.startswith(info['package'] + '/')}
            record = ''.join(f'{name},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")},{len(data)}\n'
                             for name, data in sources.items()).encode()
            payloads = {name: b'synthetic fixture\n' for name in info['metadata']}
            payloads['RECORD'] = record
            for name, payload in payloads.items():
                (metadata / name).write_bytes(payload)
            distributions[distribution] = dict(info, pythonFiles=len(sources), metadata={name: helper.digest(data) for name, data in payloads.items()})
        self.metadata = site / 'molscribe-1.1.1.dist-info'
        patches = {name: dict(change, before=helper.digest(ORIGINALS[name]), after=helper.digest(PREPARED[name])) for name, change in helper.PATCHES.items()}
        for name, value in {'PATCHES': patches, 'DISTRIBUTIONS': distributions}.items():
            patcher = patch.object(helper, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_apply_idempotence_and_exact_rollback_preserve_install_record(self):
        record_before = (self.metadata / 'RECORD').read_bytes()
        result = helper.prepare(self.runtime)
        self.assertTrue(result['changed'])
        self.assertEqual({name: path.read_bytes() for name, path in self.targets.items()}, PREPARED)
        receipt_before = Path(result['receipt']).read_bytes()
        self.assertFalse(helper.prepare(self.runtime)['changed'])
        self.assertEqual(Path(result['receipt']).read_bytes(), receipt_before)
        self.assertTrue(helper.prepare(self.runtime, rollback=True)['changed'])
        self.assertEqual({name: path.read_bytes() for name, path in self.targets.items()}, ORIGINALS)
        self.assertFalse(helper.prepare(self.runtime, rollback=True)['changed'])
        self.assertEqual((self.metadata / 'RECORD').read_bytes(), record_before)

    def test_drawing_imports_remain_local_and_inference_ast_is_unchanged(self):
        name = 'molscribe/interface.py'
        candidate = helper.transformed(name, ORIGINALS[name])
        before, after = ast.parse(ORIGINALS[name]), ast.parse(candidate)
        before_predict = before.body[-1].body[0]
        after_predict = after.body[-1].body[0]
        self.assertEqual(ast.dump(before_predict), ast.dump(after_predict))
        self.assertFalse(any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in after.body))
        method = helper.function_scope(after, ('MolScribe', 'draw_prediction'))
        self.assertIsInstance(method.body[0], ast.If)
        self.assertIsInstance(method.body[1], ast.Import)
        self.assertEqual(method.body[1].names[0].name, 'matplotlib.pyplot')
        self.assertEqual(method.body[2].module, 'matplotlib.backends.backend_agg')
        name = 'molscribe/dataset.py'
        before, after = ast.parse(ORIGINALS[name]), ast.parse(helper.transformed(name, ORIGINALS[name]))
        self.assertEqual(ast.dump(helper.function_scope(before, ('get_transforms',))),
                         ast.dump(helper.function_scope(after, ('get_transforms',))))
        for name in ORIGINALS:
            helper.transformed(name, ORIGINALS[name])  # All non-import AST nodes must match.

    def test_source_or_metadata_drift_rejects_before_backup_or_write(self):
        for path in (self.target, self.metadata / 'METADATA'):
            original = path.read_bytes()
            path.write_bytes(original + b'\n# drift\n')
            with self.assertRaises(RuntimeError):
                helper.prepare(self.runtime)
            self.assertEqual(path.read_bytes(), original + b'\n# drift\n')
            self.assertFalse((self.runtime / 'nivram-runtime-preparation').exists())
            path.write_bytes(original)

    def test_rollback_refuses_modified_source_and_modified_backup(self):
        result = helper.prepare(self.runtime)
        expected = PREPARED['molscribe/interface.py']
        self.target.write_bytes(expected + b'\n# user edit\n')
        with self.assertRaises(RuntimeError):
            helper.prepare(self.runtime, rollback=True)
        self.assertEqual(self.target.read_bytes(), expected + b'\n# user edit\n')
        self.target.write_bytes(expected)
        backup = Path(result['receipt']).with_name('original-1.py')
        backup.write_bytes(b'changed backup')
        with self.assertRaisesRegex(RuntimeError, 'backup changed'):
            helper.prepare(self.runtime, rollback=True)
        self.assertEqual(self.target.read_bytes(), expected)

    def test_completed_replace_with_interrupted_receipt_is_recoverable(self):
        real_save = helper.save_receipt
        def interrupted(path, receipt, state):
            if state == 'applied':
                raise OSError('simulated receipt interruption')
            return real_save(path, receipt, state)
        with patch.object(helper, 'save_receipt', side_effect=interrupted):
            with self.assertRaises(OSError):
                helper.prepare(self.runtime)
        self.assertEqual({name: path.read_bytes() for name, path in self.targets.items()}, PREPARED)
        result = helper.prepare(self.runtime)
        self.assertFalse(result['changed'])
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(json.loads(Path(result['receipt']).read_text())['state'], 'applied')

    def test_reparse_object_and_parent_escape_are_rejected(self):
        original_lstat = Path.lstat
        target = self.target
        def reparse_lstat(path):
            result = original_lstat(path)
            if path == target:
                class Reparse:
                    st_mode = result.st_mode
                    st_file_attributes = 0x400
                return Reparse()
            return result
        with patch.object(Path, 'lstat', reparse_lstat):
            with self.assertRaisesRegex(RuntimeError, 'reparse'):
                helper.prepare(self.runtime)
        with self.assertRaisesRegex(RuntimeError, 'bounded relative'):
            helper.child(self.runtime, '../outside.py')
        self.assertEqual(self.target.read_bytes(), ORIGINALS['molscribe/interface.py'])

    def test_interrupted_multi_file_apply_can_restore_mixed_state(self):
        real_publish = helper.publish
        second = self.targets['molscribe/dataset.py']
        def interrupted(path, data):
            if path == second:
                raise OSError('simulated second-file interruption')
            return real_publish(path, data)
        with patch.object(helper, 'publish', side_effect=interrupted):
            with self.assertRaises(OSError):
                helper.prepare(self.runtime)
        self.assertEqual(self.target.read_bytes(), PREPARED['molscribe/interface.py'])
        self.assertEqual(second.read_bytes(), ORIGINALS['molscribe/dataset.py'])
        result = helper.prepare(self.runtime, rollback=True)
        self.assertEqual(result['state'], 'rolled-back')
        self.assertEqual({name: path.read_bytes() for name, path in self.targets.items()}, ORIGINALS)


if __name__ == '__main__':
    unittest.main()
