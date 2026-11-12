"""Expose only the dialog-scoped application operations to pywebview.

pywebview 6.2.1's raw dispatcher resolves unknown dotted names with ``getattr``
against ``window._js_api``. Hiding underscore methods from generated JavaScript
stubs is therefore not a capability boundary. Keep that fallback object None,
explicitly expose the supported methods, and reject unknown application names
before the dispatcher can enter its attribute traversal. Vendor-internal event
and callback messages remain the vendor's responsibility; this adapter does not
claim to replace WebView2's navigation/network policy or the loopback API checks.

This adapter uses the pinned vendor's dispatch-table contract. Its regression
tests exercise the installed vendor dispatcher and expose implementation without
creating a native window. Revalidate that contract when upgrading pywebview.
"""
from __future__ import annotations

from collections.abc import Mapping


PUBLIC_METHODS = (
    'open_project', 'accept_open', 'save_project', 'autosave_project', 'recovery_context',
    'list_recovery', 'recovery_inventory', 'recover_project', 'accept_recovery',
    'diagnostics', 'save_diagnostics', 'save_reference_export', 'save_population_export',
    'inspect_image', 'inspect_image_bytes', 'cancel_image_import', 'preview_image_components',
    'preview_image_region', 'cancel_image_region', 'recognize_image', 'preview_image_draft',
    'begin_optical_setup', 'choose_optical_setup_file', 'save_optical_setup', 'cancel_optical_setup',
    'prepare_image_import', 'list_profiles', 'save_profile', 'list_fragments', 'save_fragment',
    'get_appearance', 'set_appearance',
    'begin_batch_export', 'prepare_batch_item', 'write_batch_png',
    'fail_batch_item', 'cancel_batch_export', 'finish_batch_export',
)


class NativeBridgeRejected(PermissionError):
    """An incoming application method is outside the native capability list."""


class _ExactFunctions(Mapping):
    """Read-only expose table; a miss must not fall through to vendor getattr."""
    def __init__(self, functions):
        self.__functions = dict(functions)

    def __getitem__(self, name):
        return self.__functions[name]

    def __iter__(self):
        return iter(self.__functions)

    def __len__(self):
        return len(self.__functions)

    def get(self, name, default=None):
        # pywebview's Edge message handler catches this exception and does not
        # dispatch or create a result callback for the rejected raw message.
        # Do not interpolate the caller-controlled name into an error message.
        if not isinstance(name, str) or name not in self.__functions:
            raise NativeBridgeRejected('This native operation is not exposed.')
        return self.__functions[name]


def expose_desktop_api(window, api):
    """Install once immediately after create_window(js_api=None), before start.

    Fail before native startup if the expected vendor contract or the public
    method list is unavailable. No DesktopApi object becomes a traversal root.
    """
    if window._js_api is not None:
        raise ValueError('Create the native window with js_api=None.')
    if window.events.loaded.is_set() or window.events._pywebviewready.is_set():
        raise RuntimeError('Install the native method boundary before page startup.')
    if window._functions:
        raise RuntimeError('The native method boundary must be installed once on an empty window.')

    functions = []
    for name in PUBLIC_METHODS:
        function = getattr(api, name, None)
        if not callable(function) or getattr(function, '__name__', None) != name:
            raise ValueError('The expected native application operations are unavailable.')
        functions.append(function)
    try:
        window.expose(*functions)
        if dict(window._functions) != dict(zip(PUBLIC_METHODS, functions)):
            raise RuntimeError('The native expose contract changed; startup was stopped.')
        window._functions = _ExactFunctions(window._functions)
    except Exception:
        # A partial expose must never leave an unguarded fallback table behind.
        window._functions = _ExactFunctions({})
        raise
    return {'status': 'installed', 'applicationMethods': len(PUBLIC_METHODS),
            'fallbackObjectExposed': False}
