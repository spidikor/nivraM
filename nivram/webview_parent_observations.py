"""Optional scalar WebView lifetime facts, separate from cleanup authority.

Native identities are compared by the caller and never enter this capture.
Each named phase freezes its first packet independently. Later callbacks can
inform a later phase, but cannot rewrite an earlier packet or certify cleanup.
Snapshots and phase freezing make one nonblocking capture-lock attempt.
"""
import threading


MAX_ORDER = 65535
MAX_CALLBACKS = 255
PHASES = frozenset(('shutdown_return', 'window_loop_return', 'handle_wait_return'))
CALLBACK_STAGES = frozenset(('not_entered', 'entered', 'browser_checked',
    'profile_checked', 'verified', 'rejected', 'failed'))
CALLBACK_FAILURES = frozenset(('browser_identity_read_failed',
    'profile_identity_read_failed', 'exit_kind_read_failed', 'receipt_publish_failed'))
DISPATCH_STATES = frozenset(('not_attempted', 'requested', 'queued', 'failed'))
HOOK_STATES = frozenset(('not_attempted', 'installed', 'failed'))
PROBE_STATES = frozenset(('unavailable', 'read', 'failed'))
_ORDERS = ('first_callback_order', 'matching_exit_order', 'dispose_enter_order',
    'dispose_return_order', 'post_dispose_pulse_order', 'form_closed_order')
_OPTIONAL_FLAGS = ('exit_callback_browser_match', 'exit_callback_profile_match',
    'dispose_on_ui', 'post_dispose_pulse_on_ui')
_FACTS = ('sequence', 'exit_callback_count', 'exit_callback_stage',
    'exit_callback_browser_match', 'exit_callback_profile_match',
    'exit_callback_failure', 'first_callback_order', 'matching_exit_order',
    'dispose_dispatch', 'dispose_enter_order', 'dispose_return_order',
    'dispose_on_ui', 'post_dispose_pulse_dispatch', 'post_dispose_pulse_order',
    'post_dispose_pulse_on_ui', 'form_closed_hook_status', 'form_closed_order',
    'pinned_handle_probe_status', 'pinned_handle_exited', 'guard_tick_count')
_FIELDS = ('available', 'status', *_FACTS)


