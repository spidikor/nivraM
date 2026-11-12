import base64
import builtins
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

from PIL import Image

from nivram import optical_worker as worker


def png_bytes(mode='RGB', size=(3, 2), color='white', **save_options):
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, format='PNG', **save_options)
    return output.getvalue()


def request_bytes(png=None, **changes):
    png = png_bytes() if png is None else png
    payload = {'schemaVersion': 1, 'pngBase64': base64.b64encode(png).decode('ascii'),
               'inputSha256': hashlib.sha256(png).hexdigest()}
    payload.update(changes)
    return json.dumps(payload).encode('utf-8')


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def replace_header(png, **changes):
    keys = ('width', 'height', 'depth', 'color', 'compression', 'filtering', 'interlace')
    values = dict(zip(keys, struct.unpack('>IIBBBBB', png[16:29])))
    values.update(changes)
    return png[:8] + chunk(b'IHDR', struct.pack('>IIBBBBB', *(values[key] for key in keys))) + png[33:]


class OpticalInputTests(unittest.TestCase):
    def test_valid_request_preserves_exact_bytes_and_digest(self):
        png = png_bytes()
        self.assertEqual(worker.read_request(io.BytesIO(request_bytes(png))), (png, hashlib.sha256(png).hexdigest()))

    def test_malformed_shape_duplicate_fields_nonfinite_and_types_reject(self):
        malformed = [b'', b'\xff', b'{', b'[]', b'null', b'{"schemaVersion":1,"schemaVersion":1}',
                     request_bytes(schemaVersion=True), request_bytes(schemaVersion=2),
                     request_bytes(extra='unused'), request_bytes(pngBase64=[]),
                     request_bytes(inputSha256='A' * 64), request_bytes(inputSha256=None),
                     b'{"schemaVersion":NaN,"pngBase64":"YQ==","inputSha256":"' + b'a' * 64 + b'"}']
        for raw in malformed:
            with self.subTest(raw=raw[:100]), self.assertRaises(worker.OpticalWorkerError):
                worker.read_request(io.BytesIO(raw))

    def test_input_byte_limit_is_bounded_before_json_decode(self):
        stream = Mock()
        stream.read.return_value = b'x' * 21
        with patch.object(worker, 'MAX_INPUT_BYTES', 20), patch.object(worker.json, 'loads') as loads:
            with self.assertRaises(worker.OpticalWorkerError):
                worker.read_request(stream)
            stream.read.assert_called_once_with(21)
            loads.assert_not_called()

    def test_base64_size_canonical_encoding_and_digest_are_required(self):
        for value in ['%%%%', 'YQ==\n', 'YR==', 'aGVsbG8=', '\u00ff']:
            with self.subTest(value=value), self.assertRaises(worker.OpticalWorkerError):
                worker.read_request(io.BytesIO(request_bytes(pngBase64=value)))
        with patch.object(worker, 'MAX_PNG_BYTES', 5), patch.object(worker.base64, 'b64decode') as decode:
            with self.assertRaises(worker.OpticalWorkerError):
                worker.read_request(io.BytesIO(request_bytes(pngBase64='A' * 12)))
            decode.assert_not_called()
        # Same base64 length as a permitted 5-byte buffer, but decodes to six.
        with patch.object(worker, 'MAX_PNG_BYTES', 5), self.assertRaises(worker.OpticalWorkerError):
            worker.read_request(io.BytesIO(request_bytes(png=b'abcdef')))
        with self.assertRaisesRegex(worker.OpticalWorkerError, 'digest'):
            worker.read_request(io.BytesIO(request_bytes(inputSha256='a' * 64)))

    def test_png_limits_color_interlace_crc_truncation_and_trailing_bytes(self):
        png = png_bytes()
        malformed = [b'not png', png[:-1], png + b'trailing', png[:40] + bytes([png[40] ^ 1]) + png[41:],
                     replace_header(png, width=2049), replace_header(png, width=1001, height=1000),
                     replace_header(png, width=0), replace_header(png, interlace=1),
                     replace_header(png, depth=4), replace_header(png, compression=1),
                     png_bytes('L'), png_bytes('P'), png[:33] + chunk(b'acTL', struct.pack('>II', 1, 0)) + png[33:]]
        for value in malformed:
            with self.subTest(size=len(value)), self.assertRaises(worker.OpticalWorkerError):
                worker.decode_png(value)
        self.assertEqual(worker.decode_png(png_bytes(size=(2048, 1))).size, (2048, 1))
        self.assertEqual(worker.decode_png(png_bytes(size=(1000, 1000))).size, (1000, 1000))

    def test_corrupt_compressed_image_cannot_pass_container_checks(self):
        png = png_bytes()
        corrupt = png[:33] + chunk(b'IDAT', b'not a zlib stream') + chunk(b'IEND', b'')
        self.assertEqual(worker.validate_png(corrupt), (3, 2))
        with self.assertRaises(worker.OpticalWorkerError):
            worker.decode_png(corrupt)

    def test_rgb_rgba_and_rgb_transparency_are_composited_to_white(self):
        rgb = worker.decode_png(png_bytes(color=(12, 34, 56)))
        self.assertEqual((rgb.mode, rgb.getpixel((0, 0))), ('RGB', (12, 34, 56)))
        transparent = worker.decode_png(png_bytes('RGBA', color=(0, 0, 0, 0)))
        self.assertEqual(transparent.getpixel((0, 0)), (255, 255, 255))
        translucent = worker.decode_png(png_bytes('RGBA', color=(0, 0, 0, 128)))
        self.assertEqual(translucent.getpixel((0, 0)), (127, 127, 127))
        key = worker.decode_png(png_bytes(color=(12, 34, 56), transparency=(12, 34, 56)))
        self.assertEqual(key.getpixel((0, 0)), (255, 255, 255))


