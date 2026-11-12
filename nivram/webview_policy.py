"""Per-window WebView2 request and navigation policy for the offline editor.

Call attach_policy(window, base_url) before webview.start(). The returned receipt
has snapshot() for small, URL-free diagnostics. Native delegates are retained on
the window for its lifetime. No operating-system settings or vendor files change.

The event order was inspected in pywebview 6.2.1 and WebView2 SDK 1.0.3856.49.
Its original ready callback immediately navigates; replace that subscription and
queue one UI action so initialization can return before filtering and navigation.
Resource filters still exist before it runs. Its original popup handler redirects the
existing window even when Handled is already true, so remove that subscription.

Primary contracts (reviewed 2026-09-19):
https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/webresourcerequested
https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.addwebresourcerequestedfilter
https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.winforms.webview2.corewebview2initializationcompleted

This intercepts document/worker resource requests; it is not an OS-wide firewall
or proof that a browser runtime has no independent background connections.
Observed blocked attempts remain counted and are not relabeled as no attempts.
"""
from __future__ import annotations

import re
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit


class PolicyError(ValueError):
    """The requested policy cannot be installed with the supported contract."""


class UrlPolicy:
    """Allow only one canonical HTTP loopback origin and inert image resources."""
    _BASE = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/?\Z", re.ASCII)
    _BLOB_PATH = re.compile(r"/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z", re.ASCII)
    _IMAGE_TYPES = frozenset(("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
                              "image/x-icon", "image/vnd.microsoft.icon", "image/avif", "image/svg+xml"))
    _DATA_PARAMETERS = frozenset(("base64", "charset=utf-8", "charset=us-ascii"))

    def __init__(self, base_url):
        match = self._BASE.fullmatch(base_url) if isinstance(base_url, str) else None
        if match is None or not 1 <= int(match.group(1)) <= 65535:
            raise PolicyError("The policy requires a canonical http://127.0.0.1:PORT/ origin.")
        self.port = int(match.group(1))
        self.netloc = f"127.0.0.1:{self.port}"
        self.origin = f"http://{self.netloc}"

    @staticmethod
    def _clean(url):
        # Reject parser-normalization ambiguities before urlsplit can strip them.
        return (isinstance(url, str) and 0 < len(url) <= 8_388_608
                and "\\" not in url and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url))

    def _network_url(self, url):
        if not self._clean(url):
            return False
        try:
            parts = urlsplit(url)
            return (parts.scheme == "http" and parts.netloc == self.netloc
                    and parts.hostname == "127.0.0.1" and parts.port == self.port
                    and parts.username is None and parts.password is None)
        except (ValueError, UnicodeError):
            return False

    def allows_navigation(self, url):
        # No file, data, blob, javascript, about, websocket, or external schemes.
        return self._network_url(url)

    def allows_resource(self, url, resource_context=None):
        if self._network_url(url):
            return True
        if resource_context != "Image" or not self._clean(url):
            return False
        # SVG is accepted only in image context, never as a document or script.
        # SVG loaded as an image does not provide a document scripting context.
        if url.startswith("data:"):
            header, comma, _ = url[5:].partition(",")
            if not comma or len(header) > 256:
                return False
            tokens = header.lower().split(";")
            return tokens[0] in self._IMAGE_TYPES and all(p in self._DATA_PARAMETERS for p in tokens[1:])
        if url.startswith("blob:") and self._network_url(url[5:]):
            parts = urlsplit(url[5:])
            return bool(self._BLOB_PATH.fullmatch(parts.path)) and not parts.query and not parts.fragment
        return False


class PolicyReceipt:
    """Only counters and bounded fixed diagnostics; never request URLs or bodies."""
    def __init__(self, origin):
        self._lock = threading.Lock()
        self._values = {
            "status": "pending", "origin": origin,
            "installed_before_initial_navigation": False,
            "filter_source_kinds": None,
            "allowed_navigations": 0, "blocked_navigations": 0,
            "allowed_resources": 0, "allowed_inert_image_resources": 0,
            "blocked_resources": 0, "blocked_new_windows": 0,
            "blocked_external_schemes": 0, "failures": 0, "failure_code": None,
        }

    def snapshot(self):
        with self._lock:
            return dict(self._values)

    def _update(self, **values):
        with self._lock:
            self._values.update(values)

    def _increment(self, name):
        with self._lock:
            self._values[name] += 1


def _sdk_enums():
    # Import only after the native WebView2 runtime has loaded its assemblies.
    from Microsoft.Web.WebView2.Core import (
        CoreWebView2WebResourceContext, CoreWebView2WebResourceRequestSourceKinds,
    )
    return CoreWebView2WebResourceContext.All, CoreWebView2WebResourceRequestSourceKinds.All


def _ui_action(callback):
    from System import Action
    return Action(callback)


