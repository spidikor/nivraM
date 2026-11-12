"""Bounded local systematic naming with explicit, independent verification.

NISPO is optional and runs in a disposable local process. OPSIN is optional and
only verifies name-to-structure consistency. Neither establishes preferred IUPAC
nomenclature. This module never downloads dependencies or contacts a provider.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.metadata
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from dataclasses import dataclass

from rdkit import Chem, rdBase

from .chemistry import graph_to_mol
from .properties import input_identity, utc_timestamp
from .worker_processes import (CancellationToken, DEFAULT_SUPERVISOR, WorkerCancelled,
                               WorkerOutputLimit, WorkerSupervisor)


NISPO_VERSION = '0.1.7'
OPSIN_VERSION = '2.9.0'
MAX_NAME_LENGTH = 16_384
MAX_WORKER_OUTPUT = 131_072
MAX_WORKER_INPUT = 65_536


@dataclass(frozen=True)
class OpsinConfig:
    """Trusted application configuration, never accepted from an HTTP request."""
    java_path: str | Path
    jar_path: str | Path
    expected_sha256: str | None = None


def _run_nispo(smiles: str, timeout_seconds: float,
               supervisor: WorkerSupervisor | None = None,
               cancellation_token: CancellationToken | None = None) -> dict:
    if getattr(sys, 'frozen', False):
        worker = Path(sys.executable).with_name('nivram-worker.exe')
        if not worker.is_file():
            return {'status': 'unavailable', 'name': None,
                    'engine': {'name': 'NISPO', 'version': None},
                    'message': 'The optional local naming worker is not installed.'}
        command = [str(worker), '--name']
    else:
        command = [sys.executable, '-m', 'nivram.naming', '--worker']
    result = (supervisor or DEFAULT_SUPERVISOR).run(
        command, input_text=json.dumps({'smiles': smiles}),
        timeout_seconds=timeout_seconds, cancellation_token=cancellation_token,
        max_input_bytes=MAX_WORKER_INPUT, max_stdout_bytes=MAX_WORKER_OUTPUT,
        max_stderr_bytes=MAX_WORKER_OUTPUT,
    )
    if result.returncode or len(result.stdout) > MAX_WORKER_OUTPUT:
        return {'status': 'failed', 'name': None,
                'engine': {'name': 'NISPO', 'version': None},
                'message': 'The naming worker failed or exceeded its output limit.'}
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict):
        return {'status': 'failed', 'name': None,
                'engine': {'name': 'NISPO', 'version': None},
                'message': 'The naming worker returned an invalid response.'}
    return data


def _run_opsin(name: str, config: OpsinConfig, timeout_seconds: float,
               supervisor: WorkerSupervisor | None = None,
               cancellation_token: CancellationToken | None = None) -> dict:
    runner = supervisor or DEFAULT_SUPERVISOR
    runner.check_cancelled(cancellation_token)
    java, jar = Path(config.java_path), Path(config.jar_path)
    engine = {'name': 'OPSIN', 'version': None}
    if not java.is_file() or not jar.is_file():
        return {'status': 'unavailable', 'engine': engine,
                'warnings': ['The optional local OPSIN runtime is not installed.']}
    digest = hashlib.sha256(jar.read_bytes()).hexdigest()
    engine['jarSha256'] = digest
    if config.expected_sha256 and digest != config.expected_sha256:
        return {'status': 'failed', 'engine': engine,
                'warnings': ['The OPSIN archive does not match its configured checksum.']}
    start = time.monotonic()
    base = [str(java), '-Xmx256m', '-jar', str(jar)]
    info = runner.run(base + ['-h'], timeout_seconds=timeout_seconds,
                      cancellation_token=cancellation_token,
                      max_stdout_bytes=MAX_WORKER_OUTPUT, max_stderr_bytes=MAX_WORKER_OUTPUT)
    match = re.search(r'opsin-([0-9]+\.[0-9]+\.[0-9]+)-', info.stdout)
    if info.returncode or not match or match.group(1) != OPSIN_VERSION:
        return {'status': 'unavailable', 'engine': engine,
                'warnings': ['The configured OPSIN version is not the evaluated 2.9.0 release.']}
    engine['version'] = match.group(1)
    remaining = timeout_seconds - (time.monotonic() - start)
    if remaining <= 0:
        raise subprocess.TimeoutExpired(base, timeout_seconds)
    result = runner.run(base + ['-osmi'], input_text=name + '\n',
                        timeout_seconds=remaining, cancellation_token=cancellation_token,
                        max_input_bytes=MAX_WORKER_INPUT, max_stdout_bytes=MAX_WORKER_OUTPUT,
                        max_stderr_bytes=MAX_WORKER_OUTPUT)
    if result.returncode or len(result.stdout) > MAX_WORKER_OUTPUT:
        return {'status': 'failed', 'engine': engine,
                'warnings': ['OPSIN failed to parse the generated name.']}
    # The 2.9.0 CLI emits one fixed prompt on stdin mode. Any further diagnostic
    # is retained as a warning state, never promoted to verified success.
    diagnostics = [line for line in result.stderr.splitlines() if line.strip()
                   and line != 'Run the jar using the -h flag for help. Enter a chemical name to begin:']
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1 or not lines[0].strip():
        return {'status': 'failed', 'engine': engine,
                'warnings': ['OPSIN returned no single molecular structure.']}
    return {'status': 'warning' if diagnostics else 'parsed',
            'smiles': lines[0].strip(), 'engine': engine,
            'warnings': ['OPSIN reported a parsing warning; the name remains unverified.'] if diagnostics else []}


def validate_name_round_trip(graph: dict, parsed_smiles: str) -> dict:
    """Compare supported chemistry without tautomer/charge/stereo normalization."""
    original = Chem.Mol(graph_to_mol(graph))
    params = Chem.SmilesParserParams()
    params.parseName = False
    with rdBase.BlockLogs():
        parsed = (Chem.MolFromSmiles(parsed_smiles, params)
                  if isinstance(parsed_smiles, str) and len(parsed_smiles) <= MAX_WORKER_OUTPUT else None)
    warnings = []
    if parsed is None or not parsed.GetNumAtoms():
        return {'status': 'failed', 'warnings': ['The parser returned invalid chemistry.']}
    if any(atom.GetAtomMapNum() for atom in original.GetAtoms()):
        warnings.append('Atom-map annotations are retained in the drawing but are not encoded by a systematic chemical name.')
    # Atom maps are document annotations, not nomenclature. Only copies change.
    for mol in (original, parsed):
        for atom in mol.GetAtoms():
            atom.SetAtomMapNum(0)
    original, parsed = Chem.RemoveHs(original), Chem.RemoveHs(parsed)
    left = Chem.MolToSmiles(original, canonical=True, isomericSmiles=True)
    right = Chem.MolToSmiles(parsed, canonical=True, isomericSmiles=True)
    matched = (left == right and original.GetNumAtoms() == parsed.GetNumAtoms()
               and original.GetNumBonds() == parsed.GetNumBonds()
               and Chem.GetFormalCharge(original) == Chem.GetFormalCharge(parsed)
               and original.HasSubstructMatch(parsed, useChirality=True)
               and parsed.HasSubstructMatch(original, useChirality=True))
    return {'status': 'matched' if matched else 'mismatch',
            'returnedSmiles': right,
            'comparison': 'Full supported molecular graph, including stereo, isotopes, formal charge, radicals and fragments; atom maps excluded',
            'warnings': warnings}


def name_structure(graph: dict, document_revision: int | str | None = None,
                   *, timeout_seconds: float = 20.0,
                   opsin_config: OpsinConfig | None = None,
                   supervisor: WorkerSupervisor | None = None,
                   cancellation_token: CancellationToken | None = None) -> dict:
    """Generate locally; unavailable verification never becomes a PIN claim."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        raise ValueError('The naming timeout must be greater than zero and at most 60 seconds.')
    snapshot = copy.deepcopy(graph)
    mol = graph_to_mol(snapshot)
    identity = input_identity(snapshot, mol, document_revision)
    result = {'status': 'unavailable', 'name': None, 'generatedName': None,
              'nameKind': 'generated_systematic', 'preferredIUPACName': False,
              'inputIdentity': identity, 'calculatedAt': utc_timestamp(),
              'engine': {'name': 'NISPO', 'version': None},
              'chemistryEngine': {'name': 'RDKit', 'version': rdBase.rdkitVersion},
              'transformations': [], 'warnings': [],
              'roundTrip': {'status': 'unavailable', 'warnings': [
                  'OPSIN verification is not configured. A generated name is not a verified Preferred IUPAC Name.']}}
    runner = supervisor or DEFAULT_SUPERVISOR

    def finish() -> dict:
        runner.check_cancelled(cancellation_token)
        return result

    start = time.monotonic()
    try:
        runner.check_cancelled(cancellation_token)
        if opsin_config is None and any(atom.GetIsotope() or atom.GetNumRadicalElectrons()
                                        for atom in mol.GetAtoms()):
            result['status'] = 'unsupported'
            result['warnings'].append('This NISPO release produced incorrect isotope/radical names in local evaluation. These structures require configured OPSIN identity verification; no unverified name is shown.')
            return finish()
        generated = _run_nispo(identity['smiles'], timeout_seconds, runner, cancellation_token)
        result['engine'] = generated.get('engine', result['engine'])
        if generated.get('status') != 'generated':
            result['status'] = generated.get('status') if generated.get('status') in ('unavailable', 'unsupported', 'failed') else 'failed'
            result['warnings'].append(generated.get('message', 'Local name generation did not succeed.'))
            return finish()
        name = generated.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LENGTH or any(char in name for char in '\r\n\t\x00'):
            result['status'] = 'failed'
            result['warnings'].append('The naming engine returned an empty or invalid name.')
            return finish()
        result.update(status='generated_unverified', name=name.strip(), generatedName=name.strip())
        if opsin_config is None:
            return finish()
        remaining = timeout_seconds - (time.monotonic() - start)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(['OPSIN'], timeout_seconds)
        parsed = _run_opsin(name, opsin_config, remaining, runner, cancellation_token)
        if parsed['status'] != 'parsed':
            result['roundTrip'] = parsed
            result['warnings'].extend(parsed.get('warnings', []))
            return finish()
        verification = validate_name_round_trip(snapshot, parsed.get('smiles'))
        verification['engine'] = parsed['engine']
        result['roundTrip'] = verification
        if verification['status'] == 'matched':
            result['status'] = 'round_trip_matched'
        elif verification['status'] == 'mismatch':
            result.update(status='mismatch', name=None)
            result['warnings'].append('The generated name reconstructed different chemistry and was rejected. Stereo, isotopes, charge, radicals and fragments must match.')
        return finish()
    except WorkerCancelled:
        result.update(status='cancelled', name=None, generatedName=None)
        result['roundTrip'] = {'status': 'cancelled', 'warnings': []}
        result['warnings'].append('The local naming job was cancelled; no result was adopted.')
        return result
    except WorkerOutputLimit:
        result.update(status='failed', name=None)
        result['warnings'].append('The local naming worker exceeded an output limit; no result was adopted.')
        return result
    except subprocess.TimeoutExpired:
        result['status'] = 'timeout'
        result['name'] = None
        result['warnings'].append('The local naming time limit was reached; no result was adopted.')
        return result
    except (OSError, ValueError, TypeError, KeyError):
        result['status'] = 'failed'
        result['name'] = None
        result['warnings'].append('The local naming adapter failed; the original structure is unchanged.')
        return result