def fake_runtime(states=None):
    states = states or {'args': {'input_size': 384}, 'encoder': {'module.weight': 'encoder-weight'},
                        'decoder': {'module.layer.module.weight': 'decoder-weight'}}
    torch = SimpleNamespace(load=Mock(return_value=states), set_num_threads=Mock(),
                            set_num_interop_threads=Mock(), device=Mock(return_value='cpu-device'))
    encoder = SimpleNamespace(n_features=128, load_state_dict=Mock(return_value=SimpleNamespace(missing_keys=[], unexpected_keys=[])), to=Mock(), eval=Mock())
    decoder = SimpleNamespace(load_state_dict=Mock(return_value=SimpleNamespace(missing_keys=[], unexpected_keys=[])), to=Mock(), eval=Mock())

    class Base:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError('Upstream permissive constructor must not run')

        def _get_args(self, args):
            return SimpleNamespace(**args)

        def _get_model(self, *_args, **_kwargs):
            raise AssertionError('Upstream permissive model builder must not run')

    interface = SimpleNamespace(MolScribe=Base, get_tokenizer=Mock(return_value='tokenizer'),
                                Encoder=Mock(return_value=encoder), Decoder=Mock(return_value=decoder),
                                get_transforms=Mock(return_value='transform'))
    return torch, interface, encoder, decoder


class OpticalModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic-weights.bin'
        self.data = b'Tiny synthetic model bytes; no pickle'
        self.path.write_bytes(self.data)
        for name, value in [('MODEL_BYTES', len(self.data)), ('MODEL_SHA256', hashlib.sha256(self.data).hexdigest())]:
            patcher = patch.object(worker, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextlib.contextmanager
    def observed_startup(self, *, import_error=None):
        """Hold a real checkpoint hash until the concurrent Torch import starts."""
        torch, interface, *_ = fake_runtime()
        prediction = {'smiles': 'CO', 'molfile': 'synthetic\nM  END\n'}
        interface.MolScribe.predict_image = lambda *_args, **_kwargs: prediction
        hash_started, import_started, hash_finished = (threading.Event() for _ in range(3))
        handles = []
        original_hash, original_import = hashlib.sha256, builtins.__import__

        class PausedHash:
            def __init__(self):
                self.digest = original_hash()

            def update(self, data):
                hash_started.set()
                if not import_started.wait(5):
                    raise AssertionError('Checkpoint hashing ran serially before the Torch import.')
                self.digest.update(data)

            def hexdigest(self):
                hash_finished.set()
                return self.digest.hexdigest()

        def open_model(*args, **kwargs):
            stream = open(*args, **kwargs)
            handles.append(stream)
            return stream

        def import_module(name, *args, **kwargs):
            if name == 'torch':
                self.assertTrue(hash_started.wait(5), 'Hashing must start before the Torch import waits for it.')
                self.assertFalse(hash_finished.is_set())
                import_started.set()
                if import_error is not None:
                    raise import_error
                return torch
            if name in ('molscribe', 'numpy'):
                self.assertTrue(hash_finished.is_set(), 'Only Torch may import before verification completes.')
                return SimpleNamespace(interface=interface) if name == 'molscribe' else SimpleNamespace(asarray=lambda image: image)
            return original_import(name, *args, **kwargs)

        def restricted_load(stream, **options):
            self.assertTrue(hash_finished.is_set())
            self.assertIs(stream, handles[0])
            self.assertFalse(stream.closed)
            self.assertEqual(stream.tell(), 0)
            self.assertEqual(stream.read(), self.data)
            self.assertEqual(options, {'map_location': 'cpu', 'weights_only': True})
            return {'args': {'input_size': 384}, 'encoder': {}, 'decoder': {}}

        torch.load.side_effect = restricted_load
        with patch.object(worker, 'open', side_effect=open_model, create=True), \
                patch.object(worker.hashlib, 'sha256', side_effect=PausedHash), \
                patch('builtins.__import__', side_effect=import_module):
            yield SimpleNamespace(torch=torch, handles=handles, imported=import_started, verified=hash_finished)

    def test_torch_import_overlaps_hashing_but_weights_use_only_the_verified_same_handle(self):
        png = png_bytes()
        with self.observed_startup() as observed:
            result = worker.recognize(png, 'a' * 64, self.path)
        self.assertEqual(result['smiles'], 'CO')
        self.assertTrue(observed.imported.is_set())
        self.assertTrue(observed.verified.is_set())
        self.assertEqual(len(observed.handles), 1)
        self.assertTrue(observed.handles[0].closed)
        observed.torch.load.assert_called_once()

    def test_overlapped_verification_failure_closes_handle_without_loading_weights(self):
        png = png_bytes()
        self.path.write_bytes(b'x' * len(self.data))
        with self.observed_startup() as observed, self.assertRaises(worker.OpticalWorkerError) as caught:
            worker.recognize(png, 'a' * 64, self.path)
        self.assertEqual(caught.exception.code, 'model_mismatch')
        self.assertTrue(observed.imported.is_set())
        self.assertTrue(observed.handles[0].closed)
        observed.torch.load.assert_not_called()

    def test_torch_import_failure_joins_verification_and_closes_its_handle_without_loading(self):
        png = png_bytes()
        with self.observed_startup(import_error=ImportError('synthetic missing Torch')) as observed, \
                self.assertRaisesRegex(ImportError, 'synthetic missing Torch'):
            worker.recognize(png, 'a' * 64, self.path)
        self.assertTrue(observed.verified.is_set())
        self.assertTrue(observed.handles[0].closed)
        observed.torch.load.assert_not_called()

    def test_missing_wrong_size_and_wrong_hash_fail_before_heavy_loader(self):
        png = png_bytes()
        with patch.object(worker, 'load_predictor') as load:
            for data in (None, b'short', b'x' * len(self.data)):
                if data is None:
                    self.path.unlink()
                else:
                    self.path.write_bytes(data)
                with self.subTest(data=data), self.assertRaises(worker.OpticalWorkerError):
                    worker.recognize(png, hashlib.sha256(png).hexdigest(), self.path)
            load.assert_not_called()

    def test_same_verified_handle_is_rewound_for_weights_only_cpu_load(self):
        torch, interface, encoder, decoder = fake_runtime()
        with worker.verified_model(self.path) as stream:
            self.assertEqual(stream.tell(), 0)

            def load(handle, **options):
                self.assertIs(handle, stream)
                self.assertEqual(handle.read(), self.data)
                self.assertEqual(options, {'map_location': 'cpu', 'weights_only': True})
                return {'args': {'input_size': 384}, 'encoder': {'module.weight': 1}, 'decoder': {'module.weight': 2}}

            torch.load.side_effect = load
            predictor = worker.load_predictor(stream, torch_module=torch, interface_module=interface)
        self.assertTrue(stream.closed)
        encoder.load_state_dict.assert_called_once_with({'weight': 1}, strict=True)
        decoder.load_state_dict.assert_called_once_with({'weight': 2}, strict=True)
        self.assertEqual(predictor.num_workers, 1)
        self.assertEqual(predictor.device, 'cpu-device')

    def test_explicit_model_builder_uses_strict_loading_and_leading_prefix_only(self):
        torch, interface, encoder, decoder = fake_runtime()
        with patch.object(worker.os, 'cpu_count', return_value=128):
            predictor = worker.load_predictor(io.BytesIO(b'fake'), torch_module=torch, interface_module=interface)
        torch.set_num_threads.assert_called_once_with(4)
        torch.set_num_interop_threads.assert_called_once_with(1)
        args = interface.Encoder.call_args.args[0]
        self.assertIs(args.no_pretrained, True)
        self.assertEqual(interface.Encoder.call_args.kwargs, {'pretrained': False})
        self.assertEqual(args.encoder_dim, 128)
        interface.Decoder.assert_called_once_with(args, 'tokenizer')
        encoder.load_state_dict.assert_called_once_with({'weight': 'encoder-weight'}, strict=True)
        decoder.load_state_dict.assert_called_once_with({'layer.module.weight': 'decoder-weight'}, strict=True)
        interface.get_transforms.assert_called_once_with(384, augment=False)
        for module in (encoder, decoder):
            module.to.assert_called_once_with('cpu-device')
            module.eval.assert_called_once_with()
        self.assertEqual(predictor.transform, 'transform')

    def test_restricted_load_rejection_never_retries_unsafe_pickle(self):
        torch, interface, *_ = fake_runtime()
        torch.load.side_effect = RuntimeError('PRIVATE_PATH unsafe globals unsupported')
        with self.assertRaisesRegex(worker.OpticalWorkerError, 'restricted weights loader') as caught:
            worker.load_predictor(io.BytesIO(), torch_module=torch, interface_module=interface)
        self.assertNotIn('PRIVATE_PATH', str(caught.exception))
        self.assertEqual(torch.load.call_count, 1)
        self.assertIs(torch.load.call_args.kwargs['weights_only'], True)
        interface.Encoder.assert_not_called()

    def test_parameter_mismatch_and_prefix_collision_fail_closed(self):
        for result in [SimpleNamespace(missing_keys=['required'], unexpected_keys=[]),
                       SimpleNamespace(missing_keys=[], unexpected_keys=['surprise'])]:
            module = SimpleNamespace(load_state_dict=Mock(return_value=result))
            with self.subTest(result=result), self.assertRaises(worker.OpticalWorkerError):
                worker.strict_state(module, {'weight': 1})
        module = SimpleNamespace(load_state_dict=Mock(side_effect=RuntimeError('PRIVATE_MODEL detail')))
        with self.assertRaises(worker.OpticalWorkerError) as caught:
            worker.strict_state(module, {'weight': 1})
        self.assertNotIn('PRIVATE_MODEL', str(caught.exception))
        module.load_state_dict.reset_mock()
        for state in [{'module.weight': 1, 'weight': 2}, {1: 2}, []]:
            with self.subTest(state=state), self.assertRaises(worker.OpticalWorkerError):
                worker.strict_state(module, state)
        module.load_state_dict.assert_not_called()


class OpticalProtocolTests(unittest.TestCase):
    def test_standalone_isolated_entrypoint_reports_malformed_input_and_missing_model(self):
        with tempfile.TemporaryDirectory() as temp:
            private_path = str(Path(temp) / 'PRIVATE_MODEL_MISSING.bin')
            command = [sys.executable, '-I', '-B', str(Path(worker.__file__).resolve()), '--model', private_path]
            for payload, error in [(b'{', 'invalid_input'), (request_bytes(), 'model_unavailable')]:
                with self.subTest(error=error):
                    result = subprocess.run(command, input=payload, capture_output=True, timeout=10, check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(len(result.stdout.splitlines()), 1)
                    self.assertEqual(json.loads(result.stdout)['error']['code'], error)
                    self.assertNotIn(b'PRIVATE_', result.stdout + result.stderr)
                    self.assertNotIn(b'Traceback', result.stdout + result.stderr)

    def test_confidence_and_output_text_are_bounded(self):
        prediction = {'smiles': 'CCO', 'molfile': 'synthetic\nM  END\n'}
        result = worker.validated_prediction(prediction, 'a' * 64)
        self.assertEqual(result, {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': worker.MODEL_SHA256,
                                  'inputSha256': 'a' * 64, **prediction, 'confidence': None})
        self.assertEqual(worker.validated_prediction({**prediction, 'confidence': 1}, 'a' * 64)['confidence'], 1.0)
        for value in [True, '0.5', float('nan'), float('inf'), -0.1, 1.1]:
            with self.subTest(value=value), self.assertRaises(worker.OpticalWorkerError):
                worker.validated_prediction({**prediction, 'confidence': value}, 'a' * 64)
        for changes in [{'smiles': ''}, {'molfile': None}, {'smiles': 'bad\x00text'},
                        {'molfile': '\ud800'}, {'smiles': 'x' * (worker.MAX_SMILES_BYTES + 1)}]:
            with self.subTest(changes=list(changes)), self.assertRaises(worker.OpticalWorkerError):
                worker.validated_prediction({**prediction, **changes}, 'a' * 64)

    def test_python_network_denial_and_offline_environment(self):
        for event in ['socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname',
                      'socket.gethostbyaddr', 'socket.sendto', 'socket.sendmsg']:
            with self.subTest(event=event), self.assertRaises(worker.OpticalWorkerError):
                worker.deny_python_network(event, ('PRIVATE_ADDRESS',))
        worker.deny_python_network('open', ('model',))
        with patch.dict(worker.os.environ, {}, clear=True), patch.object(worker.os, 'cpu_count', return_value=64):
            worker.configure_offline()
            self.assertEqual(worker.os.environ['HF_HUB_OFFLINE'], '1')
            self.assertEqual(worker.os.environ['TRANSFORMERS_OFFLINE'], '1')
            self.assertEqual(worker.os.environ['CUDA_VISIBLE_DEVICES'], '')
            for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
                self.assertEqual(worker.os.environ[key], '4')

    def test_protocol_success_and_errors_are_single_json_without_private_details(self):
        success = worker.validated_prediction({'smiles': 'CCO', 'molfile': 'M  END\n', 'confidence': .75}, 'a' * 64)
        with patch.object(worker.sys, 'addaudithook') as audit, patch.object(worker, 'configure_offline'), \
                patch.object(worker, 'library_output_to_stderr', side_effect=contextlib.nullcontext):
            for outcome, code in [(success, 0), (RuntimeError('PRIVATE_PATH PRIVATE_IMAGE traceback'), 2),
                                  (worker.OpticalWorkerError('model_mismatch', 'The model failed its integrity check.'), 2)]:
                output = io.StringIO()
                with patch.object(worker, 'recognize', side_effect=outcome if isinstance(outcome, Exception) else None,
                                  return_value=outcome):
                    self.assertEqual(worker.main(['--model', 'PRIVATE_PATH'], stdin=io.BytesIO(request_bytes()), stdout=output), code)
                text = output.getvalue()
                self.assertEqual(len(text.splitlines()), 1)
                parsed = json.loads(text)
                self.assertNotIn('PRIVATE_', text)
                self.assertEqual(parsed['schemaVersion'], 1)
                if code == 0:
                    self.assertEqual(parsed, success)
                else:
                    self.assertIn('code', parsed['error'])
            self.assertEqual(audit.call_count, 3)

    def test_cli_arguments_and_output_limit_report_safe_errors(self):
        output = io.StringIO()
        self.assertEqual(worker.main(['--unknown', 'PRIVATE_PATH'], stdin=io.BytesIO(), stdout=output), 2)
        self.assertEqual(json.loads(output.getvalue())['error']['code'], 'invalid_arguments')
        self.assertNotIn('PRIVATE_PATH', output.getvalue())
        with patch.object(worker.sys, 'addaudithook'), patch.object(worker, 'configure_offline'), \
                patch.object(worker, 'library_output_to_stderr', side_effect=contextlib.nullcontext), \
                patch.object(worker, 'recognize', return_value={'text': 'x' * 100}), patch.object(worker, 'MAX_OUTPUT_BYTES', 20):
            output = io.StringIO()
            self.assertEqual(worker.main(['--model', 'model'], stdin=io.BytesIO(request_bytes()), stdout=output), 2)
            self.assertEqual(json.loads(output.getvalue())['error']['code'], 'invalid_prediction')


if __name__ == '__main__':
    unittest.main()
