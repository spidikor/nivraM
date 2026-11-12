"""URL and event-order contract tests; native acceptance is a separate gate."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram.webview_policy import PolicyError, UrlPolicy, attach_policy


BASE = "http://127.0.0.1:54321/"
BLOB = "blob:http://127.0.0.1:54321/11111111-2222-3333-4444-555555555555"


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self, *args):
        for handler in list(self.handlers):
            handler(*args)


class FakeEnvironment:
    fail_response = False

    def CreateWebResourceResponse(self, body, status, reason, headers):
        if self.fail_response:
            raise RuntimeError("simulated native failure with sensitive URL")
        return SimpleNamespace(body=body, status=status, reason=reason, headers=headers)


class FakeCore:
    def __init__(self):
        self.WebResourceRequested = Event()
        self.NewWindowRequested = Event()
        self.FrameNavigationStarting = Event()
        self.LaunchingExternalUriScheme = Event()
        self.Environment = FakeEnvironment()
        self.filters = []
        self.stopped = False

    def AddWebResourceRequestedFilter(self, *args):
        self.filters.append(args)

    def Stop(self):
        self.stopped = True

    def request(self, url, context="Script"):
        args = SimpleNamespace(Request=SimpleNamespace(Uri=url), ResourceContext=context, Response=None)
        self.WebResourceRequested.fire(self, args)
        return args


class FakeControl:
    def __init__(self):
        self.CoreWebView2 = None
        self.CoreWebView2InitializationCompleted = Event()
        self.NavigationStarting = Event()
        self.disposed = False
        self.pending_actions = []

    def BeginInvoke(self, action):
        self.pending_actions.append(action)

    def pump(self):
        while self.pending_actions:
            self.pending_actions.pop(0)()

    def Dispose(self):
        self.disposed = True


class FakeBrowser:
    def __init__(self, control):
        self.webview = control
        self.original_new_windows = 0
        self.loaded = False
        self.protected_before_load = False
        self.ready_calls = 0

    def on_webview_ready(self, sender, args):
        self.ready_calls += 1
        core = sender.CoreWebView2
        core.NewWindowRequested += self.on_new_window_request
        # Mirrors the consequential order in pywebview 6.2.1: its ready callback
        # adds its own popup handler and immediately starts the initial load.
        self.protected_before_load = bool(core.filters and core.WebResourceRequested.handlers)
        nav = SimpleNamespace(Uri=BASE, Cancel=False)
        sender.NavigationStarting.fire(sender, nav)
        if not nav.Cancel:
            core.request(BASE, "Document")
            self.loaded = True

    def on_new_window_request(self, sender, args):
        self.original_new_windows += 1


def fixture():
    control = FakeControl()
    browser = FakeBrowser(control)
    native = SimpleNamespace(webview=control, browser=browser, closed=False)
    native.Close = lambda: setattr(native, "closed", True)
    window = SimpleNamespace(native=native, events=SimpleNamespace(before_show=Event()))
    control.CoreWebView2InitializationCompleted += browser.on_webview_ready
    return window, control, browser


def install_fixture():
    window, control, browser = fixture()
    receipt = attach_policy(window, BASE)
    window.events.before_show.fire()
    core = FakeCore()
    control.CoreWebView2 = core
    with patch("nivram.webview_policy._sdk_enums", return_value=("AllContexts", "AllSources")), \
         patch("nivram.webview_policy._ui_action", side_effect=lambda callback: callback):
        control.CoreWebView2InitializationCompleted.fire(control, SimpleNamespace(IsSuccess=True))
        control.pump()
    return window, control, browser, core, receipt


class UrlPolicyTests(unittest.TestCase):
    def test_only_exact_loopback_origin_is_network_allowed(self):
        policy = UrlPolicy(BASE)
        for url in (BASE, BASE + "api/status", BASE + "file?x=1#section"):
            self.assertTrue(policy.allows_navigation(url))
            self.assertTrue(policy.allows_resource(url, "Fetch"))
        for url in (
            "https://127.0.0.1:54321/", "http://127.0.0.1:54322/", "http://127.0.0.1/",
            "http://localhost:54321/", "http://127.0.0.1.example.com:54321/", "http://127.1:54321/",
            "http://2130706433:54321/", "http://[::1]:54321/", "http://user@127.0.0.1:54321/",
            "http://127.0.0.1:54321@evil.example/", "http://127.0.0.1:54321\\@evil.example/",
            "http://127.0.0.1:054321/", "http://127.0.0.1:54321.evil.example/", "//127.0.0.1:54321/",
            "http://127.0.0.1%2eexample.com:54321/", "\n" + BASE, BASE + "\tpath",
            "file:///C:/private.txt", "javascript:alert(1)", "ws://127.0.0.1:54321/", "about:blank",
            "https://example.com/", "ms-settings:privacy", "", None,
        ):
            with self.subTest(url=url):
                self.assertFalse(policy.allows_navigation(url))
                self.assertFalse(policy.allows_resource(url, "Script"))

    def test_blob_and_data_are_inert_image_resources_only(self):
        policy = UrlPolicy(BASE)
        images = (BLOB, "data:image/png;base64,iVBORw0KGgo=", "data:image/svg+xml,%3Csvg/%3E")
        for url in images:
            self.assertTrue(policy.allows_resource(url, "Image"))
            self.assertFalse(policy.allows_navigation(url))
            for context in ("Script", "Document", "Fetch", "Other", None):
                self.assertFalse(policy.allows_resource(url, context))
        for url in (BLOB.replace("54321", "54322"), "blob:null/anything", "data:text/html,<script/>",
                    "data:application/javascript,alert(1)", "data:image/png;unexpected=1,abc"):
            self.assertFalse(policy.allows_resource(url, "Image"))

    def test_base_is_a_canonical_local_origin_not_arbitrary_url(self):
        for base in ("https://example.com/", "http://127.0.0.1:0/", "http://127.0.0.1:65536/",
                     "http://127.0.0.1:54321/path", "http://u:p@127.0.0.1:54321/", "http://127.0.0.1:54321/?x=1"):
            with self.subTest(base=base), self.assertRaises(PolicyError):
                UrlPolicy(base)


class NativeEventPolicyTests(unittest.TestCase):
    def test_initialization_returns_before_navigation_and_queues_exactly_once(self):
        window, control, browser = fixture()
        receipt = attach_policy(window, BASE)
        window.events.before_show.fire()
        control.CoreWebView2 = FakeCore()
        with patch("nivram.webview_policy._sdk_enums", return_value=("AllContexts", "AllSources")), \
             patch("nivram.webview_policy._ui_action", side_effect=lambda callback: callback):
            for _ in range(2):
                control.CoreWebView2InitializationCompleted.fire(control, SimpleNamespace(IsSuccess=True))
            self.assertEqual(receipt.snapshot()["status"], "queued")
            self.assertFalse(browser.loaded)
            self.assertEqual(control.CoreWebView2.filters, [])
            self.assertEqual(len(control.pending_actions), 1)
            self.assertIs(window._nivram_network_policy._initialization_action, control.pending_actions[0])
            attempted = SimpleNamespace(Uri=BASE, Cancel=False)
            control.NavigationStarting.fire(control, attempted)
            self.assertTrue(attempted.Cancel)
            control.pump()
            window._nivram_network_policy._initialization_action()
            control.CoreWebView2InitializationCompleted.fire(control, SimpleNamespace(IsSuccess=True))
        self.assertEqual(browser.ready_calls, 1)
        self.assertTrue(browser.protected_before_load)
        self.assertEqual(receipt.snapshot()["status"], "installed")

    def test_dispatch_failure_closes_control_without_library_navigation(self):
        window, control, browser = fixture()
        receipt = attach_policy(window, BASE)
        window.events.before_show.fire()
        control.CoreWebView2 = FakeCore()
        with patch("nivram.webview_policy._ui_action", side_effect=lambda callback: callback), \
             patch.object(control, "BeginInvoke", side_effect=RuntimeError("dispatch failed")):
            control.CoreWebView2InitializationCompleted.fire(control, SimpleNamespace(IsSuccess=True))
        self.assertFalse(browser.loaded)
        self.assertTrue(control.disposed)
        self.assertTrue(window.native.closed)
        self.assertEqual(receipt.snapshot()["failure_code"], "native_initialization_dispatch_failed")

    def test_queued_initialization_does_not_revive_a_failed_policy(self):
        window, control, browser = fixture()
        receipt = attach_policy(window, BASE)
        window.events.before_show.fire()
        control.CoreWebView2 = FakeCore()
        with patch("nivram.webview_policy._ui_action", side_effect=lambda callback: callback):
            control.CoreWebView2InitializationCompleted.fire(control, SimpleNamespace(IsSuccess=True))
        window._nivram_network_policy._fail("simulated_failure")
        control.pump()
        self.assertFalse(browser.loaded)
        self.assertEqual(receipt.snapshot()["status"], "failed")

    def test_deferred_filter_failure_prevents_library_navigation(self):
        window, control, browser = fixture()
        receipt = attach_policy(window, BASE)
        window.events.before_show.fire()
        control.CoreWebView2 = FakeCore()
        with patch("nivram.webview_policy._ui_action", side_effect=lambda callback: callback):
            control.CoreWebView2InitializationCompleted.fire(control, SimpleNamespace(IsSuccess=True))
        with patch("nivram.webview_policy._sdk_enums", side_effect=RuntimeError("SDK failed")):
            control.pump()
        self.assertFalse(browser.loaded)
        self.assertTrue(control.disposed)
        self.assertEqual(receipt.snapshot()["failure_code"], "native_resource_policy_install_failed")

    def test_filter_is_installed_before_library_initial_navigation(self):
        _, _, browser, core, receipt = install_fixture()
        self.assertTrue(browser.protected_before_load)
        self.assertTrue(browser.loaded)
        self.assertEqual(core.filters[0], ("*", "AllContexts", "AllSources"))
        snapshot = receipt.snapshot()
        self.assertEqual(snapshot["status"], "installed")
        self.assertTrue(snapshot["installed_before_initial_navigation"])
        self.assertEqual(snapshot["allowed_resources"], 1)

    def test_external_resources_get_native_denial_and_attempts_remain_counted(self):
        _, _, _, core, receipt = install_fixture()
        url = "https://injection.example/script.js?private=do-not-log"
        args = core.request(url)
        self.assertEqual(args.Response.status, 403)
        self.assertEqual(args.Request.Uri, url)
        self.assertEqual(receipt.snapshot()["blocked_resources"], 1)
        self.assertNotIn("private", str(receipt.snapshot()))
        self.assertNotIn("injection.example", str(receipt.snapshot()))
        self.assertIsNone(core.request(BASE + "api/status", "Fetch").Response)
        self.assertIsNone(core.request(BLOB, "Image").Response)

    def test_navigation_and_frames_are_restricted(self):
        _, control, _, core, receipt = install_fixture()
        for uri in ("https://example.com/", BLOB, "file:///C:/private.txt"):
            args = SimpleNamespace(Uri=uri, Cancel=False)
            control.NavigationStarting.fire(control, args)
            self.assertTrue(args.Cancel)
        frame = SimpleNamespace(Uri="https://example.com/", Cancel=False)
        core.FrameNavigationStarting.fire(core, frame)
        self.assertTrue(frame.Cancel)
        self.assertEqual(receipt.snapshot()["blocked_navigations"], 4)

    def test_popup_replaces_pywebview_existing_window_redirect_behavior(self):
        _, _, browser, core, receipt = install_fixture()
        for uri in (BASE, "https://example.com/", BLOB):
            args = SimpleNamespace(Uri=uri, Handled=False)
            core.NewWindowRequested.fire(core, args)
            self.assertTrue(args.Handled)
        self.assertEqual(browser.original_new_windows, 0)
        self.assertEqual(receipt.snapshot()["blocked_new_windows"], 3)

    def test_external_protocol_never_launches_another_application(self):
        _, _, _, core, receipt = install_fixture()
        args = SimpleNamespace(Uri="mailto:someone@example.com", Cancel=False)
        core.LaunchingExternalUriScheme.fire(core, args)
        self.assertTrue(args.Cancel)
        self.assertEqual(receipt.snapshot()["blocked_external_schemes"], 1)

    def test_initialization_failure_prevents_library_navigation(self):
        window, control, browser = fixture()
        receipt = attach_policy(window, BASE)
        window.events.before_show.fire()
        control.CoreWebView2InitializationCompleted.fire(control, SimpleNamespace(IsSuccess=False))
        self.assertFalse(browser.loaded)
        self.assertTrue(control.disposed)
        self.assertEqual(receipt.snapshot()["status"], "failed")

    def test_response_failure_redirects_to_inert_local_path_and_stops_control(self):
        _, control, _, core, receipt = install_fixture()
        core.Environment.fail_response = True
        args = core.request("https://example.com/sensitive?token=secret")
        self.assertEqual(args.Request.Uri, BASE + "__nivram_policy_blocked__")
        self.assertTrue(core.stopped)
        self.assertTrue(control.disposed)
        self.assertEqual(receipt.snapshot()["status"], "failed")
        self.assertNotIn("secret", str(receipt.snapshot()))

    def test_late_attachment_is_not_misreported_as_startup_protection(self):
        window, control, _ = fixture()
        control.CoreWebView2 = FakeCore()
        receipt = attach_policy(window, BASE)
        window.events.before_show.fire()
        self.assertEqual(receipt.snapshot()["status"], "failed")
        self.assertFalse(receipt.snapshot()["installed_before_initial_navigation"])
        self.assertTrue(control.disposed)

    def test_duplicate_attachment_keeps_one_policy_and_rejects_origin_change(self):
        window, _, _ = fixture()
        receipt = attach_policy(window, BASE)
        self.assertIs(attach_policy(window, BASE), receipt)
        self.assertEqual(len(window.events.before_show.handlers), 1)
        with self.assertRaises(PolicyError):
            attach_policy(window, "http://127.0.0.1:12345/")


if __name__ == "__main__":
    unittest.main()