def worker_main() -> int:
    """Entrypoint for source Python or a separately bundled console worker."""
    response = {'status': 'failed', 'name': None,
                'engine': {'name': 'NISPO', 'version': None}}
    try:
        raw = sys.stdin.read(MAX_WORKER_INPUT + 1)
        if len(raw) > MAX_WORKER_INPUT:
            raise ValueError('Input limit exceeded.')
        data = json.loads(raw)
        smiles = data.get('smiles')
        if not isinstance(smiles, str) or not smiles:
            raise ValueError('Missing molecular structure.')
        try:
            version = importlib.metadata.version('nispo')
        except importlib.metadata.PackageNotFoundError:
            response.update(status='unavailable', message='NISPO is not installed; no name was generated.')
        else:
            response['engine']['version'] = version
            if version != NISPO_VERSION:
                response.update(status='unavailable', message='The installed NISPO version has not been evaluated for this build.')
            else:
                mol = Chem.MolFromSmiles(smiles)
                if mol is None or not 0 < mol.GetNumAtoms() <= 512:
                    raise ValueError('Invalid molecular structure.')
                with contextlib.redirect_stdout(io.StringIO()):
                    from nispo import mol_to_iupac
                    name = mol_to_iupac(mol)
                if isinstance(name, str) and name.strip() and len(name) <= MAX_NAME_LENGTH:
                    response.update(status='generated', name=name.strip())
                else:
                    response.update(status='unsupported', message='NISPO returned no supported systematic name for this structure.')
    except Exception:
        response.update(status='failed', name=None,
                        message='Local name generation failed; no substitute structure or name was used.')
    sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(worker_main() if '--worker' in sys.argv else 2)
