"""Download ownership and ordering; real native behavior needs acceptance too."""
from collections import deque
import json
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram.webview_downloads import (
    DownloadError, _format, _suggested_name, _valid_destination, attach_downloads,
)


BASE = 'http://127.0.0.1:54321/'
BLOB = 'blob:' + BASE + '11111111-2222-3333-4444-555555555555'


class Event:
    def __init__(self):
        self.handlers = []
        self.add_count = self.remove_count = 0

    def __iadd__(self, handler):
        self.add_count += 1
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.remove_count += 1
        if handler not in self.handlers:
            raise ValueError('unknown event handler')
        self.handlers.remove(handler)
        return self

    def fire(self, *args):
        for handler in tuple(self.handlers):
            handler(*args)


class Control:
    def __init__(self):
        self.CoreWebView2 = None
        self.CoreWebView2InitializationCompleted = Event()
        self.pending = deque()
        self.InvokeRequired = True
        self.disposed = False
        self.dispatch_error = False

    def BeginInvoke(self, action):
        if self.dispatch_error:
            raise OSError('sensitive personal path')
        self.pending.append(action)

    def pump(self):
        while self.pending:
            self.pending.popleft()()

    def Dispose(self):
        self.disposed = True


class Browser:
    def __init__(self, control):
        self.webview = control
        self.original_calls = 0

    def ready(self):
        self.webview.CoreWebView2.DownloadStarting += self.on_download_starting

    def on_download_starting(self, sender, args):
        self.original_calls += 1


class Operation:
    def __init__(self, mime='image/png', uri=BLOB):
        self.Uri, self.MimeType = uri, mime
        self.StateChanged = Event()
        self._state = 'InProgress'
        self.cancel_count = 0
        self.ui_thread = threading.get_ident()
        self.cancel_stalls = False

    @property
    def State(self):
        if threading.get_ident() != self.ui_thread:
            raise AssertionError('COM state accessed from a worker')
        return self._state

    def transition(self, value):
        self._state = value
        self.StateChanged.fire(self, None)

    def Cancel(self):
        self.cancel_count += 1
        if not self.cancel_stalls:
            self.transition('Interrupted')


class Args:
    def __init__(self, operation, name='synthetic.png'):
        self.DownloadOperation = operation
        self.ResultFilePath = name
        self.Cancel = False
        self.Handled = False
        self.complete_count = 0
        self.deferral_count = 0
        self.completion_error = False

    def GetDeferral(self):
        self.deferral_count += 1
        return SimpleNamespace(Complete=self.complete)

    def complete(self):
        self.complete_count += 1
        if self.completion_error:
            raise OSError('private payload or personal path')
        if self.Cancel:
            self.DownloadOperation.Cancel()


class Dialog:
    chosen = r'C:\exports\synthetic.png'
    instances = []
    showing_event = False

    def __init__(self, owner, suggested, format_info):
        self.owner, self.suggested, self.format_info = owner, suggested, format_info
        self.disposed = False
        self.cancelled = False
        self.instances.append(self)

    def choose(self):
        if self.showing_event:
            raise AssertionError('Modal dialog opened inside the WebView event')
        if isinstance(self.chosen, Exception):
            raise self.chosen
        return self.chosen

    def cancel(self):
        self.cancelled = True
        return True

    def dispose(self):
        self.disposed = True


def setup(*, permission_event=None, include_permission=True):
    control = Control()
    browser = Browser(control)
    window = SimpleNamespace(native=SimpleNamespace(webview=control, browser=browser),
                             events=SimpleNamespace(before_show=Event()))
    # Model native policy's posted vendor-ready action, registered first.
    control.CoreWebView2InitializationCompleted += lambda *_: control.BeginInvoke(browser.ready)
    adapter = attach_downloads(window, BASE)
    window.events.before_show.fire()
    control.CoreWebView2 = SimpleNamespace(DownloadStarting=Event())
    if include_permission:
        control.CoreWebView2.PermissionRequested = permission_event if permission_event is not None else Event()
    control.CoreWebView2InitializationCompleted.fire(control, SimpleNamespace(IsSuccess=True))
    control.pump()
    return adapter, window, control, browser


class PermissionArgs:
    """Only the four observed SDK getters exist; every write/action fails."""
    def __init__(self, *, kind='MultipleAutomaticDownloads', user=True, state='Default',
                 uri=BASE + 'private-path?token=PRIVATE', failure=None):
        object.__setattr__(self, 'values', dict(PermissionKind=kind, IsUserInitiated=user, State=state, Uri=uri))
        object.__setattr__(self, 'failure', failure)
        object.__setattr__(self, 'reads', [])

    def __getattr__(self, name):
        self.reads.append(name)
        if name == self.failure: raise OSError('PRIVATE request details')
        if name not in self.values: raise AssertionError('Unexpected permission action/getter: ' + name)
        return self.values[name]

    def __setattr__(self, name, value):
        raise AssertionError('Permission observer wrote ' + name)