def _unknown(status='unavailable'):
    return {'available': False, 'status': status, **dict.fromkeys(_FACTS)}


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def webview_parent_diagnostic(value):
    """Return a fresh exact scalar schema without calling custom hooks."""
    if (type(value) is not dict or len(value) != len(_FIELDS)
            or any(type(key) is not str for key in value)
            or set(value) != set(_FIELDS) or type(value['status']) is not str):
        return _unknown()
    status = value['status']
    if status in ('busy', 'unavailable'):
        if value['available'] is not False or any(value[key] is not None for key in _FACTS):
            return _unknown()
        return _unknown(status)
    if (status != 'valid' or value['available'] is not True
            or not _integer(value['sequence'], 0, MAX_ORDER)
            or not _integer(value['exit_callback_count'], 0, MAX_CALLBACKS)
            or type(value['exit_callback_stage']) is not str
            or value['exit_callback_stage'] not in CALLBACK_STAGES
            or any(value[key] is not None and type(value[key]) is not bool
                   for key in _OPTIONAL_FLAGS)
            or (value['exit_callback_failure'] is not None
                and (type(value['exit_callback_failure']) is not str
                     or value['exit_callback_failure'] not in CALLBACK_FAILURES))
            or any(value[key] is not None
                   and not _integer(value[key], 1, value['sequence']) for key in _ORDERS)
            or any(type(value[key]) is not str or value[key] not in DISPATCH_STATES
                   for key in ('dispose_dispatch', 'post_dispose_pulse_dispatch'))
            or type(value['form_closed_hook_status']) is not str
            or value['form_closed_hook_status'] not in HOOK_STATES
            or type(value['pinned_handle_probe_status']) is not str
            or value['pinned_handle_probe_status'] not in PROBE_STATES
            or (value['guard_tick_count'] is not None
                and not _integer(value['guard_tick_count'], 0, MAX_ORDER))):
        return _unknown()
    stage = value['exit_callback_stage']
    browser, profile = value['exit_callback_browser_match'], value['exit_callback_profile_match']
    failure = value['exit_callback_failure']
    if (value['exit_callback_count'] == 0
            and (stage != 'not_entered' or any(value[key] is not None for key in
                 ('first_callback_order', 'matching_exit_order',
                  'exit_callback_browser_match', 'exit_callback_profile_match', 'exit_callback_failure')))):
        return _unknown()
    if (value['exit_callback_count'] > 0
            and (stage == 'not_entered' or value['first_callback_order'] is None)):
        return _unknown()
    if (profile is not None and browser is not True
            or stage == 'entered' and (browser is not None or profile is not None or failure is not None)
            or stage == 'browser_checked' and (browser is not True or profile is not None or failure is not None)
            or stage == 'profile_checked' and (browser is not True or profile is not True or failure is not None)
            or stage == 'verified' and (browser is not True or profile is not True
                                        or failure is not None or value['matching_exit_order'] is None)
            or stage == 'rejected' and (failure is not None or not
                (browser is False and profile is None or browser is True and profile is False))
            or stage == 'failed' and failure is None
            or stage != 'failed' and failure is not None
            or value['matching_exit_order'] is not None and value['first_callback_order'] is None):
        return _unknown()
    if ((value['dispose_enter_order'] is None
             and (value['dispose_return_order'] is not None or value['dispose_on_ui'] is not None))
            or (value['dispose_enter_order'] is not None and type(value['dispose_on_ui']) is not bool)
            or (value['dispose_return_order'] is not None
                and value['dispose_return_order'] < value['dispose_enter_order'])
            or (value['post_dispose_pulse_order'] is None
                and value['post_dispose_pulse_on_ui'] is not None)
            or (value['post_dispose_pulse_order'] is not None
                and (value['dispose_return_order'] is None
                     or type(value['post_dispose_pulse_on_ui']) is not bool
                     or value['post_dispose_pulse_order'] < value['dispose_return_order']))
            or (value['pinned_handle_probe_status'] == 'read'
                and type(value['pinned_handle_exited']) is not bool)
            or (value['pinned_handle_probe_status'] != 'read'
                and value['pinned_handle_exited'] is not None)):
        return _unknown()
    return {key: value[key] for key in _FIELDS}


