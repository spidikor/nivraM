"""Windows desktop shell and narrowly scoped native project operations."""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.request import urlopen

from . import __version__
from .persistence import ProjectStore, SaveConflict, SaveFailure


class InstanceLock:
    """A process-lifetime lock released by Windows even after a crash."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.handle = None

    def acquire(self):
        import msvcrt
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = (self.directory / 'instance.lock').open('a+b')
        try:
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def close(self):
        if self.handle is not None:
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
            self.handle = None


class DesktopApi:
    """Only dialog-selected paths are usable; no generic filesystem/command API."""
    def __init__(self, directory, *, preparation_service=None):
        self._store = ProjectStore(Path(directory) / 'recovery')
        self._window = None
        self._path = None
        self._pending_open = None
        self._pending_recovery = None
        self._lock = threading.RLock()
        from .image_session import ImageImportSession
        self._images = ImageImportSession(Path(directory) / 'import-sources',
                                          preparation_service=preparation_service)
        from .optical_setup import OpticalSetupStore
        self._optical_setup = OpticalSetupStore(directory, self._images)
        from .profiles import ProfileStore
        self._profiles = ProfileStore(Path(directory) / 'profiles')
        from .fragments import FragmentStore
        self._fragments = FragmentStore(Path(directory) / 'fragments')
        from .preferences import AppearanceStore
        self._appearance = AppearanceStore(directory)
        self._cleanup = None
        self._downloads = None
        self._server = None
        self._diagnostic_pending = None
        from .batch_export import BatchExportManager
        self._batch = BatchExportManager(preparation_service=preparation_service)
        from .reference_export import ReferenceExportSaver
        self._reference_exports = ReferenceExportSaver()
        from .population_export import PopulationExportSaver
        self._population_exports = PopulationExportSaver()

    def _set_window(self, window):
        self._window = window
        self._recovery_page_ready = False
        # The pinned vendor invokes before_load synchronously before exposing
        # the page bridge. Only this host event may advance the generation.
        window.events.before_load += self._begin_recovery_page

    def _begin_recovery_page(self):
        self._recovery_page_ready = False
        self._pending_recovery = None
        self._store.begin_recovery_generation()
        self._appearance.begin_page()
        self._optical_setup.begin_page()
        self._recovery_page_ready = True

    def get_appearance(self):
        return self._appearance.read()

    def set_appearance(self, theme, generation, revision):
        return self._appearance.save(theme, generation, revision)

    def recovery_context(self):
        if not getattr(self, '_recovery_page_ready', False):
            raise ValueError('Recovery is not ready for this editor page.')
        return self._store.recovery_context()

    def open_project(self):
        import webview
        with self._lock:
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN,
                allow_multiple=False, file_types=('nivraM projects (*.nivram)',))
            if not paths:
                return {'status': 'cancelled'}
            result = self._store.open(paths[0])
            key = uuid.uuid4().hex
            self._pending_open = (key, Path(paths[0]))
            result['open_id'] = key
            return result

    def accept_open(self, key):
        with self._lock:
            if self._pending_open is None or self._pending_open[0] != key:
                raise ValueError('This open operation is no longer current.')
            self._path = self._pending_open[1]
            self._pending_open = None
            self._pending_recovery = None
            return {'status': 'accepted'}

    def save_project(self, document, expected_disk_hash=None, save_as=False):
        import webview
        with self._lock:
            path = self._path
            if path is None or save_as:
                paths = self._window.create_file_dialog(webview.FileDialog.SAVE,
                    save_filename='structure.nivram', file_types=('nivraM projects (*.nivram)',))
                if not paths:
                    return {'status': 'cancelled'}
                path = Path(paths if isinstance(paths, str) else paths[0])
                if path.suffix.lower() != '.nivram':
                    path = path.with_name(path.name + '.nivram')
                if path.exists():
                    # Bind the reviewed destination before the confirmation.
                    # A replacement while that dialog is open must remain a
                    # conflict, not become the new expected version to overwrite.
                    try:
                        expected_disk_hash = self._store.open(path)['disk_hash']
                    except FileNotFoundError:
                        return {'status': 'conflict', 'message': 'The destination disappeared. Choose Save As again.'}
                    except (OSError, ValueError):
                        error = SaveFailure('failed', 'not_committed')
                        return {'status': error.status, 'outcome': error.outcome, 'message': str(error)}
                    # Confirm the actual final filename, including an appended extension.
                    if not self._window.create_confirmation_dialog('Replace project?', f'Replace {path.name}?'):
                        return {'status': 'cancelled'}
                else:
                    expected_disk_hash = None
            try:
                result = self._store.save(path, document, expected_disk_hash)
            except SaveConflict as exc:
                return {'status': 'conflict', 'message': str(exc)}
            except SaveFailure as exc:
                return {'status': exc.status, 'outcome': exc.outcome, 'message': str(exc)}
            if result.get('status') == 'saved':
                self._path = path
                self._pending_recovery = None
            return result

    def autosave_project(self, document, generation, revision):
        if not getattr(self, '_recovery_page_ready', False):
            raise ValueError('Recovery is not ready for this editor page.')
        return self._store.autosave(document, generation, revision)

    def list_recovery(self):
        return self._store.list_recovery()

    def recovery_inventory(self):
        return self._store.recovery_inventory()

    def recover_project(self, key):
        with self._lock:
            generation = self.recovery_context()['generation']
            result = self._store.recover(key)
            token = uuid.uuid4().hex
            self._pending_recovery = (token, generation)
            # The existing destination survives a canceled, rejected or stale
            # frontend load. Only an acknowledged recovered drawing detaches it.
            return {'status': 'recovered', 'recovery_id': token, 'document': result['document']}

    def accept_recovery(self, token):
        with self._lock:
            if (self._pending_recovery is None or self._pending_recovery[0] != token
                    or self._pending_recovery[1] != self.recovery_context()['generation']):
                raise ValueError('Recovery acknowledgement is no longer current.')
            self._path = None
            self._pending_open = None
            self._pending_recovery = None
            return {'status': 'accepted'}

    def diagnostics(self, reference_exports=None):
        from .diagnostics import diagnostics_report
        data = diagnostics_report(cleanup=self._cleanup.snapshot() if self._cleanup else None,
            naming=dict(self._server.naming_runtime_status) if self._server else None,
            downloads=self._downloads.snapshot() if self._downloads else None,
            reference_exports=reference_exports)
        key = uuid.uuid4().hex
        with self._lock:
            self._diagnostic_pending = (key, json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2).encode('utf-8'))
        return {'reportId': key, 'data': data}

    def save_diagnostics(self, report_id):
        import webview
        from .persistence import atomic_write
        with self._lock:
            if self._diagnostic_pending is None or self._diagnostic_pending[0] != report_id:
                raise ValueError('Reopen diagnostics to review a current report before saving.')
            data = self._diagnostic_pending[1]
            paths = self._window.create_file_dialog(webview.FileDialog.SAVE,
                save_filename='nivram-diagnostics.json', file_types=('JSON reports (*.json)',))
            if not paths:
                return {'status': 'cancelled'}
            destination = Path(paths if isinstance(paths, str) else paths[0])
            if destination.suffix.lower() != '.json':
                destination = destination.with_name(destination.name + '.json')
            if destination.exists() and not self._window.create_confirmation_dialog('Replace report?', f'Replace {destination.name}?'):
                return {'status': 'cancelled'}
            atomic_write(destination, data)
            if destination.read_bytes() != data:
                raise OSError('Report readback did not match the reviewed bytes.')
        return {'status': 'saved'}

    def save_reference_export(self, kind, payload, session_token, transparent_background=False):
        return self._reference_exports.save(self._window, kind, payload, session_token,
            getattr(self._server, 'api_token', None), transparent_background)

    def save_population_export(self, kind, payload, session_token, transparent_background=False):
        return self._population_exports.save(self._window, kind, payload, session_token,
            getattr(self._server, 'api_token', None), transparent_background)

    def inspect_image(self, job_id):
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN,
            allow_multiple=False, file_types=('PNG images (*.png)',))
        if not paths:
            return {'status': 'cancelled'}
        return self._images.inspect(paths[0], job_id)

    def _authorize_optical_setup(self, session_token):
        expected = getattr(self._server, 'api_token', None)
        if (not isinstance(session_token, str) or not isinstance(expected, str)
                or not 1 <= len(session_token) <= 128 or not 1 <= len(expected) <= 128
                or not session_token.isascii() or not expected.isascii()
                or not hmac.compare_digest(session_token, expected)):
            raise PermissionError('Recognition setup requires the current local editor session.')

    def begin_optical_setup(self, session_token):
        self._authorize_optical_setup(session_token)
        return self._optical_setup.begin()

    def choose_optical_setup_file(self, setup_id, kind, session_token):
        self._authorize_optical_setup(session_token)
        self._optical_setup.ensure_choice(setup_id, kind)
        import webview
        filters = ('Python runtime (*.exe)',) if kind == 'python' else ('MolScribe model (*.pth)',)
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN,
            allow_multiple=False, file_types=filters)
        selected = (paths if isinstance(paths, str) else paths[0]) if paths else None
        # The host store rechecks identity after the potentially long-lived dialog.
        return self._optical_setup.choose(setup_id, kind, selected)

    def save_optical_setup(self, setup_id, session_token):
        self._authorize_optical_setup(session_token)
        return self._optical_setup.save(setup_id)

    def cancel_optical_setup(self, setup_id, session_token):
        self._authorize_optical_setup(session_token)
        return self._optical_setup.cancel(setup_id)

    def inspect_image_bytes(self, png_base64, filename, job_id, session_token):
        expected = getattr(self._server, 'api_token', None)
        if (not isinstance(session_token, str) or not isinstance(expected, str)
                or not 1 <= len(session_token) <= 128 or not 1 <= len(expected) <= 128
                or not session_token.isascii() or not expected.isascii()
                or not hmac.compare_digest(session_token, expected)):
            raise PermissionError('PNG intake requires the current local editor session.')
        return self._images.inspect_bytes(png_base64, filename, job_id)

    def cancel_image_import(self, job_id):
        return self._images.cancel(job_id)

    def recognize_image(self, job_id, request_id, rect, session_token):
        expected = getattr(self._server, 'api_token', None)
        if (not isinstance(session_token, str) or not isinstance(expected, str)
                or not 1 <= len(session_token) <= 128 or not 1 <= len(expected) <= 128
                or not session_token.isascii() or not expected.isascii()
                or not hmac.compare_digest(session_token, expected)):
            raise PermissionError('The local image session is not authorized.')
        return self._images.recognize(job_id, request_id, rect)

    def preview_image_components(self, job_id, selected_ids, style):
        return self._images.candidate(job_id, selected_ids, style)

    def preview_image_draft(self, job_id, request_id, rect, source, style, session_token):
        expected = getattr(self._server, 'api_token', None)
        if (not isinstance(session_token, str) or not isinstance(expected, str)
                or not 1 <= len(session_token) <= 128 or not 1 <= len(expected) <= 128
                or not session_token.isascii() or not expected.isascii()
                or not hmac.compare_digest(session_token, expected)):
            raise PermissionError('The local image session is not authorized.')
        return self._images.prepare_manual(job_id, request_id, rect, source, style)

    def preview_image_region(self, job_id, request_id, rect, mode, selected_ids):
        return self._images.preview_region(job_id, request_id, rect, mode, selected_ids)

    def cancel_image_region(self, job_id, request_id):
        return self._images.cancel_region(job_id, request_id)

    def prepare_image_import(self, job_id, selected_ids, style, review_token=None):
        return self._images.prepare_accept(job_id, selected_ids, style, review_token)

    def list_fragments(self):
        return self._fragments.list()

    def save_fragment(self, name, graph):
        return self._fragments.save(name, graph)

    def list_profiles(self):
        return self._profiles.list()

    def save_profile(self, name, style, export, profile_id=None, expected_revision=None):
        return self._profiles.save(name, style, export, profile_id, expected_revision)

    def begin_batch_export(self, settings):
        return self._batch.begin(self._window, settings)

    def prepare_batch_item(self, session_id, item_id):
        return self._batch.prepare(session_id, item_id)

    def write_batch_png(self, session_id, item_id, png_base64):
        return self._batch.write_png(session_id, item_id, png_base64)

    def fail_batch_item(self, session_id, item_id, error_code):
        return self._batch.fail(session_id, item_id, error_code)

    def cancel_batch_export(self, session_id):
        return self._batch.cancel(session_id)

    def finish_batch_export(self, session_id):
        return self._batch.finish(session_id)


def _finalization_group(api, server, server_thread, downloads, session):
    """Independent resource owners receive shutdown once, even if another stalls."""
    from .native_lifetime import FinalizationGroup
    stages = []
    if api is not None:
        stages.extend((('optical_setup', api._optical_setup.close),
                       ('images', api._images.close), ('batch', api._batch.close),
                       ('reference_exports', api._reference_exports.close),
                       ('population_exports', api._population_exports.close)))
    if downloads is not None:
        stages.append(('downloads', lambda: downloads.finish_shutdown(timeout_seconds=5)))
    if session is not None:
        stages.append(('webview', lambda: session.finish_shutdown(timeout_seconds=20)))
    if server is not None:
        def close_server():
            try:
                if server_thread is not None and server_thread.is_alive():
                    server.shutdown()
            finally:
                try:
                    # LocalServer owns scientific-job/process shutdown here.
                    server.server_close()
                finally:
                    if server_thread is not None:
                        server_thread.join(timeout=3)
            return server.scientific_jobs_status()
        stages.append(('server', close_server))
    return FinalizationGroup(stages)


def _attach_lifecycle(window, api, server, server_thread, downloads, session, *, review=True):
    """Wire the production close owners identically for desktop and acceptance."""
    from .lifecycle import CloseCoordinator
    from .native_lifetime import NativeLifetime
    coordinator = None
    if review:
        coordinator = CloseCoordinator(window, api._store,
            save_appearance=api._appearance.save_for_close)
        window.events.closing += coordinator.request_close
    finalization = _finalization_group(api, server, server_thread, downloads, session)
    lifetime = NativeLifetime(window,
        on_closed=coordinator.observe_closed if coordinator else lambda: None,
        cancel_on_ui=downloads.begin_shutdown_on_ui,
        dispose_on_ui=session.dispose_on_ui,
        finalize=lambda: finalization.run(timeout_seconds=30))
    return coordinator, finalization, lifetime


def _smoke_failures(report):
    """Judge the completed smoke run, never startup readiness alone."""
    failures = []
    if report.get('startup_status') != 'passed' or report.get('error'):
        failures.append('startup_failed')
    policy = report.get('network_policy', {})
    if (policy.get('status') != 'installed'
            or policy.get('installed_before_initial_navigation') is not True
            or policy.get('failures') != 0 or policy.get('failure_code') is not None):
        failures.append('network_policy_unconfirmed')
    cleanup = report.get('webview_cleanup', {})
    if (cleanup.get('status') != 'removed'
            or any(cleanup.get(field) is not True for field in (
                'native_profile_matched', 'collection_exit_observed',
                'collection_identity_matched', 'control_disposed', 'runtime_cache_removed'))):
        failures.append('webview_cleanup_unconfirmed')
    finalization = report.get('finalization', {})
    stages = finalization.get('stages', {})
    required = {'optical_setup', 'images', 'batch', 'reference_exports', 'population_exports', 'downloads', 'webview', 'server'}
    if (finalization.get('started') is not True or finalization.get('completed') is not True
            or finalization.get('workers_alive') != 0 or not required.issubset(stages)
            or any(value.get('status') != 'returned' for value in stages.values())):
        failures.append('finalization_unconfirmed')
    # A callback returning normally is not proof that its owned work stopped.
    if stages.get('images', {}).get('result') is not True:
        failures.append('image_shutdown_unconfirmed')
    if stages.get('optical_setup', {}).get('result') is not True:
        failures.append('optical_setup_shutdown_unconfirmed')
    references = stages.get('reference_exports', {}).get('result')
    if (not isinstance(references, dict) or references.get('closed') is not True
            or references.get('active') != 0):
        failures.append('reference_shutdown_unconfirmed')
    populations = stages.get('population_exports', {}).get('result')
    if (not isinstance(populations, dict) or populations.get('closed') is not True
            or populations.get('active') != 0):
        failures.append('population_export_shutdown_unconfirmed')
    server = stages.get('server', {}).get('result')
    shutdown = server.get('shutdown', {}) if isinstance(server, dict) else {}
    if shutdown.get('status') != 'confirmed' or shutdown.get('ownedProcessesExited') is not True:
        failures.append('scientific_shutdown_unconfirmed')
    downloads = report.get('download_shutdown', {})
    if (downloads.get('status') != 'closed' or downloads.get('shutdown_confirmed') is not True
            or downloads.get('active') != 0 or downloads.get('failures') != 0):
        failures.append('download_shutdown_unconfirmed')
    lifetime = report.get('native_lifetime', {})
    if (lifetime.get('errors') != [] or lifetime.get('finalizer_alive') is not False
            or lifetime.get('finalizer_finished') is not True
            or lifetime.get('closed_observed') is not True
            or lifetime.get('guard_released') is not True
            or lifetime.get('watchdog_release') is not False):
        failures.append('native_lifetime_unconfirmed')
    processes = report.get('owned_processes', {})
    if processes.get('confirmed') is not True or processes.get('remaining_children') != 0:
        failures.append('owned_process_exit_unconfirmed')
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description='nivraM desktop')
    parser.add_argument('--data-dir', type=Path, help='Alternate local profile for isolated testing')
    parser.add_argument('--smoke-report', type=Path, help='Run a hidden real-WebView startup check and exit')
    args = parser.parse_args(argv)
    data_dir = args.data_dir or Path(os.environ['LOCALAPPDATA']) / 'nivraM'
    instance = InstanceLock(data_dir)
    if not instance.acquire():
        return 2
    server = None
    thread = None
    api = None
    session = None
    downloads = None
    lifetime = None
    finalization = None
    policy = None
    process_job = None
    report = {'status': 'failed', 'version': __version__, 'route': 'native-WebView2-loopback'}
    try:
        # Establish OS ownership before loading the native runtime or creating
        # scientific children. Keep the noninheritable handle for this process
        # lifetime; closing it here would terminate its member host as well.
        from .owned_process_job import OwnedProcessJob
        process_job = OwnedProcessJob.for_current_process()
        import webview
        from .server import make_server
        server = make_server()
        server.desktop_mode = True
        thread = threading.Thread(target=server.serve_forever, name='nivraM-loopback', daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/'
        with urlopen(url + 'api/status', timeout=5) as response:
            if response.status != 200:
                raise RuntimeError('The local chemistry service did not become ready.')
        api = DesktopApi(data_dir, preparation_service=server.import_preparation)
        api._server = server
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
        webview.settings['REMOTE_DEBUGGING_PORT'] = None
        window = webview.create_window('nivraM', url=url, js_api=None,
            width=1280, height=800, min_size=(800, 600),
            hidden=bool(args.smoke_report), background_color='#FFFFFF')
        from .native_bridge import expose_desktop_api
        expose_desktop_api(window, api)
        api._set_window(window)
        from .webview_policy import attach_policy
        policy = attach_policy(window, url)
        from .webview_downloads import attach_downloads
        downloads = attach_downloads(window, url)
        api._downloads = downloads
        from .webview_cleanup import ManagedWebViewSession
        session = ManagedWebViewSession(data_dir,
            terminate_owned_processes=process_job.terminate_children)
        session.attach(window)
        api._cleanup = session
        close_coordinator, finalization, lifetime = _attach_lifecycle(
            window, api, server, thread, downloads, session, review=not args.smoke_report)

        def verify():
            try:
                deadline = time.monotonic() + 60
                # evaluate_js has its own shorter readiness wait. Honor our whole
                # startup budget before calling it on a cold native runtime.
                if not window.events._pywebviewready.wait(max(0, deadline - time.monotonic())):
                    report['readiness'] = {
                        'loaded': window.events.loaded.is_set(),
                        'bridge_ready': window.events._pywebviewready.is_set(),
                    }
                    raise RuntimeError('Native page/bridge did not become ready within 60 seconds.')
                while time.monotonic() < deadline:
                    result = window.evaluate_js("({ready:!!document.querySelector('#engine-version')?.textContent.includes('RDKit'),title:document.title,status:document.querySelector('#request-status')?.textContent,bridge:typeof window.pywebview?.api?.save_project==='function',agent:navigator.userAgent})")
                    if result and result.get('ready') and result.get('bridge'):
                        policy_state = policy.snapshot()
                        if policy_state['status'] != 'installed':
                            raise RuntimeError('Native request protection did not initialize.')
                        report.update(status='passed', observations=result, network_policy=policy_state)
                        break
                    time.sleep(0.2)
                else:
                    report['error'] = 'Native application initialization timed out.'
            except Exception as exc:
                report['error'] = type(exc).__name__ + ': ' + str(exc)
            finally:
                report['network_policy'] = policy.snapshot()
                window.destroy()

        webview.start(verify if args.smoke_report else None, gui='edgechromium',
            debug=False, private_mode=True, storage_path=str(session.storage_path))
        if not args.smoke_report:
            return 0
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        if not args.smoke_report:
            # A windowed build has no console; provide a bounded actionable error.
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                'nivraM could not start. Check the installed WebView2 runtime and local application files.\n' + type(exc).__name__,
                'nivraM startup error', 0x10)
            return 1
    finally:
        if lifetime is not None:
            report['native_lifetime'] = lifetime.finish(timeout_seconds=3)
        if finalization is None:
            finalization = _finalization_group(api, server, thread, downloads, session)
        # If startup failed before native FormClosed, there is no callback
        # owner. Start the same bounded group here, never run stages twice.
        if not finalization.snapshot()['started']:
            finalization.run(timeout_seconds=30)
        report['finalization'] = finalization.snapshot()
        if session is not None:
            report['webview_cleanup'] = session.snapshot()
        if downloads is not None:
            report['downloads'] = downloads.snapshot()
            report['download_shutdown'] = downloads.snapshot()
        instance.close()
        if args.smoke_report:
            if process_job is not None:
                try:
                    report['owned_processes'] = process_job.snapshot()
                except Exception:
                    report['owned_processes'] = {'status': 'unconfirmed', 'confirmed': False,
                        'remaining_children': None, 'reason': 'ownership_receipt_unavailable'}
            if policy is not None:
                report['network_policy'] = policy.snapshot()
            report['startup_status'] = report['status']
            report['smoke_failures'] = _smoke_failures(report)
            report['status'] = 'failed' if report['smoke_failures'] else 'passed'
            args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
            args.smoke_report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    # The smoke exit status must be chosen after final receipts are collected.
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
