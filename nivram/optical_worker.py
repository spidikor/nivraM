"""One local, bounded MolScribe inference in a separately owned Python process.

This file is also an entrypoint for ``python -I -B optical_worker.py --model ...``.
Checkpoint hashing overlaps importing Torch; checkpoint loading and MolScribe
imports still require successful verification of the model's exact bytes.
The Python socket audit hook is defense in depth, not OS network isolation.
"""
import argparse
import base64
import contextlib
import hashlib
import io
import json
import math
import numbers
import os
import re
import stat
import struct
import sys
import zlib
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor


MODEL_BYTES = 1_134_173_494
MODEL_SHA256 = '32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501'
MAX_INPUT_BYTES = 6 * 1024 * 1024
MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_EDGE = 2048
MAX_PIXELS = 1_000_000
MAX_SMILES_BYTES = 64 * 1024
MAX_MOLFILE_BYTES = 512 * 1024
MAX_OUTPUT_BYTES = 768 * 1024
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


class OpticalWorkerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def failure(code, message):
    raise OpticalWorkerError(code, message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            failure('invalid_input', 'The optical request contains duplicate fields.')
        result[key] = value
    return result


def read_request(stream):
    raw = stream.read(MAX_INPUT_BYTES + 1)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_INPUT_BYTES:
        failure('invalid_input', 'The optical request exceeds the supported input size.')
    try:
        request = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                             parse_constant=lambda _: failure('invalid_input', 'The optical request is not valid JSON.'))
    except OpticalWorkerError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        failure('invalid_input', 'The optical request is not valid JSON.')
    if not isinstance(request, dict) or set(request) != {'schemaVersion', 'pngBase64', 'inputSha256'}:
        failure('invalid_input', 'The optical request has unsupported fields.')
    if type(request['schemaVersion']) is not int or request['schemaVersion'] != 1:
        failure('invalid_input', 'The optical request version is unsupported.')
    encoded, digest = request['pngBase64'], request['inputSha256']
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        failure('invalid_input', 'The optical request requires an exact image digest.')
    if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_PNG_BYTES + 2) // 3):
        failure('invalid_png', 'The PNG exceeds the supported byte limit.')
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError):
        failure('invalid_png', 'The image encoding is not valid base64.')
    if len(png) > MAX_PNG_BYTES or base64.b64encode(png).decode('ascii') != encoded:
        failure('invalid_png', 'The PNG exceeds its limit or has a noncanonical encoding.')
    if hashlib.sha256(png).hexdigest() != digest:
        failure('input_mismatch', 'The PNG does not match the requested image digest.')
    return png, digest


