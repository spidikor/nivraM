"""Close review with bounded caller waits and one outstanding native evaluation.

The native WebView call itself cannot be cancelled safely. A timeout therefore
does not submit another call or claim the worker stopped. A declined close may
schedule one matching release after that call eventually settles.
"""
import math
import re
import threading


_BEGIN = 'window.nivramDocument?.beginCloseReview?.() ?? window.nivramDocument?.getSnapshot() ?? null'
_RECHECK = 'window.nivramDocument?.getSnapshot() ?? null'
_END = 'window.nivramDocument?.endCloseReview?.(); true'
_GENERATION = re.compile(r'[0-9a-f]{32}')
_RECOVERY_ID = re.compile(r'[1-9][0-9]{0,19}-[0-9a-f]{32}')
_MAX_REVISION = 9007199254740991


def _confirmed_recovery(receipt, snapshot):
    """Only the exact reviewed generation/revision may carry saved assurance."""
    return (isinstance(receipt, dict)
            and set(receipt) == {'status', 'id', 'generation', 'revision'}
            and receipt['status'] == 'recovered-copy-saved'
            and isinstance(receipt['id'], str) and _RECOVERY_ID.fullmatch(receipt['id']) is not None
            and receipt['generation'] == snapshot['recoveryGeneration']
            and type(receipt['revision']) is int and receipt['revision'] == snapshot['revision'])


class _Evaluation:
    def __init__(self, expression, token, stage):
        self.expression, self.token, self.stage = expression, token, stage
        self.completed = threading.Event()
        self.value = self.error = None


class _CloseEvaluations:
    """Serialize native calls; idle workers exit and stalled ones are never replaced."""
    def __init__(self, evaluate):
        self._evaluate = evaluate
        self._lock = threading.Lock()
        self._request = self._worker = None
        self._review_token = self._release_token = None
        self._closed = False
        self._terminated = False

    def _start_locked(self, request):
        self._request = request
        self._worker = threading.Thread(target=self._run, args=(request,),
                                        name='nivraM-close-evaluation', daemon=True)
        self._worker.start()

    def _run(self, request):
        while request is not None:
            try:
                with self._lock:
                    available = not self._closed
                if not available:
                    raise RuntimeError('Close evaluation stopped before dispatch.')
                request.value = self._evaluate(request.expression)
            except BaseException as error:
                request.error = error
            finally:
                with self._lock:
                    following = None
                    if (request.stage == 'end' and request.error is None
                            and self._review_token == request.token):
                        self._review_token = None
                    if self._release_token == request.token:
                        self._release_token = None
                        if not self._closed and request.stage != 'end':
                            following = _Evaluation(_END, request.token, 'end')
                    self._request = following
                    if following is None:
                        self._worker = None
                    # Publish completion only after any matching release owns
                    # the lane. A new review cannot overtake that release.
                    request.completed.set()
                request = following

    @staticmethod
    def _wait(request, timeout):
        if not request.completed.wait(timeout):
            raise TimeoutError('The native close evaluation is still outstanding.')
        if request.error is not None:
            raise request.error
        return request.value

    def evaluate(self, expression, token, stage, timeout):
        with self._lock:
            if self._closed or self._request is not None:
                raise RuntimeError('A previous close evaluation is unavailable or still outstanding.')
            if stage == 'begin':
                self._review_token = token
            elif self._review_token != token:
                raise RuntimeError('This close review no longer owns the editor state.')
            request = _Evaluation(expression, token, stage)
            self._start_locked(request)
        return self._wait(request, timeout)

    def release(self, token, timeout):
        with self._lock:
            if self._closed or self._review_token != token:
                return
            request = self._request
            if request is not None:
                if request.token == token and request.stage != 'end':
                    # No second native call while the first is outstanding.
                    # Only this attempt may release its late begin/recheck.
                    self._release_token = token
                return
            request = _Evaluation(_END, token, 'end')
            self._start_locked(request)
        self._wait(request, timeout)

    def close(self, *, permanent=False):
        with self._lock:
            self._closed = True
            self._terminated = self._terminated or permanent
            self._release_token = None

    def resume_after_failed_destroy(self, timeout):
        """Resume the existing lane without replacing an outstanding native call."""
        with self._lock:
            if self._terminated:
                return
            self._closed = False
            # The attempted close may have followed a timeout from an earlier
            # review. Only that review's token owns the editor suspension.
            token = self._review_token
        if token is not None:
            self.release(token, timeout)


