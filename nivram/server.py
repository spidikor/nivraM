"""Loopback API; external PubChem requests require explicit one-use consent."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
import copy
import hmac
import json
import mimetypes
import re
import secrets
import socket
import threading
import time
from rdkit import Chem, rdBase
from . import __version__
from .chemistry import ChemistryError, AtomValenceError, graph_to_mol, mol_to_graph, clean_graph, export_mol
from .scene import render_scene
from .render_contract import begin_binding, finish_binding
from .worker_processes import CancellationToken, WorkerCancelled, WorkerSupervisor
from .pubchem_jobs import PubChemJobs, PubChemJobError
from .import_preparation import ImportPreparationService, ImportPreparationError
from .population_jobs import PopulationJobs, PopulationJobError, MAX_TRANSPORT_BYTES as MAX_POPULATION_REQUEST_BYTES
from .population_state import PopulationStateJobs, PopulationStateError

PUBLIC = Path(__file__).resolve().parent.parent / 'public'
LOCK = threading.RLock()
MAX_NAMING_JOBS = 2
MAX_JOB_RECORDS = 256
JOB_HISTORY_SECONDS = 120.0
MAX_JOB_DEADLINE_MS = 60_000
REJECTION_DRAIN_BYTES = 65_536
REJECTION_DRAIN_SECONDS = .25
MAX_REQUEST_BODY_BYTES = 4_000_000
MAX_REQUEST_JSON_DEPTH = 64
REQUEST_BODY_SECONDS = 10.0
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; font-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"


def _check_request_json_depth(body):
    """Bound wire nesting before decoding, independent of parser recursion limits.

    Root object/array counts as one container. Quoted bytes and escaped quotes
    do not add nesting, including JSON encoded inside source strings. This is
    not a JSON parser: the existing decoder still validates UTF-8 and grammar.
    Inspect raw bytes so overwritten duplicate-key values cannot hide depth.
    """
    depth, quoted, escaped = 0, False, False
    for byte in body:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:  # backslash
                escaped = True
            elif byte == 34:  # quote
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (123, 91):  # object or array
            depth += 1
            if depth > MAX_REQUEST_JSON_DEPTH:
                raise ValueError('JSON nesting exceeds the request limit.')
        elif byte in (125, 93):
            depth -= 1
            if depth < 0:
                raise ValueError('Unbalanced JSON containers.')


class NamingJobError(ValueError):
    def __init__(self, status, message, state='rejected'):
        super().__init__(message)
        self.http_status, self.state = status, state


class NamingJobs:
    """Bounded per-server tokens and recent IDs, never molecule-bearing records.

    Unexpired records are never evicted to admit another ID. Public deadlines
    keep an old queued name request invalid after its cancellation tombstone
    expires. Legacy callers without IDs still receive an owned internal token.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}
        self._history = {}
        self._closed = False

    @staticmethod
    def _identity(data, *, optional=False):
        values = (data.get('clientId'), data.get('jobId'))
        if optional and not any(field in data for field in ('clientId','jobId','expiresAt')):
            return None
        if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value) for value in values):
            raise NamingJobError(422, 'Provide valid bounded client and job identifiers.')
        expiry = data.get('expiresAt')
        if isinstance(expiry, bool) or not isinstance(expiry, int) or not 0 < expiry <= 2**53-1:
            raise NamingJobError(422, 'Provide a valid naming request deadline.')
        if expiry > int(time.time()*1000) + MAX_JOB_DEADLINE_MS:
            raise NamingJobError(422, 'The naming request deadline is too far in the future.')
        return values

    def _purge(self):
        now = time.monotonic()
        for key, (_status, expiry) in list(self._history.items()):
            if expiry <= now:
                del self._history[key]

    def register(self, data):
        key = self._identity(data, optional=True)
        if key is not None and data['expiresAt'] <= int(time.time()*1000):
            raise NamingJobError(409, 'The naming request expired before it started.', 'expired')
        if key is None:
            key = ('legacy', secrets.token_hex(16))
        with self._lock:
            self._purge()
            if self._closed:
                raise NamingJobError(503, 'The local naming service is shutting down.', 'closed')
            if key in self._active:
                raise NamingJobError(409, 'This naming job is already active.', 'active')
            if key in self._history:
                state = self._history[key][0]
                raise NamingJobError(409, 'This naming job already finished or was cancelled. Use a new job identifier.', state)
            if len(self._active) >= MAX_NAMING_JOBS:
                raise NamingJobError(429, 'Two local naming jobs are already running. Try again after one finishes.', 'busy')
            if len(self._active) + len(self._history) >= MAX_JOB_RECORDS:
                raise NamingJobError(429, 'Recent naming requests filled the bounded job registry. Try again shortly.', 'busy')
            token = CancellationToken()
            self._active[key] = token
            return key, token

    def cancel(self, data):
        if set(data) != {'clientId', 'jobId', 'expiresAt'}:
            raise NamingJobError(422, 'Cancellation accepts only a client ID, job ID and request deadline.')
        key = self._identity(data)
        with self._lock:
            self._purge()
            token = self._active.get(key)
            if token is not None:
                token.cancel()
                return {'status': 'cancellation_requested', 'registered': True}
            if key in self._history:
                return {'status': self._history[key][0], 'registered': False}
            if self._closed:
                return {'status': 'closed', 'registered': False}
            if data['expiresAt'] <= int(time.time()*1000):
                return {'status': 'expired', 'registered': False}
            if len(self._active) + len(self._history) >= MAX_JOB_RECORDS:
                raise NamingJobError(429, 'The bounded cancellation registry is full. No cancellation was recorded.', 'unconfirmed')
            self._history[key] = ('cancelled', time.monotonic() + JOB_HISTORY_SECONDS)
            return {'status': 'cancelled_before_registration', 'registered': False}

    def finish(self, key, token):
        with self._lock:
            if self._active.get(key) is token:
                del self._active[key]
                self._history[key] = ('cancelled' if token.cancelled else 'finished',
                                      time.monotonic() + JOB_HISTORY_SECONDS)

    def close(self):
        with self._lock:
            self._closed = True
            for token in self._active.values():
                token.cancel()

    def snapshot(self):
        with self._lock:
            self._purge()
            return {'acceptingJobs': not self._closed, 'activeJobs': len(self._active),
                    'recentJobIds': len(self._history), 'maximumActiveJobs': MAX_NAMING_JOBS,
                    'maximumJobRecords': MAX_JOB_RECORDS}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self.naming_jobs = NamingJobs()
        self.conformer_jobs = NamingJobs()
        self.coordinate_recovery_jobs = NamingJobs()
        self.supplied_validation_jobs = NamingJobs()
        self.worker_supervisor = WorkerSupervisor(max_workers=MAX_NAMING_JOBS)
        self.pubchem_jobs = PubChemJobs(self.worker_supervisor)
        self.import_preparation = ImportPreparationService()
        self.population_jobs = PopulationJobs()
        self.population_state_jobs = PopulationStateJobs(self.import_preparation)
        self.worker_shutdown_receipt = None
        super().__init__(*args, **kwargs)

    def scientific_jobs_status(self):
        return {**self.naming_jobs.snapshot(), 'shutdown': self.worker_shutdown_receipt,
                'onlineLookup': self.pubchem_jobs.snapshot(),
                'importPreparation': self.import_preparation.snapshot(),
                'populationEvaluation': self.population_jobs.snapshot(),
                'populationStatePreparation': self.population_state_jobs.snapshot()}

    def shutdown_scientific_jobs(self):
        self.naming_jobs.close()
        self.conformer_jobs.close()
        self.coordinate_recovery_jobs.close()
        self.supplied_validation_jobs.close()
        self.pubchem_jobs.close()
        self.population_state_jobs.close()
        naming_confirmed = self.worker_supervisor.shutdown(timeout_seconds=2.0)
        preparation_confirmed = self.import_preparation.shutdown(timeout_seconds=2.0)
        population_confirmed = self.population_jobs.shutdown(timeout_seconds=2.0)
        confirmed = naming_confirmed and preparation_confirmed and population_confirmed
        self.worker_shutdown_receipt = {
            'status': 'confirmed' if confirmed else 'unconfirmed',
            'ownedProcessesExited': confirmed,
            'importPreparationExited': preparation_confirmed,
            'populationEvaluationExited': population_confirmed,
            'scope': 'Direct child-process exit; request threads may still be unwinding.',
        }
        return dict(self.worker_shutdown_receipt)

    def server_close(self):
        try:
            self.shutdown_scientific_jobs()
        finally:
            super().server_close()