class PermissionObserverTests(unittest.TestCase):
    def setUp(self):
        self.action_patch = patch('nivram.webview_downloads._ui_action', side_effect=lambda f: f)
        self.action_patch.start(); self.addCleanup(self.action_patch.stop)

    def observe(self, adapter, control, args=None):
        args = PermissionArgs() if args is None else args
        control.CoreWebView2.PermissionRequested.fire(control.CoreWebView2, args)
        return adapter.snapshot()['permission_observer']

    def test_metadata_only_observation_preserves_request_and_download_state(self):
        adapter, _, control, _ = setup()
        before = adapter.snapshot(); args = PermissionArgs()
        result = self.observe(adapter, control, args)
        self.assertEqual(set(args.reads), {'PermissionKind', 'IsUserInitiated', 'State', 'Uri'})
        self.assertEqual(len(args.reads), 4)
        self.assertEqual(result, {'status':'observing', 'seen':1, 'read_failures':0, 'dropped':0,
            'events':[{'sequence':1, 'download_sequence':0, 'kind':7, 'is_user_initiated':True,
                       'state':'Default', 'origin_matches':True}]})
        after = adapter.snapshot()
        self.assertEqual({k:v for k,v in before.items() if k != 'permission_observer'},
                         {k:v for k,v in after.items() if k != 'permission_observer'})
        self.assertEqual(control.pending, deque()); self.assertFalse(control.disposed)
        for token in ('PRIVATE', 'private-path', '127.0.0.1', 'token=', 'PermissionArgs'):
            self.assertNotIn(token, json.dumps(after))

    def test_origin_check_is_exact_origin_with_path_query_and_bounded_input(self):
        adapter, _, control, _ = setup()
        cases = [(BASE, True), (BASE.rstrip('/'), True), (BASE + 'path?PRIVATE=1', True),
                 ('http://127.0.0.1:54322/', False), ('http://127.0.0.1:54321@evil.invalid/', False),
                 ('https://127.0.0.1:54321/', False), ('http://localhost:54321/', False),
                 ('blob:' + BASE + 'id', False), (' ' + BASE, False), (BASE + 'a' * 4096, False)]
        for uri, matches in cases:
            with self.subTest(matches=matches):
                value = self.observe(adapter, control, PermissionArgs(uri=uri))['events'][-1]
                self.assertEqual(value['origin_matches'], matches)
        self.assertNotIn('PRIVATE', json.dumps(adapter.snapshot()))

    def test_unknown_enums_are_bounded_without_arbitrary_strings(self):
        adapter, _, control, _ = setup()
        value = self.observe(adapter, control, PermissionArgs(kind='PRIVATE', state='PRIVATE'))['events'][-1]
        self.assertEqual(value['kind'], -1); self.assertEqual(value['state'], 'Unrecognized')
        for kind, expected in [('UnknownPermission',0), ('ClipboardRead',6), ('PersistentStorage',13),
                               ('MidiSystemExclusiveMessageAccess',11), ('MidiSystemExclusiveMessages',11)]:
            self.assertEqual(self.observe(adapter, control, PermissionArgs(kind=kind))['events'][-1]['kind'], expected)
        self.assertNotIn('PRIVATE', json.dumps(adapter.snapshot()))

    def test_getter_errors_count_without_fabricated_event_or_native_failure(self):
        adapter, _, control, _ = setup()
        for key in ('PermissionKind', 'IsUserInitiated', 'State', 'Uri'):
            self.observe(adapter, control, PermissionArgs(failure=key))
        result = adapter.snapshot()
        self.assertEqual(result['permission_observer']['seen'], 4)
        self.assertEqual(result['permission_observer']['read_failures'], 4)
        self.assertEqual(result['permission_observer']['events'], [])
        self.assertEqual(result['permission_observer']['status'], 'observing')
        self.assertEqual(result['failures'], 0); self.assertEqual(result['trace_count'], 0)
        self.assertFalse(control.disposed); self.assertNotIn('PRIVATE', json.dumps(result))

    def test_wrong_thread_does_not_read_native_permission_args(self):
        adapter, _, control, _ = setup(); args = PermissionArgs()
        worker_call(control, lambda: self.observe(adapter, control, args), pump=False)
        self.assertEqual(args.reads, [])
        result = adapter.snapshot()['permission_observer']
        self.assertEqual(result['seen'], 1); self.assertEqual(result['read_failures'], 1)
        self.assertEqual(result['events'], [])

    def test_trace_bound_detached_snapshots_and_download_sequence(self):
        adapter, _, control, _ = setup()
        for _ in range(3): adapter._trace('start')
        for _ in range(7): self.observe(adapter, control)
        result = adapter.snapshot()['permission_observer']
        self.assertEqual((result['seen'], result['dropped']), (7, 3))
        self.assertEqual([row['sequence'] for row in result['events']], [4,5,6,7])
        self.assertEqual({row['download_sequence'] for row in result['events']}, {3})
        result['events'][0]['kind'] = 'PRIVATE'; result['status'] = 'PRIVATE'
        self.assertNotIn('PRIVATE', json.dumps(adapter.snapshot()))
        class NoNativeReads:
            def __getattribute__(self, name): raise AssertionError('snapshot read native ' + name)
        control.CoreWebView2 = NoNativeReads(); adapter._core = control.CoreWebView2
        self.assertEqual(worker_call(control, adapter.snapshot, pump=False)['permission_observer']['seen'], 7)

    def test_permission_counters_saturate_and_invalid_boolean_is_not_coerced(self):
        adapter, _, control, _ = setup()
        for _ in range(4): self.observe(adapter, control)
        for key in ('seen', 'read_failures', 'dropped'):
            adapter._permission_values[key] = 999_999_999
        for _ in range(3): self.observe(adapter, control)
        for _ in range(3): self.observe(adapter, control, PermissionArgs(user='PRIVATE'))
        value = adapter.snapshot()['permission_observer']
        self.assertEqual((value['seen'], value['read_failures'], value['dropped']),
                         (1_000_000_000, 1_000_000_000, 1_000_000_000))
        self.assertTrue(all(1 <= row['sequence'] <= 1_000_000_000 for row in value['events']))
        self.assertNotIn('PRIVATE', json.dumps(value))

    def test_individual_download_cancellation_keeps_permission_observer_attached(self):
        adapter, _, control, _ = setup(); event = control.CoreWebView2.PermissionRequested
        self.assertTrue(worker_call(control, lambda: adapter.cancel(1, .1)))
        self.assertEqual(event.remove_count, 0)
        self.assertEqual(self.observe(adapter, control)['status'], 'observing')

    def test_subscribe_and_ui_shutdown_detach_exact_delegate_once(self):
        event = Event(); adapter, _, control, _ = setup(permission_event=event)
        handler = event.handlers[0]
        adapter._on_initialized(control, SimpleNamespace(IsSuccess=True)); control.pump()
        self.assertEqual(event.add_count, 1); self.assertEqual(event.handlers, [handler])
        adapter.begin_shutdown_on_ui(); adapter.begin_shutdown_on_ui()
        result = worker_call(control, lambda: adapter.shutdown(.1))
        self.assertEqual(event.remove_count, 1); self.assertEqual(event.handlers, [])
        self.assertEqual(result['permission_observer']['status'], 'detached')
        args = PermissionArgs(); handler(None, args)
        self.assertEqual(args.reads, []); self.assertEqual(adapter.snapshot()['permission_observer']['seen'], 0)

    def test_worker_shutdown_dispatches_detach_to_owned_ui_thread(self):
        main_thread = threading.get_ident()
        class UiEvent(Event):
            def __isub__(self, handler):
                if threading.get_ident() != main_thread: raise AssertionError('Wrong detach thread')
                return super().__isub__(handler)
        event = UiEvent(); adapter, _, control, _ = setup(permission_event=event)
        result = worker_call(control, lambda: adapter.shutdown(.1))
        self.assertEqual(event.remove_count, 1)
        self.assertEqual(result['permission_observer']['status'], 'detached')

    def test_missing_or_failed_subscription_does_not_change_download_policy(self):
        class BadEvent(Event):
            def __iadd__(self, handler):
                self.add_count += 1; raise OSError('PRIVATE subscription')
        for present in (False, True):
            event = BadEvent()
            adapter, _, control, _ = setup(permission_event=event, include_permission=present)
            adapter._on_initialized(control, SimpleNamespace(IsSuccess=True)); control.pump()
            result = adapter.snapshot()
            self.assertEqual(result['status'], 'installed'); self.assertEqual(result['failures'], 0)
            self.assertEqual(result['permission_observer']['status'], 'subscribe_failed')
            self.assertEqual(event.add_count, 1 if present else 0); self.assertFalse(control.disposed)
            adapter.begin_shutdown_on_ui(); self.assertEqual(event.remove_count, 0)
            self.assertNotIn('PRIVATE', json.dumps(adapter.snapshot()))

    def test_detach_failure_is_separate_and_not_retried(self):
        class BadEvent(Event):
            def __isub__(self, handler):
                self.remove_count += 1; raise OSError('PRIVATE removal')
        event = BadEvent(); adapter, _, control, _ = setup(permission_event=event)
        adapter.begin_shutdown_on_ui()
        result = worker_call(control, lambda: adapter.shutdown(.1))
        self.assertEqual(event.remove_count, 1)
        self.assertEqual(result['permission_observer']['status'], 'detach_failed')
        self.assertEqual(result['failures'], 0); self.assertFalse(control.disposed)
        self.assertNotIn('PRIVATE', json.dumps(result))