class ParentWebViewCapture:
    """One session's fixed facts; no native calls occur under this lock.

Marks hold a lock only over bounded in-memory scalars. Snapshots never wait.
Each of the three phase slots uses builtin setdefault to preserve its first
packet, including a busy packet, under the pinned CPython runtime.
"""

    def __init__(self):
        self._lock = threading.Lock()
        self._configured = False
        self._facts = None
        self._frozen = {}

    def configure(self):
        with self._lock:
            if self._configured:
                raise ValueError('A parent WebView capture belongs to one session.')
            self._configured = True
            self._facts = {'available': True, 'status': 'valid',
                'sequence': 0, 'exit_callback_count': 0, 'exit_callback_stage': 'not_entered',
                'exit_callback_browser_match': None, 'exit_callback_profile_match': None,
                'exit_callback_failure': None, **dict.fromkeys(_ORDERS),
                'dispose_dispatch': 'not_attempted', 'dispose_on_ui': None,
                'post_dispose_pulse_dispatch': 'not_attempted', 'post_dispose_pulse_on_ui': None,
                'form_closed_hook_status': 'not_attempted',
                'pinned_handle_probe_status': 'unavailable', 'pinned_handle_exited': None,
                'guard_tick_count': None}

    def _change(self, callback):
        with self._lock:
            if self._facts is not None:
                callback(self._facts)

    @staticmethod
    def _order(facts, field):
        if facts[field] is None:
            facts['sequence'] = min(MAX_ORDER, facts['sequence'] + 1)
            facts[field] = facts['sequence']

    def callback_entered(self):
        def update(facts):
            self._order(facts, 'first_callback_order')
            facts.update(exit_callback_count=min(MAX_CALLBACKS, facts['exit_callback_count'] + 1),
                exit_callback_stage='entered', exit_callback_browser_match=None,
                exit_callback_profile_match=None, exit_callback_failure=None)
        self._change(update)

    def callback_browser_checked(self, match):
        if type(match) is not bool:
            raise ValueError('A browser identity observation must be boolean.')
        self._change(lambda facts: facts.update(exit_callback_browser_match=match,
            exit_callback_stage='browser_checked' if match else 'rejected'))

    def callback_profile_checked(self, match):
        if type(match) is not bool:
            raise ValueError('A profile identity observation must be boolean.')
        self._change(lambda facts: facts.update(exit_callback_profile_match=match,
            exit_callback_stage='profile_checked' if match else 'rejected'))

    def callback_verified(self):
        def update(facts):
            self._order(facts, 'matching_exit_order')
            facts['exit_callback_stage'] = 'verified'
        self._change(update)

    def callback_failed(self, code):
        if type(code) is not str or code not in CALLBACK_FAILURES:
            raise ValueError('Unsupported callback failure observation.')
        self._change(lambda facts: facts.update(exit_callback_stage='failed', exit_callback_failure=code))

    def dispose_dispatch(self, status):
        if type(status) is not str or status not in DISPATCH_STATES:
            raise ValueError('Unsupported disposal dispatch observation.')
        self._change(lambda facts: facts.update(dispose_dispatch=status))

    def dispose_entered(self, *, on_ui):
        if type(on_ui) is not bool:
            raise ValueError('UI affinity observation must be boolean.')
        def update(facts):
            if facts['dispose_enter_order'] is None:
                self._order(facts, 'dispose_enter_order')
                facts['dispose_on_ui'] = on_ui
        self._change(update)

    def dispose_returned(self):
        self._change(lambda facts: self._order(facts, 'dispose_return_order'))

    def pulse_dispatch(self, status):
        if type(status) is not str or status not in DISPATCH_STATES:
            raise ValueError('Unsupported pulse dispatch observation.')
        self._change(lambda facts: facts.update(post_dispose_pulse_dispatch=status))

    def pulse_seen(self, *, on_ui):
        if type(on_ui) is not bool:
            raise ValueError('UI affinity observation must be boolean.')
        def update(facts):
            if facts['post_dispose_pulse_order'] is None:
                self._order(facts, 'post_dispose_pulse_order')
                facts['post_dispose_pulse_on_ui'] = on_ui
        self._change(update)

    def form_closed_hook(self, status):
        if type(status) is not str or status not in HOOK_STATES:
            raise ValueError('Unsupported FormClosed hook observation.')
        self._change(lambda facts: facts.update(form_closed_hook_status=status))

    def form_closed(self):
        self._change(lambda facts: self._order(facts, 'form_closed_order'))

    def pinned_handle_probe(self, status, *, exited=None):
        if (type(status) is not str or status not in PROBE_STATES
                or status == 'read' and type(exited) is not bool
                or status != 'read' and exited is not None):
            raise ValueError('Unsupported pinned handle observation.')
        self._change(lambda facts: facts.update(pinned_handle_probe_status=status,
                                                pinned_handle_exited=exited))

    def guard_attached(self):
        self._change(lambda facts: facts.update(guard_tick_count=0)
                     if facts['guard_tick_count'] is None else None)

    def guard_tick(self):
        self._change(lambda facts: facts.update(guard_tick_count=min(MAX_ORDER,
            (facts['guard_tick_count'] or 0) + 1)))

    def snapshot(self):
        if not self._lock.acquire(blocking=False):
            return _unknown('busy')
        try:
            return webview_parent_diagnostic(self._facts)
        finally:
            self._lock.release()

    def freeze(self, phase):
        if type(phase) is not str or phase not in PHASES:
            raise ValueError('Unsupported parent WebView phase.')
        frozen = self._frozen.get(phase)
        if frozen is not None:
            return webview_parent_diagnostic(frozen)
        if not self._lock.acquire(blocking=False):
            return webview_parent_diagnostic(self._frozen.setdefault(phase, _unknown('busy')))
        try:
            return webview_parent_diagnostic(self._frozen.setdefault(phase,
                webview_parent_diagnostic(self._facts)))
        finally:
            self._lock.release()
