"""Bounded core-runtime validation of an optical engine's raw JSON result.

Native chemistry is imported only by the child entry point. The parent performs
plain data checks and uses its supplied process owner and remaining deadline.
The process owner must install the private Job limits before the child starts;
this module does not install quotas, provision a model or use the network.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from .import_limits import RADICAL_CONSISTENCY_MESSAGE, SINGLET_MESSAGE
from .import_protocol import _graph, strict_json_loads

MAX_INPUT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MODEL_SHA256 = '32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501'
SAFE_FAILURE = 'Local optical validation could not complete. The drawing is unchanged.'
REJECTION_MESSAGES = {
    'inconsistent_mol_radical': RADICAL_CONSISTENCY_MESSAGE,
    'unsupported_singlet_radical': SINGLET_MESSAGE,
    'inconsistent_prediction': ('Recognition returned conflicting MOL and SMILES structures. '
        'No proposal was added. Try a clearer crop, or paste the intended SMILES or 2D MOL structure for review.'),
    'unsupported_abbreviation': ('Recognition contains an unresolved atom abbreviation. '
        'No proposal was added. Use an image with the group drawn out, or paste the intended SMILES or 2D MOL structure for review.'),
}
_CANDIDATE_FIELDS = {'id', 'status', 'kind', 'graph', 'label', 'source',
    'requiresReview', 'atomCount', 'formalCharge', 'warnings', 'unsupportedFeatures', 'recognition'}
_ATOM_FIELDS = {'id', 'symbol', 'x', 'y', 'charge', 'isotope', 'explicitHs',
    'noImplicit', 'aromatic', 'chiralTag', 'radicals', 'mapNum'}
_BOND_FIELDS = {'id', 'begin', 'end', 'order', 'direction', 'stereo', 'stereoAtoms'}
_RECOGNITION_FIELDS = {'method', 'modelSha256', 'inputSha256', 'confidence',
    'confidenceMeaning', 'coordinates'}
_SOURCE_WORKER_BOOTSTRAP = (
    'import sys; sys.path.insert(0, sys.argv.pop(1)); '
    'from nivram.optical_validation import worker_main; '
    'raise SystemExit(worker_main())')


def _command():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('nivram-worker.exe')), '--validate-optical']
    # Ignore cwd, PYTHONPATH and user-site packages. Only the trusted source
    # root is added explicitly; its path stays data in a separate argument.
    return [sys.executable, '-I', '-B', '-c', _SOURCE_WORKER_BOOTSTRAP,
            str(Path(__file__).resolve().parent.parent)]


def _require(condition):
    if not condition:
        raise ValueError(SAFE_FAILURE)


def _bounded_text(text, limit):
    _require(isinstance(text, str) and 0 < len(text) <= limit)
    try:
        encoded = text.encode('utf-8')
    except UnicodeError:
        raise ValueError(SAFE_FAILURE) from None
    _require(len(encoded) <= limit)
    return encoded


def _checked_candidate(candidate, input_digest):
    """Check only JSON shape and duplicated fields; never reconstruct chemistry."""
    _require(isinstance(candidate, dict) and set(candidate) == _CANDIDATE_FIELDS)
    _require(candidate['id'] == 'optical-1' and candidate['status'] == 'supported'
             and candidate['kind'] == 'optical_candidate'
             and candidate['source'] == 'optical-recognition' and candidate['requiresReview'] is True)
    graph = candidate['graph']
    _require(isinstance(graph, dict) and set(graph) == {'atoms', 'bonds'})
    _graph(graph)
    for atom in graph['atoms']:
        _require(set(atom) == _ATOM_FIELDS and atom['id'].startswith('optical-1-'))
    for bond in graph['bonds']:
        _require(set(bond) == _BOND_FIELDS and bond['id'].startswith('optical-1-'))
    count = len(graph['atoms'])
    _require(type(candidate['atomCount']) is int and candidate['atomCount'] == count
             and candidate['label'] == f'Optically recognized structure ({count} atoms)')
    _require(type(candidate['formalCharge']) is int
             and candidate['formalCharge'] == sum(atom['charge'] for atom in graph['atoms']))
    warnings = candidate['warnings']
    _require(isinstance(warnings, list) and 1 <= len(warnings) <= 16
             and all(isinstance(item, str) and 0 < len(item) <= 4096 for item in warnings)
             and candidate['unsupportedFeatures'] == [])
    recognition = candidate['recognition']
    _require(isinstance(recognition, dict) and set(recognition) == _RECOGNITION_FIELDS)
    _require(recognition['method'] == 'MolScribe' and recognition['modelSha256'] == MODEL_SHA256
             and recognition['inputSha256'] == input_digest
             and recognition['confidenceMeaning'] == 'uncalibrated model score'
             and recognition['coordinates'] == 'preserved-from-recognized-molfile')
    confidence = recognition['confidence']
    _require(confidence is None or (type(confidence) in (int, float)
             and 0 <= confidence <= 1 and math.isfinite(confidence)))
    return candidate


def _remaining_seconds(remaining_time):
    seconds = remaining_time()
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise subprocess.TimeoutExpired(['local-optical-validation'], 0)
    return seconds


def validate_optical_result(raw_stdout, input_digest, supervisor, remaining_time, token):
    """Return a child-validated candidate using the existing sequential owner.

    ``input_digest`` is the retained crop hash carried by the raw optical result,
    not a digest of its JSON encoding. Raw text is forwarded without rewriting.
    Cancellation and process-owner failures remain the caller's responsibility.
    """
    supervisor.check_cancelled(token)
    _bounded_text(raw_stdout, MAX_INPUT_BYTES)
    _require(isinstance(input_digest, str) and re.fullmatch('[a-f0-9]{64}', input_digest))
    completed = supervisor.run(_command(), input_text=raw_stdout,
        timeout_seconds=_remaining_seconds(remaining_time), cancellation_token=token,
        max_input_bytes=MAX_INPUT_BYTES, max_stdout_bytes=MAX_OUTPUT_BYTES,
        max_stderr_bytes=MAX_STDERR_BYTES)
    supervisor.check_cancelled(token)
    rejection_message = None
    try:
        _require(type(completed.returncode) is int and completed.returncode in (0, 2))
        _bounded_text(completed.stdout, MAX_OUTPUT_BYTES)
        envelope = strict_json_loads(completed.stdout)
        _require(isinstance(envelope, dict)
                 and set(envelope) == {'schemaVersion', 'inputSha256',
                                      'candidate' if completed.returncode == 0 else 'error'}
                 and type(envelope['schemaVersion']) is int and envelope['schemaVersion'] == 1
                 and envelope['inputSha256'] == input_digest)
        if completed.returncode == 0:
            candidate = _checked_candidate(envelope['candidate'], input_digest)
        else:
            error = envelope['error']
            _require(isinstance(error, dict) and set(error) == {'code', 'message'}
                     and isinstance(error['code'], str) and error['code'] in REJECTION_MESSAGES
                     and error['message'] == REJECTION_MESSAGES[error['code']])
            rejection_message = REJECTION_MESSAGES[error['code']]
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise ValueError(SAFE_FAILURE) from None
    supervisor.check_cancelled(token)
    _remaining_seconds(remaining_time)
    if rejection_message is not None:
        raise ValueError(rejection_message)
    return candidate


def worker_main(*, stdin=None, stdout=None):
    """Read at most one bounded raw response and emit one binary JSON envelope."""
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    exit_code = 0
    failure = {'schemaVersion': 1, 'error': {
        'code': 'invalid_prediction', 'message': SAFE_FAILURE}}
    try:
        raw = stdin.read(MAX_INPUT_BYTES + 1)
        _require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_INPUT_BYTES)
        result = strict_json_loads(raw.decode('utf-8'))
        # The validator owns all native chemistry. Suppress Python library
        # chatter; optical_candidate separately blocks RDKit's native logs.
        with open(os.devnull, 'w', encoding='utf-8') as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                from .optical_import import OpticalImportError, optical_candidate
                try:
                    candidate = optical_candidate(result)
                except OpticalImportError as exc:
                    digest = result.get('inputSha256') if isinstance(result, dict) else None
                    if (exc.code in REJECTION_MESSAGES and isinstance(digest, str)
                            and re.fullmatch('[a-f0-9]{64}', digest)):
                        failure = {'schemaVersion': 1, 'inputSha256': digest, 'error': {
                            'code': exc.code, 'message': REJECTION_MESSAGES[exc.code]}}
                    raise
        envelope = {'schemaVersion': 1, 'inputSha256': result['inputSha256'], 'candidate': candidate}
        encoded = json.dumps(envelope, ensure_ascii=True, allow_nan=False,
                             separators=(',', ':')).encode('utf-8') + b'\n'
        _require(len(encoded) <= MAX_OUTPUT_BYTES)
    except Exception:
        exit_code = 2
        encoded = json.dumps(failure, separators=(',', ':')).encode('ascii') + b'\n'
    stdout.write(encoded)
    stdout.flush()
    return exit_code


if __name__ == '__main__':
    raise SystemExit(worker_main() if sys.argv[1:] == ['--worker'] else 2)