class CloseCoordinator:
    """Review work without stopping services; native FormClosed owns teardown."""
    def __init__(self, window, store, *, evaluation_timeout=5, save_appearance=None):
        if (isinstance(evaluation_timeout, bool) or not isinstance(evaluation_timeout, (int, float))
                or not math.isfinite(evaluation_timeout) or not 0 < evaluation_timeout <= 60):
            raise ValueError('Close evaluation timeout must be finite, positive and at most 60 seconds.')
        self.window = window
        self.store = store
        self._save_appearance = save_appearance
        self._lock = threading.Lock()
        self._pending = False
        self._approved = False
        self._closed = False
        self._attempt = 0
        self._evaluation_timeout = evaluation_timeout
        self._evaluations = _CloseEvaluations(window.evaluate_js)

    def observe_closed(self):
        """Called at entry to the owned native FormClosed callback, before vendor code."""
        with self._lock:
            self._closed = True
            self._approved = True
        self._evaluations.close(permanent=True)

    def _has_closed(self):
        # The native observer precedes vendor code. The public event is useful
        # as additional evidence, never a successful destroy() return value.
        with self._lock:
            if self._closed:
                return True
        return self.window.events.closed.is_set()

    def request_close(self):
        with self._lock:
            if self._approved:
                return True
            if self._pending:
                return False
            self._pending = True
            self._attempt += 1
            token = self._attempt
        threading.Thread(target=self._review_close, args=(token,), name='nivraM-close', daemon=True).start()
        return False

    def _snapshot(self, expression, token, stage):
        value = self._evaluations.evaluate(expression, token, stage, self._evaluation_timeout)
        if (not isinstance(value, dict) or type(value.get('dirty')) is not bool
                or type(value.get('revision')) is not int or not 0 <= value['revision'] <= _MAX_REVISION
                or (value['dirty'] and (not isinstance(value.get('document'), dict)
                                       or 'recoveryGeneration' not in value))
                or ('recoveryGeneration' in value and (
                    not isinstance(value['recoveryGeneration'], str)
                    or _GENERATION.fullmatch(value['recoveryGeneration']) is None))):
            raise ValueError('The editor did not return a valid close-review snapshot.')
        return value

    def _confirm_unverified(self, recovered):
        message = ('A recovery copy of the reviewed revision was saved, but the latest editor state '
                   'could not be verified. More recent changes may not be recovered. Close anyway?'
                   if recovered else
                   'The editor could not confirm its latest state or a current recovery copy. '
                   'Unsaved changes may not be recovered. Close anyway?')
        return self.window.create_confirmation_dialog('Unable to verify unsaved work', message)

    def _review_close(self, token=None):
        if token is None:
            with self._lock:
                if self._pending or self._approved:
                    return
                self._pending = True
                self._attempt += 1
                token = self._attempt
        shutdown_started = False
        try:
            try:
                snapshot = self._snapshot(_BEGIN, token, 'begin')
            except Exception:
                if not self._confirm_unverified(False):
                    return
                snapshot = None
            if snapshot and snapshot.get('dirty'):
                recovered = False
                try:
                    receipt = self.store.autosave(snapshot['document'], snapshot['recoveryGeneration'], snapshot['revision'])
                    recovered = _confirmed_recovery(receipt, snapshot)
                    message = ('A recovery copy was saved. Close without saving this project to its chosen file?'
                               if recovered else
                               'The recovery copy could not be confirmed. Unsaved changes may not be recovered. Close anyway?')
                except Exception:
                    message = 'The recovery copy could not be confirmed. Unsaved changes may not be recovered. Close anyway?'
                if not self.window.create_confirmation_dialog('Unsaved project', message):
                    return
                # A later edit during the dialog must receive another close review.
                try:
                    latest = self._snapshot(_RECHECK, token, 'recheck')
                except Exception:
                    if not self._confirm_unverified(recovered):
                        return
                else:
                    if (latest['revision'] != snapshot['revision']
                            or latest.get('recoveryGeneration') != snapshot['recoveryGeneration']
                            or latest.get('document') != snapshot['document']):
                        return
            if self._save_appearance is not None:
                try:
                    self._save_appearance(snapshot.get('appearance') if snapshot else None)
                except Exception:
                    if not self.window.create_confirmation_dialog('Appearance could not be saved',
                            'The light/dark choice could not be saved for your next session. '
                            'Keep the app open to try again, or close anyway?'):
                        return
            # A late result is never a close decision. Stop queued releases
            # before destruction; an already-entered native call may
            # still settle, but its worker has no save or destroy callback.
            self._evaluations.close()
            shutdown_started = True
            with self._lock:
                self._approved = True
            self.window.destroy()
        finally:
            if self._has_closed():
                self.observe_closed()
            else:
                with self._lock:
                    if not self._closed:
                        self._approved = False
                try:
                    if shutdown_started:
                        # A failed or vetoed native close leaves all services
                        # untouched. Release only this editor review's token.
                        self._evaluations.resume_after_failed_destroy(self._evaluation_timeout)
                    else:
                        self._evaluations.release(token, self._evaluation_timeout)
                except Exception:
                    pass
            with self._lock:
                if token == self._attempt:
                    self._pending = False