def validate_png(png):
    """Check the bounded PNG container before asking Pillow to decompress it."""
    if not isinstance(png, bytes) or len(png) > MAX_PNG_BYTES or not png.startswith(PNG_SIGNATURE):
        failure('invalid_png', 'A supported PNG image is required.')
    offset, width, height, depth, color = 8, None, None, None, None
    seen_idat = False
    idat_finished = False
    seen_palette = False
    while offset < len(png):
        if len(png) - offset < 12:
            failure('invalid_png', 'The PNG container is truncated.')
        length = struct.unpack_from('>I', png, offset)[0]
        kind = png[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(png) or not re.fullmatch(b'[A-Za-z]{4}', kind) or kind[2] & 32:
            failure('invalid_png', 'The PNG container has an invalid chunk.')
        chunk = png[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack_from('>I', png, offset + 8 + length)[0]
        if zlib.crc32(kind + chunk) & 0xffffffff != expected_crc:
            failure('invalid_png', 'The PNG container failed its integrity check.')
        if offset == 8:
            if kind != b'IHDR' or length != 13:
                failure('invalid_png', 'The PNG header is invalid.')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunk)
            if not (0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE and width * height <= MAX_PIXELS):
                failure('invalid_png', 'Use an image no larger than 2048 pixels per side and one million pixels.')
            if depth not in (8, 16) or color not in (2, 6) or compression or filtering or interlace:
                failure('invalid_png', 'Optical recognition requires a noninterlaced RGB or RGBA PNG.')
        elif kind == b'IHDR':
            failure('invalid_png', 'The PNG contains a repeated header.')
        if kind in (b'acTL', b'fcTL', b'fdAT'):
            failure('invalid_png', 'Use a single still PNG image.')
        if kind == b'PLTE':
            if seen_palette or seen_idat or not 0 < length <= 768 or length % 3:
                failure('invalid_png', 'The PNG palette chunk is invalid.')
            seen_palette = True
        if kind == b'IDAT':
            if idat_finished:
                failure('invalid_png', 'The PNG image chunks are not consecutive.')
            seen_idat = True
        elif seen_idat:
            idat_finished = True
        if not kind[0] & 32 and kind not in (b'IHDR', b'PLTE', b'IDAT', b'IEND'):
            failure('invalid_png', 'The PNG contains an unsupported required chunk.')
        if kind == b'IEND':
            if length or not seen_idat or end != len(png):
                failure('invalid_png', 'The PNG image end is invalid.')
            return width, height
        offset = end
    failure('invalid_png', 'The PNG image is incomplete.')


def decode_png(png):
    expected_size = validate_png(png)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            if image.format != 'PNG' or image.size != expected_size or image.mode not in ('RGB', 'RGBA'):
                failure('invalid_png', 'The decoded PNG format is unsupported.')
            image.verify()
        with Image.open(io.BytesIO(png)) as image:
            image.load()
            # Transparency, including RGB tRNS, is always composited on white.
            rgba = image.convert('RGBA')
            white = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
            return Image.alpha_composite(white, rgba).convert('RGB')
    except OpticalWorkerError:
        raise
    except Exception:
        failure('invalid_png', 'The PNG could not be decoded completely.')


@contextlib.contextmanager
def verified_model(path):
    """Keep the same handle from hashing through the restricted torch.load."""
    try:
        stream = open(path, 'rb')
    except OSError:
        failure('model_unavailable', 'The local optical model is unavailable.')
    with stream:
        try:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != MODEL_BYTES:
                failure('model_mismatch', 'The local optical model has an unexpected size.')
            digest = hashlib.sha256()
            total = 0
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MODEL_BYTES:
                    failure('model_mismatch', 'The local optical model changed while being checked.')
                digest.update(chunk)
            if total != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
                failure('model_mismatch', 'The local optical model failed its integrity check.')
            stream.seek(0)
        except OSError:
            failure('model_unavailable', 'The local optical model could not be read.')
        yield stream


def configure_offline():
    threads = str(min(4, max(1, os.cpu_count() or 1)))
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      WANDB_MODE='disabled', TOKENIZERS_PARALLELISM='false', CUDA_VISIBLE_DEVICES='',
                      OMP_NUM_THREADS=threads, MKL_NUM_THREADS=threads, OPENBLAS_NUM_THREADS=threads)


def deny_python_network(event, _args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname',
                 'socket.gethostbyaddr', 'socket.sendto', 'socket.sendmsg'}:
        failure('network_disabled', 'Network access is disabled in the local optical worker.')


def strict_state(module, state):
    if not isinstance(state, Mapping):
        failure('model_incompatible', 'The optical model contains invalid parameter data.')
    cleaned = {}
    for key, value in state.items():
        if not isinstance(key, str):
            failure('model_incompatible', 'The optical model contains invalid parameter names.')
        name = key.removeprefix('module.')
        if name in cleaned:
            failure('model_incompatible', 'The optical model has ambiguous parameter names.')
        cleaned[name] = value
    try:
        result = module.load_state_dict(cleaned, strict=True)
        if getattr(result, 'missing_keys', ()) or getattr(result, 'unexpected_keys', ()):
            failure('model_incompatible', 'The optical model parameters do not match this runtime.')
    except OpticalWorkerError:
        raise
    except Exception:
        failure('model_incompatible', 'The optical model parameters do not match this runtime.')