def request(control, operation=None, name='synthetic.png'):
    args = Args(operation or Operation(), name)
    Dialog.showing_event = True
    try:
        control.CoreWebView2.DownloadStarting.fire(control.CoreWebView2, args)
    finally:
        Dialog.showing_event = False
    return args


def worker_call(control, callback, pump=True):
    result = {}
    def run():
        try:
            result['value'] = callback()
        except BaseException as error:
            result['error'] = error
    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic()+2
    while worker.is_alive() and time.monotonic() < deadline:
        if pump:
            control.pump()
        worker.join(.002)
    if worker.is_alive():
        raise AssertionError('Bounded worker did not return')
    if 'error' in result:
        raise result['error']
    return result['value']


class DownloadTests(unittest.TestCase):
    def test_cached_trace_distinguishes_admission_block_and_dialog_stages(self):
        adapter,_,control,_=setup()
        first=request(control,Operation('application/json'),name='reference.json')
        before=adapter.snapshot()
        self.assertEqual([r['event'] for r in before['trace']],['start','admitted','dispatch_requested','dispatch_returned'])
        self.assertEqual(before['trace'][1]['format'],'json'); self.assertEqual(len(Dialog.instances),0)
        blocked=request(control,Operation('text/csv;charset=utf-8'),name='reference.csv')
        self.assertTrue(blocked.Cancel)
        self.assertEqual(adapter.snapshot()['trace'][-1]['reason'],'active_download')
        self.assertEqual(adapter.snapshot()['trace'][-1]['format'],'csv')
        Dialog.chosen=r'C:\exports\reference.json'; control.pump(); first.DownloadOperation.transition('Completed')
        events=[r['event'] for r in adapter.snapshot()['trace']]
        for event in ('choose_entered','dialog_show_entered','dialog_returned','deferral_completed','settled'):
            self.assertIn(event,events)
        accepted=request(control,Operation('text/csv;charset=utf-8'),name='reference.csv')
        self.assertFalse(accepted.Cancel); self.assertEqual(adapter.snapshot()['accepted'],2)
        self.assertEqual(adapter.snapshot()['trace'][-2]['event'],'dispatch_requested')

    def test_diagnostic_snapshot_is_cached_bounded_detached_and_content_free(self):
        adapter,_,control,_=setup(); args=request(control)
        class NoNativeReads:
            def __getattribute__(self,name): raise AssertionError('snapshot read native '+name)
        record=adapter._records[0]
        record.operation=NoNativeReads(); record.args=NoNativeReads(); record.dialog=NoNativeReads(); record.deferral=NoNativeReads()
        for _ in range(45): adapter._trace('choose_entered',record)
        adapter._values['accepted']=10**30
        snapshot=adapter.snapshot()
        self.assertEqual(len(snapshot['trace']),16); self.assertGreater(snapshot['trace_dropped'],0)
        self.assertEqual(snapshot['accepted'],1_000_000_000)
        snapshot['trace'][0]['event']='PRIVATE'
        self.assertNotIn('PRIVATE',json.dumps(adapter.snapshot()))
        self.assertLess(len(json.dumps(adapter.snapshot()).encode()),7000)
        for forbidden in ('blob:', '127.0.0.1','synthetic.png','C:'):
            self.assertNotIn(forbidden,json.dumps(adapter.snapshot()))

    def setUp(self):
        Dialog.instances = []
        Dialog.chosen = r'C:\exports\synthetic.png'
        Dialog.showing_event = False
        self.patches = [patch('nivram.webview_downloads._ui_action', side_effect=lambda f: f),
                        patch('nivram.webview_downloads._SaveDialog', Dialog)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_vendor_handler_is_replaced_for_only_owned_window(self):
        adapter, window, control, browser = setup()
        other_control, other = Control(), None
        other = Browser(other_control)
        other_control.CoreWebView2 = SimpleNamespace(DownloadStarting=Event())
        other.ready()
        args = request(control)
        self.assertEqual(browser.original_calls, 0)
        self.assertEqual(adapter.snapshot()['status'], 'installed')
        self.assertEqual(len(control.CoreWebView2.DownloadStarting.handlers), 1)
        other_control.CoreWebView2.DownloadStarting.fire(None, args)
        self.assertEqual(other.original_calls, 1)
        with self.assertRaises(DownloadError):
            attach_downloads(window, BASE)

    @unittest.skipUnless(sys.platform == 'win32', 'Pinned Windows pythonnet contract')
    def test_real_pythonnet_rejects_removing_a_never_subscribed_event(self):
        # No window, WebView, timer start, or native UI session is created.
        import clr  # noqa: F401
        from System.Timers import Timer
        timer = Timer()
        def handler(sender, args):
            pass
        try:
            with self.assertRaisesRegex(ValueError, 'unknown event handler'):
                timer.Elapsed -= handler
            timer.Elapsed += handler
            timer.Elapsed -= handler
            with self.assertRaisesRegex(ValueError, 'unknown event handler'):
                timer.Elapsed -= handler
        finally:
            timer.Dispose()

    def test_dialog_is_posted_and_deferral_is_completed_after_choice(self):
        adapter, _, control, _ = setup()
        args = request(control)
        self.assertTrue(args.Handled)
        self.assertEqual((args.deferral_count, args.complete_count, len(Dialog.instances)), (1, 0, 0))
        control.pump()
        self.assertEqual(args.complete_count, 1)
        self.assertEqual(args.ResultFilePath, Dialog.chosen)
        self.assertTrue(Dialog.instances[0].disposed)
        self.assertEqual(adapter.snapshot()['completed'], 0)
        args.DownloadOperation.transition('Completed')
        receipt = worker_call(control, lambda: adapter.wait_for_download(timeout_seconds=.1))
        self.assertEqual(receipt['state'], 'Completed')
        self.assertTrue(receipt['settled'])

    def test_readable_or_chosen_path_cannot_fake_completion(self):
        adapter, _, control, _ = setup()
        request(control)
        control.pump()
        receipt = worker_call(control, lambda: adapter.wait_for_download(timeout_seconds=.01))
        self.assertEqual(receipt['state'], 'InProgress')
        self.assertFalse(receipt['settled'])

    def test_strict_current_origin_blob_only(self):
        for uri in (BASE+'export', 'https://example.com/x', 'data:image/png;base64,AAAA',
                    BLOB.replace('54321', '54322'), BLOB+'?q=1', BLOB+'#x',
                    BLOB.replace('127.0.0.1', 'localhost'), BLOB.replace('11111111', 'bad'),
                    BLOB.replace('http:', 'https:'), BLOB+'\n'):
            with self.subTest(uri=uri):
                adapter, _, control, _ = setup()
                args = request(control, Operation(uri=uri))
                control.pump()
                self.assertTrue(args.Cancel)
                self.assertEqual(args.deferral_count, 0)
                self.assertEqual(adapter.snapshot()['blocked'], 1)
        self.assertEqual(Dialog.instances, [])

    def test_known_mime_types_and_matching_extensions(self):
        for mime, name in [('image/svg+xml','figure.svg'), ('image/svg+xml; charset=utf-8','vector.svg'), ('image/png','figure.png'), ('chemical/x-mdl-molfile','structure.mol'),
                           ('application/json','structure.nivram'), ('application/json','copy.json'),
                           ('text/csv; charset=utf-8','table.csv')]:
            with self.subTest(mime=mime, name=name):
                adapter, _, control, _ = setup()
                Dialog.chosen = 'C:\\exports\\'+name
                args = request(control, Operation(mime), name)
                control.pump()
                self.assertFalse(args.Cancel)
                self.assertEqual(args.ResultFilePath, Dialog.chosen)
                args.DownloadOperation.transition('Completed')
                self.assertEqual(adapter.snapshot()['completed'], 1)
        for mime in ('text/html','image/svg','application/octet-stream','image/png;evil=1','image/svg+xml;evil=1'):
            adapter, _, control, _ = setup()
            self.assertTrue(request(control, Operation(mime)).Cancel)

    def test_native_chosen_unexpected_extension_is_not_rewritten(self):
        adapter, _, control, _ = setup()
        Dialog.chosen = r'C:\exports\protected.txt'
        args = request(control)
        control.pump()
        self.assertTrue(args.Cancel)
        self.assertEqual(args.ResultFilePath, 'synthetic.png')
        self.assertEqual(adapter.snapshot()['failure_code'], 'download_destination_rejected')

    def test_dialog_cancel_and_exception_always_complete_deferral(self):
        for chosen in (None, OSError('C:/Users/private/path and molecule')):
            adapter, _, control, _ = setup()
            Dialog.chosen = chosen
            args = request(control)
            control.pump()
            self.assertTrue(args.Cancel)
            self.assertEqual(args.complete_count, 1)
            self.assertEqual(adapter.snapshot()['active'], 0)
            self.assertTrue(Dialog.instances[-1].disposed)
            self.assertNotIn('private', json.dumps(adapter.snapshot()))

    def test_only_one_pending_download_is_accepted(self):
        adapter, _, control, _ = setup()
        first, second = request(control), request(control)
        self.assertFalse(first.Cancel)
        self.assertTrue(second.Cancel)
        self.assertEqual(second.deferral_count, 0)
        self.assertEqual(adapter.snapshot()['accepted'], 1)

    def test_completed_download_without_delivered_event_allows_next_download(self):
        adapter, _, control, _ = setup()
        first = request(control)
        control.pump()
        self.assertTrue(Dialog.instances[-1].disposed)
        self.assertEqual(first.complete_count, 1)
        # The operation can complete before its queued StateChanged callback.
        first.DownloadOperation._state = 'Completed'
        second = request(control)
        self.assertFalse(second.Cancel)
        self.assertEqual(second.deferral_count, 1)
        self.assertEqual(first.DownloadOperation.StateChanged.handlers, [])
        state = adapter.snapshot()
        self.assertEqual((state['accepted'], state['completed'], state['active']), (2, 1, 1))
        self.assertEqual(state['failures'], 0)
        third = request(control)
        self.assertTrue(third.Cancel)
        self.assertEqual(third.deferral_count, 0)

    def test_late_completion_callback_after_refresh_cannot_settle_twice(self):
        adapter, _, control, _ = setup()
        first = request(control)
        control.pump()
        late_callback = first.DownloadOperation.StateChanged.handlers[0]
        first.DownloadOperation._state = 'Completed'
        second = request(control)
        self.assertFalse(second.Cancel)
        before = adapter.snapshot()
        late_callback(first.DownloadOperation, None)
        late_callback(first.DownloadOperation, None)
        first.DownloadOperation.StateChanged.fire(first.DownloadOperation, None)
        self.assertEqual(adapter.snapshot(), before)
        self.assertEqual(first.complete_count, 1)
        self.assertEqual(first.DownloadOperation.cancel_count, 0)
        self.assertEqual(first.DownloadOperation.StateChanged.handlers, [])
        self.assertFalse(second.Cancel)
        self.assertEqual(second.complete_count, 0)

    def test_refresh_keeps_in_progress_and_unknown_downloads_exclusive(self):
        for state in ('InProgress', 'Unknown'):
            with self.subTest(state=state):
                adapter, _, control, _ = setup()
                first = request(control)
                control.pump()
                first.DownloadOperation._state = state
                second = request(control)
                self.assertTrue(second.Cancel)
                self.assertEqual(second.deferral_count, 0)
                snapshot = adapter.snapshot()
                self.assertEqual(snapshot['last_state'], state)
                self.assertEqual((snapshot['accepted'], snapshot['completed'], snapshot['active']), (1, 0, 1))
                self.assertEqual(len(first.DownloadOperation.StateChanged.handlers), 1)

    def test_refresh_state_read_failure_keeps_next_download_blocked(self):
        class UnreadableOperation(Operation):
            fail_state_read = False

            @property
            def State(self):
                if self.fail_state_read:
                    raise OSError('synthetic private detail')
                return super().State

        adapter, _, control, _ = setup()
        operation = UnreadableOperation()
        request(control, operation=operation)
        control.pump()
        operation.fail_state_read = True
        second = request(control)
        self.assertTrue(second.Cancel)
        self.assertEqual(second.deferral_count, 0)
        snapshot = adapter.snapshot()
        self.assertEqual((snapshot['accepted'], snapshot['completed'], snapshot['active']), (1, 0, 1))
        self.assertEqual(snapshot['failure_code'], 'download_state_unconfirmed')
        self.assertEqual(snapshot['failures'], 1)
        self.assertNotIn('private', json.dumps(snapshot))

    def test_refresh_cannot_settle_completed_operation_with_queued_dialog(self):
        adapter, _, control, _ = setup()
        first = request(control)
        first.DownloadOperation._state = 'Completed'
        second = request(control)
        self.assertTrue(second.Cancel)
        self.assertEqual(second.deferral_count, 0)
        self.assertEqual(first.complete_count, 0)
        self.assertEqual(Dialog.instances, [])
        state = adapter.snapshot()
        self.assertEqual((state['accepted'], state['completed'], state['active']), (1, 0, 1))

    def test_refresh_cannot_settle_while_dialog_is_showing_and_deferral_pending(self):
        adapter, _, control, _ = setup()
        first = request(control)
        observed = []

        class PendingDialog(Dialog):
            def choose(self):
                first.DownloadOperation._state = 'Completed'
                second = request(control)
                observed.append((second.Cancel, second.deferral_count,
                                 first.complete_count, adapter.snapshot()['active'],
                                 adapter.snapshot()['completed']))
                return self.chosen

        with patch('nivram.webview_downloads._SaveDialog', PendingDialog):
            control.pump()
        self.assertEqual(observed, [(True, 0, 0, 1, 0)])
        self.assertEqual(first.complete_count, 1)
        self.assertEqual(adapter.snapshot()['completed'], 1)

    def test_closing_and_policy_rejection_do_not_refresh_prior_native_state(self):
        for closing in (False, True):
            with self.subTest(closing=closing):
                adapter, _, control, _ = setup()
                first = request(control)
                control.pump()
                first.DownloadOperation._state = 'Completed'
                adapter._closing = closing
                operation = Operation() if closing else Operation(uri='https://example.com/file.png')
                with patch.object(adapter, '_observe') as observe:
                    second = request(control, operation=operation)
                observe.assert_not_called()
                self.assertTrue(second.Cancel)
                self.assertEqual(second.deferral_count, 0)
                self.assertEqual(adapter.snapshot()['completed'], 0)

    def test_shutdown_cancels_owned_incomplete_but_not_completed_or_unrelated(self):
        adapter, _, control, _ = setup()
        completed = request(control)
        control.pump()
        completed.DownloadOperation.transition('Completed')
        active = request(control)
        control.pump()
        unrelated = Operation()
        result = worker_call(control, lambda: adapter.shutdown(timeout_seconds=.2))
        self.assertTrue(result['shutdown_confirmed'])
        self.assertEqual(completed.DownloadOperation.cancel_count, 0)
        self.assertEqual(unrelated.cancel_count, 0)
        self.assertGreater(active.DownloadOperation.cancel_count, 0)
        self.assertTrue(request(control).Cancel)

    def test_shutdown_before_queued_dialog_avoids_showing_it(self):
        adapter, _, control, _ = setup()
        args = request(control)
        result = worker_call(control, lambda: adapter.shutdown(timeout_seconds=.2))
        self.assertTrue(result['shutdown_confirmed'])
        self.assertEqual(Dialog.instances, [])
        self.assertTrue(args.Cancel)
        self.assertEqual(args.complete_count, 1)

    def test_postclosed_wait_uses_cached_receipts_without_dead_control_access(self):
        adapter, _, control, _ = setup()
        args = request(control)
        adapter.begin_shutdown_on_ui()
        cancelled = args.DownloadOperation.cancel_count
        adapter.begin_shutdown_on_ui()
        control.Dispose()
        control.dispatch_error = True
        del control.InvokeRequired
        result = worker_call(control, lambda: adapter.finish_shutdown(.02), pump=False)
        self.assertTrue(result['shutdown_confirmed'])
        self.assertEqual(args.DownloadOperation.cancel_count, cancelled)
        self.assertEqual(args.complete_count, 1)
        self.assertEqual(Dialog.instances, [])

    def test_postclosed_wait_cannot_claim_uncancelled_or_unsettled_download(self):
        adapter, _, control, _ = setup()
        result = worker_call(control, lambda: adapter.finish_shutdown(.01), pump=False)
        self.assertFalse(result['shutdown_confirmed'])
        args = request(control)
        control.pump()
        args.DownloadOperation.cancel_stalls = True
        adapter.begin_shutdown_on_ui()
        result = worker_call(control, lambda: adapter.finish_shutdown(.01), pump=False)
        self.assertFalse(result['shutdown_confirmed'])
        self.assertEqual(result['active'], 1)

    def test_split_download_shutdown_enforces_thread_affinity(self):
        adapter, _, control, _ = setup()
        with self.assertRaises(DownloadError):
            adapter.finish_shutdown(.01)
        with self.assertRaises(DownloadError):
            worker_call(control, adapter.begin_shutdown_on_ui, pump=False)
        self.assertFalse(adapter._closing)

    def test_active_owned_dialog_cancellation_completes_once_and_confirms_return(self):
        adapter, _, control, _ = setup()
        outcome = {}
        class ActiveDialog(Dialog):
            def choose(self):
                def close():
                    outcome['value'] = adapter.shutdown(timeout_seconds=.2)
                outcome['thread'] = threading.Thread(target=close)
                outcome['thread'].start()
                deadline = time.monotonic()+1
                while not self.cancelled and time.monotonic() < deadline:
                    control.pump()
                    time.sleep(.001)
                return None
        with patch('nivram.webview_downloads._SaveDialog', ActiveDialog):
            args = request(control)
            control.pump()
        outcome['thread'].join(1)
        self.assertFalse(outcome['thread'].is_alive())
        self.assertTrue(outcome['value']['shutdown_confirmed'])
        self.assertEqual(args.complete_count, 1)
        self.assertTrue(Dialog.instances[-1].cancelled)

    def test_unmatched_active_dialog_returns_unconfirmed_without_guessing(self):
        adapter, _, control, _ = setup()
        outcome = {}
        class UnmatchedDialog(Dialog):
            def cancel(self):
                return False
            def choose(self):
                def close():
                    outcome['value'] = adapter.shutdown(timeout_seconds=.02)
                outcome['thread'] = threading.Thread(target=close)
                outcome['thread'].start()
                deadline = time.monotonic()+1
                while outcome['thread'].is_alive() and time.monotonic() < deadline:
                    control.pump()
                    time.sleep(.001)
                return None
        with patch('nivram.webview_downloads._SaveDialog', UnmatchedDialog):
            request(control)
            control.pump()
        outcome['thread'].join(1)
        self.assertFalse(outcome['value']['shutdown_confirmed'])
        self.assertFalse(Dialog.instances[-1].cancelled)

    def test_unusable_cancel_setter_disposes_only_the_affected_control(self):
        adapter, _, control, _ = setup()
        class Uncancellable:
            @property
            def Cancel(self):
                return False
            @Cancel.setter
            def Cancel(self, value):
                raise OSError('private path must not escape')
        args = Uncancellable()
        args.DownloadOperation = Operation('text/html')
        control.CoreWebView2.DownloadStarting.fire(None, args)
        self.assertTrue(control.disposed)
        self.assertEqual(adapter.snapshot()['failure_code'], 'download_rejection_failed')

    def test_cancel_by_identity_does_not_cancel_later_download(self):
        adapter, _, control, _ = setup()
        first = request(control)
        control.pump()
        first.DownloadOperation.transition('Completed')
        second = request(control)
        control.pump()
        self.assertTrue(worker_call(control, lambda: adapter.cancel(1, timeout_seconds=.1)))
        self.assertEqual(second.DownloadOperation.cancel_count, 0)
        self.assertTrue(worker_call(control, lambda: adapter.cancel(2, timeout_seconds=.1)))
        self.assertGreater(second.DownloadOperation.cancel_count, 0)

    def test_unresponsive_native_cancel_remains_bounded_and_unconfirmed(self):
        adapter, _, control, _ = setup()
        args = request(control)
        control.pump()
        args.DownloadOperation.cancel_stalls = True
        result = worker_call(control, lambda: adapter.shutdown(timeout_seconds=.02))
        self.assertFalse(result['shutdown_confirmed'])
        self.assertEqual(result['status'], 'shutdown_unconfirmed')

    def test_missing_ui_dispatch_and_deferral_failure_do_not_claim_success(self):
        adapter, _, control, _ = setup()
        args = request(control)
        args.completion_error = True
        control.pump()
        control.dispatch_error = True
        result = worker_call(control, lambda: adapter.shutdown(timeout_seconds=.02))
        self.assertFalse(result['shutdown_confirmed'])
        self.assertNotIn('personal', json.dumps(result))
        self.assertIsNotNone(adapter._records[0].deferral)

    def test_receipts_retain_no_paths_urls_names_or_native_handles(self):
        adapter, _, control, _ = setup()
        args = request(control, name=r'C:\Users\secret\private-compound.png')
        control.pump()
        args.DownloadOperation.transition('Completed')
        result = worker_call(control, lambda: adapter.wait_for_download(timeout_seconds=.1))
        serialized = json.dumps([adapter.snapshot(), result])
        for forbidden in ('secret','compound','127.0.0.1','C:', 'exports'):
            self.assertNotIn(forbidden, serialized)
        self.assertIsNone(adapter._records[0].operation)
        self.assertIsNone(adapter._records[0].args)

    def test_receipt_and_delegate_retention_is_bounded(self):
        adapter, _, control, _ = setup()
        for _ in range(40):
            args = request(control)
            control.pump()
            args.DownloadOperation.transition('Completed')
        self.assertEqual(len(adapter._records), 32)
        self.assertEqual(adapter.snapshot()['last_id'], 40)
        for _ in range(5):
            self.assertTrue(worker_call(control, lambda: adapter.cancel(40, timeout_seconds=.1)))
        self.assertEqual(adapter._actions, [])

    def test_ui_thread_waits_and_invalid_budgets_are_rejected(self):
        adapter, _, control, _ = setup()
        for value in (False, 0, -1, float('nan'), 61):
            with self.assertRaises(DownloadError):
                adapter.shutdown(value)
        control.InvokeRequired = False
        for call in (adapter.shutdown, adapter.wait_for_download, lambda: adapter.cancel(1)):
            with self.assertRaises(DownloadError):
                call()

    def test_suggestion_and_destination_have_distinct_trust_boundaries(self):
        self.assertEqual(_suggested_name(r'C:\private\CON.png', ('.png',)), 'structure.png')
        self.assertEqual(_suggested_name('../molecule.exe', ('.mol',)), 'molecule.mol')
        for value in ('relative.png', r'C:\export\file.txt', r'C:\export\file.png:stream',
                      r'C:\export\CON.png', r'C:\export\file.png.', 'C:\\export\\x\n.png'):
            self.assertFalse(_valid_destination(value, ('.png',)))
        self.assertTrue(_valid_destination(r'C:\export\molecule.png', ('.png',)))
        self.assertIsNone(_format('image/png; charset=unexpected'))


if __name__ == '__main__':
    unittest.main()
