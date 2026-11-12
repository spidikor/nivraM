"""Optional disclosed PubChem retrieval; network I/O exists only in the worker.

The application owns consent, single-flight admission and rate limiting. A
database name is exposed only with an exact supported structure match; this is
not independent validation of the provider's name-to-structure assignment.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener)

ENDPOINT = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/property/IUPACName,SMILES/JSON'
PROVIDER = 'PubChem (NCBI/NLM)'
MAX_SMILES = 16_384
MAX_INPUT = 65_536
MAX_OUTPUT = 131_072
MAX_RECORDS = 8
TIMEOUT_SECONDS = 15
WARNINGS = (
    'PubChem may standardize the submitted structure. A different tautomer, charge, isotope, stereochemistry or fragment set is not accepted as a match.',
    'Atom maps are drawing annotations and are excluded from chemical-name identity matching; the drawing is never changed.',
    'A retrieved database IUPAC name is not independently validated as a preferred IUPAC name.',
)
ERRORS = {
    'not_found': 'not_found', 'service_unavailable': 'unavailable',
    'timeout': 'unavailable', 'network_unavailable': 'unavailable',
    'worker_unavailable': 'unavailable', 'ambiguous_records': 'ambiguous',
    'redirect_rejected': 'failed', 'invalid_response': 'failed',
    'response_limit': 'failed', 'invalid_input': 'failed', 'worker_failed': 'failed',
}


def _smiles(value):
    if (not isinstance(value, str) or not value or len(value) > MAX_SMILES
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ValueError('Unsupported SMILES value.')
    return value


def _record(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid provider record.')
    cid, name = value.get('CID'), value.get('IUPACName')
    if (type(cid) is not int or not 0 < cid < 2 ** 63 or not isinstance(name, str)
            or not name or name != name.strip() or len(name.encode('utf-8')) > MAX_SMILES
            or any(unicodedata.category(c).startswith('C') for c in name)):
        raise ValueError('Invalid provider identity or name.')
    return {'CID': cid, 'IUPACName': name, 'SMILES': _smiles(value.get('SMILES'))}


def _error(code):
    return {'status': ERRORS[code], 'records': [], 'retrievedAt': None, 'errorCode': code}


def prepare_submission(graph, document_revision):
    """Prepare the exact disclosure locally; does not authorize or send it."""
    from .chemistry import graph_to_mol
    from .properties import input_identity
    snapshot = copy.deepcopy(graph)
    molecule = graph_to_mol(snapshot)
    if not molecule.GetNumAtoms():
        raise ValueError('Draw a structure before requesting a database name.')
    identity = input_identity(snapshot, molecule, document_revision)
    smiles = _smiles(identity['smiles'])
    if len(urlencode({'smiles': smiles}).encode('ascii')) > MAX_INPUT:
        raise ValueError('The submitted structure exceeds the provider request limit.')
    return {'graph': snapshot, 'inputIdentity': identity, 'disclosure': {
        'version': 1, 'provider': PROVIDER, 'endpoint': ENDPOINT,
        'purpose': 'Retrieve the PubChem IUPAC name and structure, then compare the returned structure locally.',
        'cost': 'PubChem provides this service free of charge; an internet connection is required.',
        'submittedSmiles': smiles, 'warnings': list(WARNINGS),
    }}


def _worker_payload(value):
    if not isinstance(value, dict) or set(value) != {'status', 'records', 'retrievedAt', 'errorCode'}:
        raise ValueError('Invalid worker response.')
    if value['status'] != 'ok':
        code = value['errorCode']
        if not isinstance(code, str) or code not in ERRORS or value != _error(code):
            raise ValueError('Invalid worker failure.')
        return value
    records = value['records']
    timestamp = value['retrievedAt']
    if (not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS
            or value['errorCode'] is not None or not isinstance(timestamp, str)
            or len(timestamp) > 40):
        raise ValueError('Invalid worker records.')
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('Invalid retrieval timestamp.')
    return {**value, 'records': [_record(record) for record in records]}


def lookup_submission(submission, *, supervisor, cancellation_token):
    """Run one owned network worker outside the application's chemistry lock."""
    from .naming import validate_name_round_trip
    from .worker_processes import WorkerCancelled, WorkerOutputLimit, WorkerProcessError
    supervisor.check_cancelled(cancellation_token)
    if not isinstance(submission, dict) or set(submission) != {'graph', 'inputIdentity', 'disclosure'}:
        raise ValueError('Invalid prepared PubChem submission.')
    try:
        prepared = prepare_submission(submission['graph'], submission['inputIdentity']['documentRevision'])
    except (KeyError, TypeError) as error:
        raise ValueError('Invalid prepared PubChem submission.') from error
    if prepared != submission:
        raise ValueError('The prepared PubChem submission changed; review it again.')
    result = {'status': 'failed', 'provider': PROVIDER, 'inputIdentity': prepared['inputIdentity'],
              'name': None, 'nameKind': 'retrieved_database_iupac', 'preferredIUPACName': False,
              'records': [], 'warnings': list(WARNINGS), 'errorCode': None}
    if getattr(sys, 'frozen', False):
        worker = Path(sys.executable).with_name('nivram-worker.exe')
        if not worker.is_file():
            result.update(status='unavailable', errorCode='worker_unavailable')
            return result
        command = [str(worker), '--pubchem']
    else:
        command = [sys.executable, '-m', 'nivram.pubchem', '--worker']
    try:
        response = supervisor.run(command,
            input_text=json.dumps({'smiles': prepared['disclosure']['submittedSmiles']}),
            timeout_seconds=TIMEOUT_SECONDS, cancellation_token=cancellation_token,
            max_input_bytes=MAX_INPUT, max_stdout_bytes=MAX_OUTPUT, max_stderr_bytes=1024)
        supervisor.check_cancelled(cancellation_token)
        if (response.returncode != 0 or not isinstance(response.stdout, str)
                or len(response.stdout.encode('utf-8')) > MAX_OUTPUT):
            raise ValueError('Invalid worker output.')
        data = _worker_payload(json.loads(response.stdout))
    except WorkerCancelled:
        raise
    except subprocess.TimeoutExpired:
        result.update(status='unavailable', errorCode='timeout')
        return result
    except (OSError, ValueError, TypeError, RecursionError, WorkerOutputLimit, WorkerProcessError):
        result.update(status='failed', errorCode='worker_failed')
        return result
    if data['status'] != 'ok':
        result.update(status=data['status'], errorCode=data['errorCode'])
        return result
    for record in data['records']:
        supervisor.check_cancelled(cancellation_token)
        try:
            comparison = validate_name_round_trip(copy.deepcopy(prepared['graph']), record['SMILES'])
        except Exception:
            comparison = {'status': 'failed', 'warnings': ['The returned structure could not be compared.']}
        result['records'].append({
            'CID': record['CID'], 'retrievedAt': data['retrievedAt'],
            'submittedSmiles': prepared['disclosure']['submittedSmiles'],
            'returnedSmiles': record['SMILES'], 'returnedName': record['IUPACName'], 'match': comparison,
        })
    supervisor.check_cancelled(cancellation_token)
    if len(result['records']) != 1:
        result.update(status='ambiguous', errorCode='ambiguous_records')
    else:
        result['status'] = result['records'][0]['match']['status']
        if result['status'] == 'matched':
            result['name'] = result['records'][0]['returnedName']
        elif result['status'] == 'failed':
            result['errorCode'] = 'invalid_response'
    return result


