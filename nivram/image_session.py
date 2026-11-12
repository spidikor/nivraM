"""Review-bound PNG import state; no source paths are accepted from web content."""
import base64
import binascii
import hashlib
import copy
from pathlib import Path
import re
import secrets
import threading
import time
from collections import deque

from .image_jobs import ImageJobs, ImageJobCancelled, MAX_INPUT
from .image_import import select_metadata_candidates
from .persistence import atomic_write
from .png_preview import preview_capability
from .png_preview_jobs import ImagePreviewJobs, validate_region
from .worker_processes import CancellationToken, WorkerCancelled
from .optical_jobs import OpticalJobs, configured_optical_jobs
from .optical_processes import WALL_SECONDS
from .import_preparation import ImportPreparationService
from .import_protocol import checked_style

MAX_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_SOURCE_STORE = 512 * 1024 * 1024
MANUAL_CORRESPONDENCE = 'user-supplied structure; image correspondence not independently verified'


class ImageImportSession:
    def __init__(self, directory, *, optical_jobs=None, preparation_service=None):
        self._directory = Path(directory)
        self._jobs = ImageJobs()
        self._preview_jobs = ImagePreviewJobs(self._directory.parent / 'image-review-cache')
        self._optical_jobs, self._optical_status = (configured_optical_jobs(self._directory.parent / 'optical-runtime')
            if optical_jobs is None else (optical_jobs, optical_jobs.availability()))
        self._owns_preparation_service = preparation_service is None
        self._preparation_service = (ImportPreparationService() if preparation_service is None
                                     else preparation_service)
        self._lock = threading.RLock()
        self._current = None
        self._pending = None
        self._cancelled_ids = deque(maxlen=64)
        self._closed = False
        self._optical_operation = None

    def optical_availability(self):
        with self._lock:
            if self._closed or not self._preparation_service.snapshot()['acceptingJobs']:
                return {'status': 'closed', 'message': 'Local recognition has closed.'}
            return dict(self._optical_jobs.availability() if self._optical_jobs else self._optical_status)

    def ensure_optical_configurable(self):
        with self._lock:
            if self._closed or not self._preparation_service.snapshot()['acceptingJobs']:
                raise ValueError('Local recognition has closed.')
            if self._optical_operation is not None:
                raise ValueError('Wait for local structure recognition to finish before changing its setup.')
            if self._optical_jobs is not None and self._optical_jobs.availability()['status'] == 'closed':
                raise ValueError('Local recognition has closed. Restart nivraM before changing its setup.')

    def configure_optical(self, python_executable, model_path, commit):
        """Change future recognition only; preserve the current review verbatim."""
        with self._lock:
            self.ensure_optical_configurable()
            if self._optical_jobs is not None:
                self._optical_jobs.reconfigure(python_executable, model_path, commit)
            else:
                owner = OpticalJobs(python_executable, model_path)
                try:
                    commit()
                except BaseException:
                    owner.close()
                    raise
                self._optical_jobs = owner
            self._optical_status = self._optical_jobs.availability()
            return dict(self._optical_status)

    def inspect(self, path, job_id):
        path = Path(path)
        def read_source():
            with path.open('rb') as handle:
                return handle.read(MAX_INPUT + 1)
        return self._inspect(read_source, path.name, job_id)

    def inspect_bytes(self, png_base64, filename, job_id):
        """Inspect original PNG bytes; filename is a label, never a filesystem path."""
        if (not isinstance(filename, str) or not 1 <= len(filename) <= 255
                or not filename.lower().endswith('.png')
                or re.search(r'[\x00-\x1f\x7f/\\:<>"|?*]', filename)):
            raise ValueError('Use a PNG filename without a folder or path.')
        if (not isinstance(png_base64, str) or not png_base64
                or len(png_base64) > 4 * ((MAX_INPUT + 2) // 3)):
            raise ValueError('PNG input exceeds the 64 MiB limit or has an invalid encoding.')
        def read_source():
            try:
                return base64.b64decode(png_base64, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError('PNG input has an invalid base64 encoding.') from None
        return self._inspect(read_source, filename, job_id)

    def _inspect(self, read_source, name, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', job_id):
            raise ValueError('Invalid image job identifier.')
        with self._lock:
            if self._closed or job_id in self._cancelled_ids:
                raise ImageJobCancelled('Image inspection was cancelled.')
            if self._pending is not None:
                raise ValueError('Another source-image inspection is already running.')
            state = {'id': job_id, 'cancelled': False, 'cancel_event': threading.Event()}
            self._pending = state
            if self._current is not None:
                self._cancel_optical(self._current)
                self._cancel_manual(self._current)
                self._preview_jobs.cancel(self._current['id'])
            self._current = None
        try:
            data = read_source()
            if len(data) > MAX_INPUT:
                raise ValueError('PNG input exceeds the 64 MiB limit.')
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Select a PNG image to inspect.')
            with self._lock:
                if state['cancelled'] or self._closed:
                    raise ImageJobCancelled('Image inspection was cancelled.')
            result = self._jobs.inspect(data, job_id, _cancel_event=state['cancel_event'])
            if result.get('originalSha256') != hashlib.sha256(data).hexdigest():
                raise ValueError('Image inspection returned an invalid source identity.')
            capability = preview_capability(data)
            preview = None
            if result['image']['previewMode'] == 'bounded_preview' and len(data) <= MAX_PREVIEW_BYTES:
                preview = 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')
            with self._lock:
                if state['cancelled'] or self._closed:
                    raise ImageJobCancelled('Image inspection was cancelled.')
                self._current = {'id': job_id, 'data': data, 'result': result,
                                 'name': name, 'reviewAvailable': preview is not None,
                                 'regionCapability': capability, 'regionRequest': None,
                                 'regionReview': None, 'regionUsed': set(), 'regionCancelled': set(),
                                 'cropRequired': False, 'opticalRequest': None, 'opticalToken': None,
                                 'opticalRegion': None, 'manualRequest': None, 'manualToken': None,
                                 'manualRegion': None, 'manualPrepared': None}
            return {'status': 'inspected', 'jobId': job_id, 'name': name, 'inspection': result,
                    'sourcePreview': preview, 'reviewAvailable': preview is not None,
                    'opticalRecognition': (self._optical_jobs.availability() if self._optical_jobs else self._optical_status),
                    'regionPreviewAvailable': capability['available'],
                    'regionPreviewLimitation': capability['reason'],
                    'previewLimitation': (None if preview else
                        'Review a detail crop at original pixel scale before importing. An overview is for navigation only.'
                        if capability['available'] else
                        'Bounded region review is unavailable: ' + capability['reason'] + ' Import remains disabled.')}
        finally:
            with self._lock:
                if self._pending is state:
                    self._pending = None

    def cancel(self, job_id):
        with self._lock:
            self._cancelled_ids.append(job_id)
            if self._pending and self._pending['id'] == job_id:
                self._pending['cancelled'] = True
                self._pending['cancel_event'].set()
            if self._current and self._current['id'] == job_id:
                self._cancel_optical(self._current)
                self._cancel_manual(self._current)
                self._current = None
        self._preview_jobs.cancel(job_id)
        return self._jobs.cancel(job_id)

    def _cancel_optical(self, current):
        if current.get('opticalToken') is not None:
            current['opticalToken'].cancel()
        if self._optical_jobs is not None and current.get('opticalRequest'):
            self._optical_jobs.cancel(current['opticalRequest'])
        current['opticalToken'] = None
        current['opticalRequest'] = None

    @staticmethod
    def _cancel_manual(current):
        if current.get('manualToken') is not None:
            current['manualToken'].cancel()
        current['manualToken'] = None
        current['manualRequest'] = None

    @staticmethod
    def _selected_manual(current, selected_ids):
        if not isinstance(selected_ids, list):
            raise ValueError('Select supported components explicitly.')
        if 'manual-1' not in selected_ids:
            return None
        if selected_ids != ['manual-1']:
            raise ValueError('Review the user-supplied structure separately from other components.')
        candidate = next((item for item in current['result']['candidates']
                          if item['id'] == 'manual-1'), None)
        if candidate is None or current['manualRegion'] is None or current['manualPrepared'] is None:
            raise ValueError('Supply a structure for this crop before reviewing its draft.')
        return candidate

    def prepare_manual(self, job_id, request_id, rect, source, style):
        if not isinstance(source, str) or not source.strip() or len(source) > 1_000_000:
            raise ValueError('Supply nonblank SMILES or 2D MOL text within the 1,000,000-byte limit.')
        encoded = source.encode('utf-8')
        if len(encoded) > 1_000_000:
            raise ValueError('Structure text exceeds the 1,000,000-byte limit.')
        style = checked_style(style)
        with self._lock:
            current = self._current
            if self._closed or current is None or current['id'] != job_id:
                raise ImageJobCancelled('This source-image review is no longer current.')
        preview = self.preview_region(job_id, request_id, rect, 'crop', [])
        with self._lock:
            if (self._closed or self._current is not current or current['regionRequest'] != request_id
                    or request_id in current['regionCancelled']):
                raise ImageJobCancelled('This supplied structure review is no longer current.')
            token = CancellationToken()
            current['manualRequest'], current['manualToken'] = request_id, token
        try:
            try:
                prepared = self._preparation_service.prepare_import(source, style, cancellation_token=token)
            except WorkerCancelled as exc:
                raise ImageJobCancelled('This supplied structure review was cancelled.') from exc
            with self._lock:
                if (self._closed or self._current is not current or token.cancelled
                        or current['regionRequest'] != request_id or request_id in current['regionCancelled']):
                    raise ImageJobCancelled('This supplied structure review is no longer current.')
                graph = copy.deepcopy(prepared['graph'])
                supplied = {'textSha256': hashlib.sha256(encoded).hexdigest(),
                            'coordinates': 'from structure text; not traced from image'}
                candidate = {'id': 'manual-1', 'source': 'user-supplied-structure',
                    'status': 'supported', 'kind': 'user_supplied_structure', 'graph': graph,
                    'label': f'User-supplied structure ({len(graph["atoms"])} atoms)',
                    'atomCount': len(graph['atoms']), 'formalCharge': sum(atom.get('charge', 0) for atom in graph['atoms']),
                    'requiresReview': True, 'unsupportedFeatures': [], 'suppliedStructure': supplied,
                    'warnings': ['This structure was supplied as text, not recognized from the PNG. Compare every atom, bond, charge and stereochemical mark with the source image before importing.']}
                updated = copy.deepcopy(current['result'])
                retained = [item for item in updated['candidates'] if item['id'] != 'manual-1']
                if len(retained) >= 1024:
                    raise ValueError('The image review already contains the maximum number of components.')
                updated['candidates'] = retained + [candidate]
                region = {'kind': 'crop', 'requestId': request_id,
                    **{key: preview[key] for key in ('sourceSha256', 'sourceWidth', 'sourceHeight',
                        'rect', 'width', 'height', 'pngSha256')},
                    'scale': 'one-source-pixel-per-image-pixel',
                    'metadataCorrespondence': MANUAL_CORRESPONDENCE}
                review_token = secrets.token_urlsafe(32)
                current['result'] = updated
                current['manualRegion'] = copy.deepcopy(region)
                current['manualPrepared'] = {'result': copy.deepcopy(prepared), 'style': copy.deepcopy(style)}
                current['regionReview'] = {'token': review_token, 'selected': ['manual-1'], 'region': region}
                return {'status': 'drafted', 'jobId': job_id, 'requestId': request_id,
                    'inspection': copy.deepcopy(updated), 'selectedComponents': ['manual-1'],
                    'region': {**preview, 'reviewToken': review_token}}
        finally:
            with self._lock:
                if self._current is current and current.get('manualToken') is token:
                    current['manualRequest'] = None
                    current['manualToken'] = None

    @staticmethod
    def _selected_optical(current, selected_ids):
        if 'optical-1' not in selected_ids:
            return None
        if selected_ids != ['optical-1']:
            raise ValueError('Review the recognized structure separately from embedded metadata components.')
        candidate = next((item for item in current['result']['candidates']
                          if item['id'] == 'optical-1'), None)
        if candidate is None or current['opticalRegion'] is None:
            raise ValueError('Recognize this crop before reviewing its proposed structure.')
        return candidate

    def recognize(self, job_id, request_id, rect):
        deadline = time.monotonic() + WALL_SECONDS

        def remaining_time():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Local structure recognition reached its time limit. The drawing is unchanged.')
            return remaining

        with self._lock:
            if self._closed:
                raise ImageJobCancelled('This structure recognition is no longer current.')
            if self._optical_jobs is None:
                raise ValueError(self._optical_status['message'])
            if self._optical_operation is not None:
                raise ValueError('Another local structure recognition is already running.')
            owner = self._optical_jobs
            operation = object()
            self._optical_operation = operation
        current = token = None
        try:
            preview = self.preview_region(job_id, request_id, rect, 'crop', [])
            with self._lock:
                current = self._current
                if (self._closed or current is None or current['id'] != job_id or
                        current['regionRequest'] != request_id or request_id in current['regionCancelled']):
                    raise ImageJobCancelled('This structure recognition is no longer current.')
                token = CancellationToken()
                current['opticalRequest'], current['opticalToken'] = request_id, token
            try:
                data = base64.b64decode(preview['pngBase64'], validate=True)
                result = owner.recognize(data, request_id, timeout=remaining_time(), _cancellation_token=token)
            except WorkerCancelled as exc:
                raise ImageJobCancelled('This structure recognition was cancelled.') from exc
            with self._lock:
                if (self._closed or self._current is not current or token.cancelled or
                        current['regionRequest'] != request_id or request_id in current['regionCancelled']):
                    raise ImageJobCancelled('This structure recognition is no longer current.')
                candidate = result['candidate']
                if (result.get('inputSha256') != preview['pngSha256'] or candidate.get('id') != 'optical-1'
                        or candidate.get('recognition', {}).get('inputSha256') != preview['pngSha256']):
                    raise ValueError('The recognition result does not match the reviewed source crop.')
                updated = copy.deepcopy(current['result'])
                updated['candidates'] = [item for item in updated['candidates'] if item['id'] != 'optical-1'] + [candidate]
                updated['recognition'] = {'status': 'proposed', **candidate['recognition']}
                review_token = secrets.token_urlsafe(32)
                region = {'kind': 'crop', 'requestId': request_id,
                    **{key: preview[key] for key in ('sourceSha256', 'sourceWidth', 'sourceHeight',
                        'rect', 'width', 'height', 'pngSha256')},
                    'scale': 'one-source-pixel-per-image-pixel',
                    'metadataCorrespondence': 'unverified optical recognition of this crop'}
                remaining_time()
                current['result'] = updated
                current['opticalRegion'] = copy.deepcopy(region)
                current['regionReview'] = {'token': review_token, 'selected': ['optical-1'], 'region': region}
                return {'status': 'recognized', 'jobId': job_id, 'requestId': request_id,
                    'inspection': copy.deepcopy(updated), 'selectedComponents': ['optical-1'],
                    'region': {**preview, 'reviewToken': review_token}}
        finally:
            with self._lock:
                if current is not None and self._current is current and current.get('opticalToken') is token:
                    current['opticalRequest'] = None
                    current['opticalToken'] = None
                if self._optical_operation is operation:
                    self._optical_operation = None

    def preview_region(self, job_id, request_id, rect, mode, selected_ids):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
            raise ValueError('Invalid region request identifier.')
        with self._lock:
            current = self._current
            if self._closed or current is None or current['id'] != job_id:
                raise ImageJobCancelled('This source-image review is no longer current.')
            if request_id in current['regionCancelled']:
                raise ImageJobCancelled('This region review was cancelled.')
            if request_id in current['regionUsed']:
                raise ValueError('This region request identifier was already used; request a fresh review.')
            if len(current['regionUsed']) + len(current['regionCancelled']) >= 512:
                raise ValueError('Reopen the source image to start a new review session.')
            capability = current['regionCapability']
            if not capability['available']:
                raise ValueError(capability['reason'])
            rect = validate_region(mode, rect, (capability['width'], capability['height']))
            if not isinstance(selected_ids, list):
                raise ValueError('Select complete metadata components for review.')
            manual = self._selected_manual(current, selected_ids)
            if selected_ids and not manual:
                select_metadata_candidates(current['result'], selected_ids)
            optical = self._selected_optical(current, selected_ids)
            if optical and (mode != 'crop' or rect != current['opticalRegion']['rect']):
                raise ValueError('Recognize the new crop before reviewing its proposed structure.')
            if manual and (mode != 'crop' or rect != current['manualRegion']['rect']):
                raise ValueError('Supply a structure for the new crop before reviewing its draft.')
            selected_ids = list(selected_ids)
            self._cancel_optical(current)
            self._cancel_manual(current)
            current['regionUsed'].add(request_id)
            current['regionRequest'] = request_id
            current['regionReview'] = None
            if mode == 'crop':
                current['cropRequired'] = True
            data = current['data']
        try:
            preview = self._preview_jobs.preview(data, job_id, request_id, mode, rect)
        except WorkerCancelled as exc:
            raise ImageJobCancelled('This region review was cancelled.') from exc
        with self._lock:
            if (self._closed or self._current is not current or
                    current['regionRequest'] != request_id or request_id in current['regionCancelled']):
                raise ImageJobCancelled('This region review is no longer current.')
            token = secrets.token_urlsafe(32) if mode == 'crop' and selected_ids else None
            if optical and preview['pngSha256'] != current['opticalRegion']['pngSha256']:
                raise ValueError('The recognition result does not match the current crop.')
            if manual and preview['pngSha256'] != current['manualRegion']['pngSha256']:
                raise ValueError('The supplied structure review does not match the current crop.')
            if token:
                current['regionReview'] = {'token': token, 'selected': selected_ids,
                    'region': {'kind': 'crop', 'requestId': request_id,
                        **{key: preview[key] for key in ('sourceSha256', 'sourceWidth', 'sourceHeight',
                            'rect', 'width', 'height', 'pngSha256')},
                        'scale': 'one-source-pixel-per-image-pixel',
                        'metadataCorrespondence': (MANUAL_CORRESPONDENCE if manual else
                            'unverified optical recognition of this crop' if optical else
                            'not established; whole components retained')}}
            return {**preview, 'jobId': job_id, 'requestId': request_id, 'reviewToken': token}

    def cancel_region(self, job_id, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
            raise ValueError('Invalid region request identifier.')
        with self._lock:
            current = self._current
            if current is not None and current['id'] == job_id:
                if current.get('opticalRequest') == request_id:
                    self._cancel_optical(current)
                if current.get('manualRequest') == request_id:
                    self._cancel_manual(current)
                if len(current['regionCancelled']) >= 512:
                    # Exhaustion discards the complete session; it must never
                    # skip cancellation or allow an in-flight result to publish.
                    self._current = None
                    self._cancel_optical(current)
                    self._cancel_manual(current)
                    self._preview_jobs.cancel(job_id)
                else:
                    current['regionCancelled'].add(request_id)
                    if current['regionRequest'] == request_id:
                        current['regionRequest'] = None
                        current['regionReview'] = None
        return self._preview_jobs.cancel(job_id, request_id)

    def candidate(self, job_id, selected_ids, style):
        from .server import LOCK
        from .scene import render_scene
        with self._lock:
            current = self._current
            if self._closed or current is None or current['id'] != job_id:
                raise ValueError('This image review is no longer current.')
            manual = self._selected_manual(current, selected_ids)
            if manual:
                cached = current['manualPrepared']
                if checked_style(style) != cached['style']:
                    raise ValueError('The drawing style changed. Supply the structure again before review.')
                # The complete parse/validation/render was protected by the
                # preparation child. Do not repeat chemistry in this host.
                return {'graph': copy.deepcopy(cached['result']['graph']),
                    'scene': copy.deepcopy(cached['result']['scene']),
                    'warnings': sorted(set(current['result']['warnings'] + manual['warnings'])),
                    'sourceSha256': current['result']['originalSha256'], 'selectedComponents': ['manual-1']}
            graph = select_metadata_candidates(current['result'], selected_ids)
            self._selected_optical(current, selected_ids)
            selected = [c for c in current['result']['candidates'] if c['id'] in selected_ids]
            warnings = list(current['result']['warnings'])
            for candidate in selected:
                warnings.extend(candidate.get('warnings', []))
        with LOCK:
            scene = render_scene(graph, style)
        return {'graph': graph, 'scene': scene, 'warnings': sorted(set(warnings)),
                'sourceSha256': current['result']['originalSha256'],
                'selectedComponents': list(selected_ids)}

    def prepare_accept(self, job_id, selected_ids, style, review_token=None):
        candidate = self.candidate(job_id, selected_ids, style)
        with self._lock:
            current = self._current
            if self._closed or current is None or current['id'] != job_id:
                raise ValueError('A current bounded source-image review is required before import.')
            review = current['regionReview']
            optical = self._selected_optical(current, selected_ids)
            manual = self._selected_manual(current, selected_ids)
            needs_crop = manual is not None or review_token is not None or current['cropRequired'] or not current['reviewAvailable']
            if needs_crop and (not isinstance(review_token, str) or not review or
                    not secrets.compare_digest(review_token, review['token']) or review['selected'] != selected_ids):
                raise ValueError('A current bounded source-image review is required before import.')
            if manual and candidate['graph'] != current['manualPrepared']['result']['graph']:
                raise ValueError('The supplied structure changed. Review the current draft before importing.')
            data = current['data']
            digest = hashlib.sha256(data).hexdigest()
            if digest != candidate['sourceSha256']:
                raise ValueError('Source identity changed; image import was cancelled.')
            self._directory.mkdir(parents=True, exist_ok=True)
            destination = self._directory / (digest + '.png')
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise ValueError('The preserved source copy is damaged; image import was cancelled.')
            else:
                total = sum(p.stat().st_size for p in self._directory.glob('*.png') if p.is_file())
                if total + len(data) > MAX_SOURCE_STORE:
                    raise ValueError('The local source-image store is full. Existing source images were retained.')
                atomic_write(destination, data)
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise ValueError('Source-image preservation could not be verified.')
            candidate['provenance'] = {'kind': ('png-user-supplied-structure' if manual else
                'png-optical-reconstruction' if optical else 'png-metadata-component-reconstruction'),
                'sourceSha256': digest, 'sourceName': current['name'],
                'sourceStorage': 'local-profile/import-sources',
                'selectedComponents': list(selected_ids), 'warnings': candidate['warnings'],
                'imageCorrespondence': 'user-reviewed; not independently verified',
                'engine': current['result']['engine']}
            if optical:
                candidate['provenance']['recognition'] = copy.deepcopy(optical['recognition'])
            if manual:
                candidate['provenance']['suppliedStructure'] = copy.deepcopy(manual['suppliedStructure'])
                candidate['provenance']['suppliedGraph'] = copy.deepcopy(candidate['graph'])
            if needs_crop:
                candidate['provenance']['reviewedRegion'] = dict(review['region'])
        return candidate

    def close(self):
        with self._lock:
            self._closed = True
            if self._current is not None:
                self._cancel_optical(self._current)
                self._cancel_manual(self._current)
            self._current = None
            if self._pending is not None:
                self._pending['cancelled'] = True
                self._pending['cancel_event'].set()
        self._jobs.close()
        preview_closed = self._preview_jobs.close()
        optical_closed = self._optical_jobs.close() if self._optical_jobs is not None else True
        preparation_closed = (self._preparation_service.shutdown(timeout_seconds=2.0)
                              if self._owns_preparation_service else True)
        with self._lock:
            self._current = None
        return preview_closed and optical_closed and preparation_closed