class Handler(BaseHTTPRequestHandler):
    server_version = 'nivraM-local'
    def log_message(self, *_):
        pass  # HTTP request logging is disabled.
    def _send(self, status, payload, mime='application/json; charset=utf-8', *, close=False):
        if isinstance(payload,(dict,list)):
            payload=json.dumps(payload,ensure_ascii=True,allow_nan=False).encode()
        elif isinstance(payload,str):payload=payload.encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(payload)))
            if close:
                self.send_header('Connection','close')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',CSP)
            self.send_header('Cross-Origin-Resource-Policy','same-origin')
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            # Closing a native window may disconnect while headers are flushed.
            # This is a completed/cancelled client request, not a server failure.
            pass
    def _reject_post(self, status, payload, *, raw_remaining=None):
        """Send first, then give a bounded in-flight body time to leave TCP.

        RFC 9112 section 9.6 describes response loss from immediate full close.
        Only one unambiguous decimal Content-Length is trusted for discarding;
        transfer-coded/ambiguous bodies are never interpreted. This cannot
        promise graceful delivery to oversized or stalling peers.

        After an admitted body read fails, the buffered reader may be poisoned
        by a timeout. Discard its remaining wire bytes through the raw socket
        under the same cap/deadline; no further request will use this stream.
        """
        self.close_connection = True
        lengths = self.headers.get_all('Content-Length', [])
        limit = 0
        if (not self.headers.get_all('Transfer-Encoding') and len(lengths) == 1
                and re.fullmatch(r'[0-9]{1,20}', lengths[0].strip())):
            limit = min(int(lengths[0].strip()), REJECTION_DRAIN_BYTES)
        if raw_remaining is not None:
            limit = min(limit, raw_remaining)
        deadline = time.monotonic() + REJECTION_DRAIN_SECONDS
        previous_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(REJECTION_DRAIN_SECONDS)
            self._send(status, payload, close=True)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            while limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.connection.settimeout(remaining)
                # read1 performs at most one underlying receive. read(n) can
                # repeatedly receive trickled input beyond a total deadline.
                read = self.connection.recv if raw_remaining is not None else self.rfile.read1
                discarded = read(min(limit, 8192))
                if not discarded:
                    break
                limit -= len(discarded)
        except (OSError, ValueError):
            pass  # Timeout/disconnect/closed stream: close this rejected request.
        finally:
            try:
                self.connection.settimeout(previous_timeout)
            except OSError:
                pass
    def _local(self):
        if self.headers.get_all('Host', []) != [f'127.0.0.1:{self.server.server_port}']:
            send = self._reject_post if self.command == 'POST' else self._send
            send(403,{'error':'Only the loopback address is accepted.'});return False
        return True
    def _read_body(self, length):
        """Read an admitted length under one total deadline, including trickles."""
        deadline = time.monotonic() + REQUEST_BODY_SECONDS
        self._body_remaining_bytes = length
        previous_timeout = self.connection.gettimeout()
        chunks, remaining_bytes = [], length
        try:
            while remaining_bytes:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise TimeoutError('Request body deadline expired.')
                self.connection.settimeout(remaining_seconds)
                # A read(n) can perform many receives, resetting an inactivity
                # timeout with each trickle. read1 permits at most one receive.
                chunk = self.rfile.read1(min(remaining_bytes, 65_536))
                if not chunk:
                    raise ValueError('Truncated request body.')
                chunks.append(chunk)
                remaining_bytes -= len(chunk)
            if time.monotonic() >= deadline:
                raise TimeoutError('Request body deadline expired.')
            return b''.join(chunks)
        finally:
            self._body_remaining_bytes = remaining_bytes
            self.connection.settimeout(previous_timeout)
    def do_GET(self):
        if not self._local():return
        route=unquote(urlsplit(self.path).path)
        if route=='/api/status':
            return self._send(200,{'version':__version__,'rdkit':rdBase.rdkitVersion,'offline':True,
                'externalProviders':'per-request consent','offlineCore':True,
                'nativePersistence':bool(getattr(self.server,'desktop_mode',False)),
                'namingVerifier':getattr(self.server,'naming_runtime_status',{'status':'unavailable'}),
                'scientificJobs':self.server.scientific_jobs_status()})
        if route=='/api/science-capabilities':
            from .populations import population_capabilities
            return self._send(200,population_capabilities())
        if route=='/api/physiology':
            from .populations import load_physiology_presets
            return self._send(200,load_physiology_presets())
        route='/index.html' if route=='/' else route
        path=(PUBLIC / route.lstrip('/')).resolve()
        if not path.is_relative_to(PUBLIC) or not path.is_file() or path.suffix not in ('.html','.mjs','.js','.css','.json','.svg'):
            return self._send(404,{'error':'Resource not found.'})
        content=path.read_bytes()
        if path.name=='index.html':content=content.replace(b'__TOKEN__',self.server.api_token.encode())
        mime={'.html':'text/html; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml'}[path.suffix]
        return self._send(200,content,mime)
    def do_POST(self):
        if not self._local():return
        origins=self.headers.get_all('Origin', [])
        expected=f'http://127.0.0.1:{self.server.server_port}'
        # Origin may be absent for same-origin/local clients, but a present
        # value must be singular and exact, including rejecting an empty value.
        if origins and origins != [expected]:
            return self._reject_post(403,{'error':'Cross-origin requests are disabled.'})
        tokens=self.headers.get_all('X-Nivram-Token', [])
        if (len(tokens)!=1 or not tokens[0].isascii()
                or not hmac.compare_digest(tokens[0],self.server.api_token)):
            return self._reject_post(403,{'error':'The local session token is missing or expired. Reload the app.'})
        content_types=self.headers.get_all('Content-Type', [])
        if len(content_types)!=1 or content_types[0].split(';')[0].strip()!='application/json':
            return self._reject_post(415,{'error':'Send a JSON request.'})
        lengths=self.headers.get_all('Content-Length', [])
        if (self.headers.get_all('Transfer-Encoding') or len(lengths)!=1
                or not re.fullmatch(r'[0-9]{1,20}', lengths[0].strip())):
            return self._reject_post(400,{'error':'Provide one decimal Content-Length without Transfer-Encoding.'})
        length=int(lengths[0].strip())
        maximum_body = MAX_POPULATION_REQUEST_BYTES if urlsplit(self.path).path in ('/api/populations/evaluate', '/api/populations/prepare-state') else MAX_REQUEST_BODY_BYTES
        if not 0<length<=maximum_body:
            return self._reject_post(413,{'error':'Population requests must fit within 8 MiB.' if maximum_body == MAX_POPULATION_REQUEST_BYTES else 'Requests must be smaller than 4 MB.'})
        try:
            body=self._read_body(length)
        except TimeoutError:
            return self._reject_post(408,{'error':'The JSON request body did not arrive before its deadline.'},
                                     raw_remaining=self._body_remaining_bytes)
        except (OSError,ValueError):
            return self._reject_post(400,{'error':'The JSON request body was incomplete.'},
                                     raw_remaining=self._body_remaining_bytes)
        try:
            _check_request_json_depth(body)
            data=json.loads(body.decode('utf-8'),parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
            if not isinstance(data,dict):return self._send(400,{'error':'The request must be a JSON object.'})
        except (ValueError,UnicodeError,RecursionError):return self._send(400,{'error':'Invalid JSON.'})
        route=urlsplit(self.path).path
        try:
            if route=='/api/populations/evaluate':
                return self._send(200,self.server.population_jobs.evaluate(data))
            if route=='/api/populations/cancel':
                return self._send(200,self.server.population_jobs.cancel(data))
            if route=='/api/populations/prepare-state':
                return self._send(200,self.server.population_state_jobs.prepare(data))
            if route=='/api/populations/cancel-state':
                return self._send(200,self.server.population_state_jobs.cancel(data))
            if route=='/api/import':
                # Parsing, graph validation, descriptors and rendering all run
                # in the limited child; never hold the host chemistry lock here.
                from .import_binding import begin_http, finish_http
                bound = begin_http(data)
                if bound is not None:
                    admitted = self.server.import_preparation.prepare_import(
                        bound['source'],bound.get('style'),with_receipt=True)
                    return self._send(200,finish_http(bound,admitted))
                return self._send(200,self.server.import_preparation.prepare_import(
                    data.get('source',''),data.get('style')))
            if route=='/api/pubchem/prepare':
                # A local, nonmutating snapshot and disclosure; never provider I/O.
                with LOCK, rdBase.BlockLogs():
                    return self._send(200,self.server.pubchem_jobs.prepare(data))
            if route=='/api/pubchem/cancel':
                return self._send(200,self.server.pubchem_jobs.cancel(data))
            if route=='/api/pubchem/lookup':
                # No chemistry lock while the isolated, cancellable worker waits.
                return self._send(200,self.server.pubchem_jobs.lookup(data))
            if route in ('/api/supplied-validation', '/api/supplied-validation/cancel'):
                from .png_metadata import _json
                data = _json(body.decode('utf-8'))
            if route=='/api/supplied-validation/cancel':
                return self._send(200,self.server.supplied_validation_jobs.cancel(data))
            if route=='/api/supplied-validation':
                from .supplied_validation import run_request, validate_request
                validate_request(data)
                key,token=self.server.supplied_validation_jobs.register(data)
                try:
                    result=run_request(data,self.server.worker_supervisor,token)
                    self.server.worker_supervisor.check_cancelled(token)
                    return self._send(200,result)
                finally:
                    self.server.supplied_validation_jobs.finish(key,token)
            if route=='/api/coordinate-recovery/cancel':
                return self._send(200,self.server.coordinate_recovery_jobs.cancel(data))
            if route=='/api/coordinate-recovery':
                from .coordinate_recovery import run_request
                key,token=self.server.coordinate_recovery_jobs.register(data)
                try:
                    result=run_request(data,self.server.worker_supervisor,token)
                    self.server.worker_supervisor.check_cancelled(token)
                    return self._send(200,result)
                finally:
                    self.server.coordinate_recovery_jobs.finish(key,token)
            if route=='/api/conformer/cancel':
                return self._send(200,self.server.conformer_jobs.cancel(data))
            if route=='/api/conformer':
                from .conformer import run_request
                key,token=self.server.conformer_jobs.register(data)
                try:
                    result=run_request(data,self.server.worker_supervisor,token)
                    self.server.worker_supervisor.check_cancelled(token)
                    return self._send(200,result)
                finally:
                    self.server.conformer_jobs.finish(key,token)
            if route=='/api/cancel-job':
                return self._send(200,self.server.naming_jobs.cancel(data))
            if route=='/api/name':
                key,cancellation_token=self.server.naming_jobs.register(data)
                try:
                    from .naming import name_structure
                    # An isolated worker performs naming; never hold the chemistry lock while it runs.
                    deadline=time.monotonic()+5
                    while not self.server.naming_runtime_ready.is_set() and time.monotonic()<deadline:
                        self.server.worker_supervisor.check_cancelled(cancellation_token)
                        self.server.naming_runtime_ready.wait(min(0.05,max(0,deadline-time.monotonic())))
                    self.server.worker_supervisor.check_cancelled(cancellation_token)
                    result=name_structure(data.get('graph',{}),data.get('documentRevision'),
                        opsin_config=getattr(self.server,'opsin_config',None),
                        supervisor=self.server.worker_supervisor,cancellation_token=cancellation_token)
                    self.server.worker_supervisor.check_cancelled(cancellation_token)
                    if 'clientId' in data:
                        result.update(clientId=data['clientId'],jobId=data['jobId'])
                    result['runtimeVerification']=dict(self.server.naming_runtime_status)
                    if self.server.naming_runtime_status['status'] != 'available':
                        result['warnings'].append(self.server.naming_runtime_status['message'])
                finally:
                    self.server.naming_jobs.finish(key,cancellation_token)
                self.server.worker_supervisor.check_cancelled(cancellation_token)
                return self._send(200,result)
            with LOCK, rdBase.BlockLogs():  # Avoid emitting submitted structures in parser logs.
                binding = None
                if route in ('/api/render', '/api/clean', '/api/reflect', '/api/rotate'):
                    binding = begin_binding(route.removeprefix('/api/'), data)
                    if binding is not None:
                        data = copy.deepcopy(data)
                if route=='/api/comparison':
                    from .comparison import compare_request
                    return self._send(200,compare_request(data))
                if route=='/api/stereo-inspector':
                    from .stereo_inspector import inspect_request
                    return self._send(200,inspect_request(data))
                if route=='/api/properties':
                    from .properties import property_response
                    return self._send(200,property_response(data))
                elif route=='/api/render':
                    submitted=data.get('graph',{})
                    mol=graph_to_mol(submitted,allow_empty=True)
                    if not mol.GetNumAtoms():graph={'atoms':[],'bonds':[]}
                    else:
                        # Store explicit Kekule bonds to allow safe ring editing;
                        # sanitization restores aromatic chemical identity.
                        copy_mol=Chem.Mol(mol);Chem.Kekulize(copy_mol,clearAromaticFlags=True)
                        graph=mol_to_graph(copy_mol,[a['id'] for a in submitted['atoms']],[b['id'] for b in submitted['bonds']])
                elif route=='/api/clean':
                    graph=clean_graph(data.get('graph',{}),data.get('selected',[]))
                elif route=='/api/reflect':
                    from .reflection import reflect_graph
                    graph=reflect_graph(data.get('graph',{}),mode=data.get('mode'),axis=data.get('axis'),selected=data.get('selected'))
                elif route=='/api/rotate':
                    from .rotation import rotate_graph
                    graph=rotate_graph(data.get('graph',{}),degrees=data.get('degrees'),selected=data.get('selected'))
                elif route=='/api/export-mol':
                    return self._send(200,{'text':export_mol(data.get('graph',{}))})
                elif route=='/api/export-smiles':
                    return self._send(200,{'text':Chem.MolToSmiles(graph_to_mol(data.get('graph',{})),isomericSmiles=True)})
                else:return self._send(404,{'error':'Unknown API action.'})
                if binding is not None:
                    return self._send(200,finish_binding(binding,graph,render_scene))
                return self._send(200,{'graph':graph,'scene':render_scene(graph,data.get('style'))})
        except (NamingJobError,PubChemJobError) as exc:
            return self._send(exc.http_status,{'error':str(exc),'status':exc.state})
        except ImportPreparationError as exc:
            return self._send(exc.http_status,{'error':str(exc),'status':exc.code})
        except (PopulationJobError, PopulationStateError) as exc:
            return self._send(exc.http_status,{'error':str(exc),'status':exc.code})
        except WorkerCancelled:
            return self._send(409,{'error':'The scientific request was cancelled; no result was adopted.','status':'cancelled'})
        except (ChemistryError,ValueError,TypeError,KeyError) as exc:
            if route.startswith('/api/pubchem/'):
                return self._send(422,{'error':'This structure or lookup response could not be validated. The drawing is unchanged.',
                                       'status':'rejected'})
            payload = {'error': str(exc)}
            if route == '/api/render' and isinstance(exc, AtomValenceError):
                payload['diagnostic'] = exc.diagnostic
            return self._send(422,payload)
        except Exception:
            return self._send(500,{'error':'The local chemistry operation failed. The previous document is retained.'})
    def do_OPTIONS(self):
        return self._send(403,{'error':'Cross-origin access is disabled.'})

def make_server(port=0):
    server=LocalServer(('127.0.0.1',port),Handler)
    server.api_token=secrets.token_urlsafe(32)
    server.opsin_config=None
    server.naming_runtime_status={'status':'checking','message':'The optional local name verifier is being checked.'}
    server.naming_runtime_ready=threading.Event()
    def configure_naming():
        try:
            from .naming_runtime import discover_naming_runtime
            runtime=discover_naming_runtime()
            server.opsin_config=runtime.config
            server.naming_runtime_status={'status':runtime.status,'message':runtime.message}
        except Exception:
            server.naming_runtime_status={'status':'unavailable','message':'The optional local name verifier could not initialize.'}
        finally:
            server.naming_runtime_ready.set()
    threading.Thread(target=configure_naming,name='nivraM-name-verifier-check',daemon=True).start()
    return server
