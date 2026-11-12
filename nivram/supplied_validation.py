"""Bounded supplied V3000 parse consistency, never generation/authenticity.

Native chemistry runs only in the disposable worker. The application-side
request/result validators use the strict declaration grammar, not RDKit.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .coordinate_recovery import _v3000, ELEMENTS

MAX_TRANSPORT = 524288
MAX_OUTPUT = 524288
ENGINE_VERSION = '2025.09.4'
SCHEMA = 'nivram-supplied-3d-validation/1'


def validate_request(data):
    if not isinstance(data, dict) or set(data) != {'molText', 'molSha256', 'clientId', 'jobId', 'expiresAt'}:
        raise ValueError('Provide an exact supplied validation request.')
    text = data['molText']
    atoms, bonds = _v3000(text)
    if not isinstance(data['molSha256'], str) or hashlib.sha256(text.encode('ascii')).hexdigest() != data['molSha256']:
        raise ValueError('Supplied MOL checksum mismatch.')
    if any(not isinstance(data[k], str) or not re.fullmatch('[a-f0-9]{32}', data[k]) for k in ('clientId', 'jobId')):
        raise ValueError('Invalid supplied validation identifiers.')
    now = int(time.time()*1000)
    if type(data['expiresAt']) is not int or not now < data['expiresAt'] <= now + 60000:
        raise ValueError('Supplied validation deadline expired or exceeds 60 seconds.')
    return atoms, bonds


def _scene(text, atoms, bonds):
    return dict(kind='supplied_coordinates', sourceText=text,
        atoms=[dict(id=i+1, element=a[0], x=a[1][0], y=a[1][1], z=a[1][2],
                    atomMap=a[2], charge=a[3].get('CHG', 0), isotope=a[3].get('MASS', 0),
                    declarations=a[3]) for i, a in enumerate(atoms)],
        bonds=[dict(id=i+1, order=b[0], a=b[1], b=b[2], declarations=b[3]) for i, b in enumerate(bonds)])


def _validation(smiles):
    return dict(schema=SCHEMA, engine='RDKit', engineVersion=ENGINE_VERSION,
        engineDerivedIsomericSmiles=smiles, interpretation='engine-derived from supplied declarations and 3D coordinates',
        declarationsPreserved=True, coordinatesPreserved=True,
        generationClaimsVerified=False, experimentalOriginVerified=False, sourceBindingReverified=False)


def validate(data):
    atoms, bonds = validate_request(data)
    from rdkit import Chem, rdBase
    if rdBase.rdkitVersion != ENGINE_VERSION:
        raise ValueError('Supplied validation requires the tested RDKit 2025.09.4 runtime.')
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(data['molText'], sanitize=True, removeHs=False, strictParsing=True)
        if mol is None or mol.GetNumConformers() != 1 or not mol.GetConformer().Is3D() or mol.GetNumAtoms() != len(atoms) or mol.GetNumBonds() != len(bonds) or len(Chem.GetMolFrags(mol)) != 1:
            raise ValueError('Supplied molecule violates the supported connected 3D subset.')
        if mol.GetStereoGroups() or any(a.HasQuery() or a.GetNumRadicalElectrons() or a.GetSymbol() not in ELEMENTS for a in mol.GetAtoms()) or any(b.HasQuery() for b in mol.GetBonds()):
            raise ValueError('Unsupported supplied molecular features.')
        if any(str(s.specified) == 'Unspecified' for s in Chem.FindPotentialStereo(mol)):
            raise ValueError('Supplied coordinates leave potential stereochemistry unspecified.')
        # 3D wins over CFG in RDKit. Therefore native parse success alone is
        # insufficient. Reject *every* declaration change, even redundant VAL
        # normalization. This deliberately supports less than the CTfile standard.
        check_atoms, check_bonds = _v3000(Chem.MolToMolBlock(mol, includeStereo=True, forceV3000=True))
        if [(a[0], a[2], a[3]) for a in atoms] != [(a[0], a[2], a[3]) for a in check_atoms] or bonds != check_bonds:
            raise ValueError('Supplied declarations change or conflict with RDKit interpretation of 3D geometry.')
        for i, a in enumerate(mol.GetAtoms()):
            p = mol.GetConformer().GetAtomPosition(i); d = atoms[i]
            if (p.x, p.y, p.z) != d[1] or (a.GetSymbol(), a.GetAtomMapNum(), a.GetFormalCharge(), a.GetIsotope()) != (d[0], d[2], d[3].get('CHG', 0), d[3].get('MASS', 0)):
                raise ValueError('Supplied coordinates or atom declarations changed.')
        smiles = Chem.MolToSmiles(mol, isomericSmiles=True)
    result = dict(molSha256=data['molSha256'], clientId=data['clientId'], jobId=data['jobId'],
                  scene=_scene(data['molText'], atoms, bonds), validation=_validation(smiles))
    validate_result(result, data)
    return result


def validate_result(result, data):
    atoms, bonds = validate_request(data)
    if not isinstance(result, dict) or set(result) != {'molSha256', 'clientId', 'jobId', 'scene', 'validation'}:
        raise ValueError('Invalid supplied validation worker response.')
    if any(result[k] != data[k] for k in ('molSha256', 'clientId', 'jobId')):
        raise ValueError('Stale or inconsistent supplied validation response.')
    # Canonical JSON equality also distinguishes bools from integers, checks
    # unknown fields, exact source text, declarations, coordinate and ID binding.
    expected = _scene(data['molText'], atoms, bonds)
    try:
        encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        if encode(result['scene']) != encode(expected):
            raise ValueError('Supplied validation scene does not match exact input.')
        validation = result['validation']
        if not isinstance(validation, dict):
            raise ValueError('Invalid supplied validation evidence.')
        smiles = validation.get('engineDerivedIsomericSmiles')
        if not isinstance(smiles, str) or not 1 <= len(smiles) <= 8192 or not smiles.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in smiles):
            raise ValueError('Invalid engine-derived identity.')
        if encode(validation) != encode(_validation(smiles)):
            raise ValueError('Invalid supplied validation evidence.')
    except (TypeError, OverflowError, RecursionError):
        raise ValueError('Invalid supplied validation response encoding.') from None
    return result


def worker_command():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('nivram-worker.exe')), '--supplied-validation']
    return [sys.executable, '-B', '-s', '-m', 'nivram.supplied_validation', '--worker']


def run_request(data, supervisor, token):
    validate_request(data)
    supervisor.check_cancelled(token)
    timeout = min(15.0, (data['expiresAt'] - time.time()*1000)/1000)
    if timeout <= 0:
        raise ValueError('Supplied validation request expired.')
    try:
        completed = supervisor.run(worker_command(), input_text=json.dumps(data, ensure_ascii=False, allow_nan=False),
            timeout_seconds=timeout, cancellation_token=token, max_input_bytes=MAX_TRANSPORT,
            max_stdout_bytes=MAX_OUTPUT, max_stderr_bytes=8192)
    except subprocess.TimeoutExpired:
        raise ValueError('Supplied validation exceeded its 15-second bound.') from None
    supervisor.check_cancelled(token)
    from .png_metadata import _json
    result = _json(completed.stdout)
    if completed.returncode != 0:
        raise ValueError('Supplied coordinate validation failed or rejected the input.')
    validate_result(result, data)
    supervisor.check_cancelled(token)
    return result


def worker_main():
    try:
        if os.name == 'nt':
            from .worker_resource_limits import install_worker_limits
            install_worker_limits()
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
            resource.setrlimit(resource.RLIMIT_AS, (2*1024**3, 2*1024**3))
        raw = sys.stdin.buffer.read(MAX_TRANSPORT+1)
        if len(raw) > MAX_TRANSPORT:
            raise ValueError('Supplied validation transport exceeds its bound.')
        from .png_metadata import _json
        result = validate(_json(raw.decode('utf-8'))); code = 0
    except Exception as exc:
        result = {'error': str(exc) if isinstance(exc, ValueError) else 'Supplied validation failed before validated output.'}; code = 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False)); sys.stdout.flush()
    return code


if __name__ == '__main__':
    raise SystemExit(worker_main() if sys.argv[1:] == ['--worker'] else 2)