def load_predictor(stream, *, torch_module=None, interface_module=None):
    if torch_module is None:
        import torch as torch_module
    torch_module.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
    torch_module.set_num_interop_threads(1)
    try:
        states = torch_module.load(stream, map_location='cpu', weights_only=True)
    except Exception:
        failure('model_incompatible', 'The model cannot be loaded with the required restricted weights loader.')
    if not isinstance(states, Mapping) or not isinstance(states.get('args'), dict) or not all(key in states for key in ('encoder', 'decoder')):
        failure('model_incompatible', 'The optical checkpoint has an unsupported structure.')
    if interface_module is None:
        from molscribe import interface as interface_module
    try:
        # Bypass upstream __init__/_get_model: both use a permissive loader.
        predictor = interface_module.MolScribe.__new__(interface_module.MolScribe)
        args = predictor._get_args(states['args'])
        args.no_pretrained = True
        predictor.device = torch_module.device('cpu')
        predictor.tokenizer = interface_module.get_tokenizer(args)
        predictor.encoder = interface_module.Encoder(args, pretrained=False)
        args.encoder_dim = predictor.encoder.n_features
        predictor.decoder = interface_module.Decoder(args, predictor.tokenizer)
        strict_state(predictor.encoder, states['encoder'])
        strict_state(predictor.decoder, states['decoder'])
        for module in (predictor.encoder, predictor.decoder):
            module.to(predictor.device)
            module.eval()
        predictor.transform = interface_module.get_transforms(args.input_size, augment=False)
        predictor.num_workers = 1
        return predictor
    except OpticalWorkerError:
        raise
    except Exception:
        failure('model_incompatible', 'The local optical model is incompatible with this runtime.')


def validated_prediction(prediction, input_sha256):
    if not isinstance(prediction, dict):
        failure('invalid_prediction', 'The optical engine returned an invalid result.')
    result = {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': MODEL_SHA256, 'inputSha256': input_sha256}
    for key, limit in (('smiles', MAX_SMILES_BYTES), ('molfile', MAX_MOLFILE_BYTES)):
        value = prediction.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or re.search('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value):
            failure('invalid_prediction', 'The optical engine did not return a supported molecular structure.')
        try:
            if len(value.encode('utf-8')) > limit:
                failure('invalid_prediction', 'The optical structure exceeds the supported output size.')
        except UnicodeError:
            failure('invalid_prediction', 'The optical engine returned invalid structure text.')
        result[key] = value
    confidence = prediction.get('confidence')
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, numbers.Real):
            failure('invalid_prediction', 'The optical confidence value is invalid.')
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            failure('invalid_prediction', 'The optical confidence value is invalid.')
    result['confidence'] = confidence
    return result


def recognize(png, input_sha256, model_path):
    image = decode_png(png)
    with contextlib.ExitStack() as resources:
        with ThreadPoolExecutor(max_workers=1) as executor:
            checked = executor.submit(resources.enter_context, verified_model(model_path))
            try:
                import torch
            finally:
                # Join even when the import fails: the worker must finish using
                # the stack before it closes the verified handle. Verification
                # errors take precedence, and no weights are loaded early.
                stream = checked.result()
        predictor = load_predictor(stream, torch_module=torch)
        import numpy as np
        try:
            prediction = predictor.predict_image(np.asarray(image), return_atoms_bonds=False, return_confidence=True)
        except Exception:
            failure('inference_failed', 'Local optical recognition failed to produce a structure.')
    return validated_prediction(prediction, input_sha256)


@contextlib.contextmanager
def library_output_to_stderr():
    """Reserve stdout for protocol JSON, including native-library fd writes."""
    saved_fd = None
    original_fd = None
    try:
        sys.stdout.flush()
        original_fd = sys.stdout.fileno()
        saved_fd = os.dup(original_fd)
        os.dup2(sys.stderr.fileno(), original_fd)
    except (AttributeError, OSError, io.UnsupportedOperation):
        if saved_fd is not None:
            os.close(saved_fd)
        saved_fd = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        if saved_fd is not None:
            sys.stderr.flush()
            os.dup2(saved_fd, original_fd)
            os.close(saved_fd)


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        failure('invalid_arguments', 'Provide one native-owned local model checkpoint.')


def main(argv=None, *, stdin=None, stdout=None):
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    exit_code = 0
    try:
        parser = _Parser(add_help=False)
        parser.add_argument('--model', required=True)
        args = parser.parse_args(argv)
        png, digest = read_request(stdin)
        configure_offline()
        sys.addaudithook(deny_python_network)
        with library_output_to_stderr():
            result = recognize(png, digest, args.model)
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) + 1 > MAX_OUTPUT_BYTES:
            failure('invalid_prediction', 'The optical result exceeds the supported output size.')
    except OpticalWorkerError as error:
        exit_code = 2
        encoded = json.dumps({'schemaVersion': 1, 'error': {'code': error.code, 'message': error.message}}, separators=(',', ':'))
    except Exception:
        exit_code = 2
        encoded = json.dumps({'schemaVersion': 1, 'error': {'code': 'worker_failed', 'message': 'The local optical worker could not complete this request.'}}, separators=(',', ':'))
    stdout.write(encoded + '\n')
    stdout.flush()
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