class _NativePolicy:
    def __init__(self, window, url_policy):
        self.window = window
        self.urls = url_policy
        self.receipt = PolicyReceipt(url_policy.origin)
        self.control = None
        self.core = None
        self._original_ready = None
        self._original_popup = None
        self._ready = False
        self._armed = False
        self._initialization_queued = False
        self._initialization_started = False
        self._initialization_action = None
        self._handlers = (self._before_show, self._on_initialized, self._on_navigation,
                          self._on_resource, self._on_new_window, self._on_external_scheme)

    def _fail(self, code):
        self._ready = False
        self.receipt._increment("failures")
        self.receipt._update(status="failed", failure_code=code)
        # Do not continue displaying an unprotected control after an interop error.
        for obj, method in ((self.core, "Stop"), (self.control, "Dispose"),
                            (getattr(self.window, "native", None), "Close")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass  # Failure is already explicit; never log request data.

    def _before_show(self):
        if self._armed or self.receipt.snapshot()["status"] == "failed":
            return
        try:
            native = self.window.native
            self.control = native.webview
            browser = native.browser
            if browser.webview is not self.control:
                raise PolicyError("The native browser/control identity is inconsistent.")
            if self.control.CoreWebView2 is not None:
                self._fail("policy_attached_after_core_initialization")
                return
            self._original_ready = browser.on_webview_ready
            self._original_popup = browser.on_new_window_request
            self.control.NavigationStarting += self._on_navigation
            # Keep pywebview's configuration/bridge behavior, but run it only after
            # the synchronous request guard is installed. A navigation-only guard
            # is insufficient: SDK docs allow speculative GETs during cancellation.
            self.control.CoreWebView2InitializationCompleted -= self._original_ready
            self.control.CoreWebView2InitializationCompleted += self._on_initialized
            self._armed = True
            self.receipt._update(status="armed")
        except Exception:
            self._fail("native_policy_setup_failed")

    def _on_initialized(self, sender, args):
        if self._initialization_queued or self.receipt.snapshot()["status"] in ("failed", "installed"):
            return
        try:
            if not args.IsSuccess:
                self._fail("webview_initialization_failed")
                return
            # Native acceptance found that reconfiguring the core and navigating
            # inside this event can stall startup. Return to the UI message pump
            # first. Keep the delegate alive, and never run an unguarded fallback.
            self._initialization_queued = True
            self._initialization_action = _ui_action(self._finish_initialization)
            self.receipt._update(status="queued")
            self.control.BeginInvoke(self._initialization_action)
        except Exception:
            self._fail("native_initialization_dispatch_failed")

    def _finish_initialization(self):
        if self._initialization_started or self.receipt.snapshot()["status"] in ("failed", "installed"):
            return
        self._initialization_started = True
        try:
            sender = self.control
            self.core = sender.CoreWebView2
            contexts, sources = _sdk_enums()
            self.core.WebResourceRequested += self._on_resource
            self.core.AddWebResourceRequestedFilter("*", contexts, sources)
            self.core.NewWindowRequested += self._on_new_window
            self.core.FrameNavigationStarting += self._on_navigation
            self.core.LaunchingExternalUriScheme += self._on_external_scheme
            self._ready = True
            self.receipt._update(status="installing", filter_source_kinds="All",
                                 installed_before_initial_navigation=True)
            # The pinned pywebview callback consumes IsSuccess only on success.
            # Do not retain native initialization event arguments after the event.
            self._original_ready(sender, SimpleNamespace(IsSuccess=True))
            # pywebview 6.2.1 otherwise navigates this same window for window.open,
            # ignoring an earlier handler's Handled flag. Remove just that handler.
            self.core.NewWindowRequested -= self._original_popup
            self.receipt._update(status="installed")
        except Exception:
            self._fail("native_resource_policy_install_failed")

    def _on_navigation(self, sender, args):
        try:
            allowed = self._ready and self.urls.allows_navigation(str(args.Uri))
            if not allowed:
                args.Cancel = True
            self.receipt._increment("allowed_navigations" if allowed else "blocked_navigations")
        except Exception:
            self._fail("native_navigation_guard_failed")

    def _on_resource(self, sender, args):
        try:
            uri = str(args.Request.Uri)
            allowed = self._ready and self.urls.allows_resource(uri, str(args.ResourceContext))
        except Exception:
            allowed = False
        if allowed:
            self.receipt._increment("allowed_resources")
            if uri.startswith(("blob:", "data:")):
                self.receipt._increment("allowed_inert_image_resources")
            return
        self.receipt._increment("blocked_resources")
        try:
            # Supplying a response in this synchronous event prevents dispatch to
            # the original server. Do not take a deferral or wait for remote work.
            args.Response = self.core.Environment.CreateWebResourceResponse(
                None, 403, "Blocked by nivraM local-only policy",
                "Content-Type: text/plain\r\nContent-Length: 0\r\nCache-Control: no-store\r\n",
            )
        except Exception:
            # A response failure must never fall through to an external target.
            # Rewrite to an inert local path before stopping/disposing the view.
            try:
                args.Request.Uri = self.urls.origin + "/__nivram_policy_blocked__"
            except Exception:
                pass
            self._fail("native_block_response_failed")

    def _on_new_window(self, sender, args):
        try:
            args.Handled = True
            self.receipt._increment("blocked_new_windows")
        except Exception:
            self._fail("native_popup_guard_failed")

    def _on_external_scheme(self, sender, args):
        try:
            args.Cancel = True
            self.receipt._increment("blocked_external_schemes")
        except Exception:
            self._fail("native_external_scheme_guard_failed")


def attach_policy(window, base_url):
    """Arm one offline policy before webview.start(); return its diagnostics receipt.

    Only the exact server origin is network-allowed. Data/blob images are resource
    exceptions, never navigation or script exceptions. All new windows are denied.
    A failed receipt is a startup failure, not a request to continue unprotected.
    """
    urls = UrlPolicy(base_url)
    existing = getattr(window, "_nivram_network_policy", None)
    if existing is not None:
        if not isinstance(existing, _NativePolicy) or existing.urls.origin != urls.origin:
            raise PolicyError("This window already has a different network policy.")
        return existing.receipt
    policy = _NativePolicy(window, urls)
    try:
        window.events.before_show += policy._before_show
        window._nivram_network_policy = policy
    except Exception as exc:
        raise PolicyError("This window does not expose the required before_show event.") from exc
    return policy.receipt