class _RedirectRejected(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            fp.close()
        finally:
            raise _RedirectRejected('Provider redirects are disabled.')


def _make_opener():
    # No ambient proxy credentials, cookies, authentication handlers or redirects.
    return build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))


def _request_smiles(smiles):
    """Worker-only fixed HTTPS request, no retries/polling and no body logging."""
    try:
        encoded = urlencode({'smiles': _smiles(smiles)}).encode('ascii')
        if len(encoded) > MAX_INPUT:
            return _error('invalid_input')
        request = Request(ENDPOINT, data=encoded, method='POST', headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json',
            'User-Agent': 'nivraM optional PubChem lookup',
        })
        with _make_opener().open(request, timeout=10) as response:
            if response.geturl() != ENDPOINT or 300 <= response.status < 400:
                return _error('redirect_rejected')
            if response.status == 404:
                return _error('not_found')
            if response.status in (202, 429) or response.status >= 500:
                return _error('service_unavailable')
            if response.status != 200:
                return _error('invalid_response')
            raw = response.read(MAX_OUTPUT + 1)
        if len(raw) > MAX_OUTPUT:
            return _error('response_limit')
        value = json.loads(raw)
        records = value['PropertyTable']['Properties']
        if not isinstance(records, list):
            return _error('invalid_response')
        if not records:
            return _error('not_found')
        if len(records) > MAX_RECORDS:
            return _error('ambiguous_records')
        return {'status': 'ok', 'records': [_record(record) for record in records],
                'retrievedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'errorCode': None}
    except HTTPError as error:
        code = error.code
        error.close()
        return _error('not_found' if code == 404 else 'service_unavailable' if code == 429 or code >= 500
                      else 'redirect_rejected' if 300 <= code < 400 else 'invalid_response')
    except _RedirectRejected:
        return _error('redirect_rejected')
    except (TimeoutError, socket.timeout):
        return _error('timeout')
    except (URLError, OSError):
        return _error('network_unavailable')
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return _error('invalid_response')


def worker_main():
    """Read only the disclosed SMILES; report fixed bounded JSON on stdout."""
    code = 0
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('Input limit.')
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {'smiles'}:
            raise ValueError('Input schema.')
        result = _request_smiles(_smiles(value['smiles']))
    except Exception:
        result, code = _error('invalid_input'), 1
    encoded = json.dumps(result, ensure_ascii=True, allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_OUTPUT:
        encoded, code = json.dumps(_error('response_limit')), 1
    sys.stdout.write(encoded)
    return code


if __name__ == '__main__':
    raise SystemExit(worker_main() if sys.argv[1:] == ['--worker'] else 2)
