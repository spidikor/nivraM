"""Bounded, visible Windows acceptance against real production modules and policy.

Uses synthetic ethanol only. A supervising process records actual child exit;
it never replaces native privacy results, opens developer tools, or disables the
request guard. No package-install or system-setting changes are performed.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import struct
import sys
import threading
import time
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REQUIRED_CHECKS = frozenset({
    'analysis-does-not-mutate-document', 'editor-and-native-bridge-ready',
    'exact-profile-png-preview-and-pixel-inspection', 'export-does-not-mutate-document',
    'native-9999-png-save-decoded-margins-opacity-and-nonmutation',
    'native-dark-mode-preserves-png-and-document', 'native-inspect-and-save-diagnostics',
    'native-large-png-detail-accept-preserve-and-one-undo', 'native-large-png-overview-navigation-only',
    'native-open-and-editor-readback', 'native-png-image-file-selected',
    'native-png-metadata-review-accept-preserve-and-one-undo',
    'native-profile-save-reopen-apply-preserves-graph', 'native-save-as-and-disk-readback',
    'native-save-existing-preserves-graph', 'native-window-loop-returned',
    'properties-panel', 'svg-image-blob-to-png', 'synthetic-import-through-real-ui-and-loopback',
})
DRAWING_PNG_REQUIRED_CHECKS = frozenset({
    'editor-and-native-bridge-ready', 'synthetic-hash-import-and-style',
    'native-spaced-profile-disk-readback-and-apply',
    'native-main-and-preview-png-rgba-equality',
    'native-theme-png-rgba-equality-and-document-nonmutation', 'native-window-loop-returned',
})
BATCH_PNG_REQUIRED_CHECKS = frozenset({
    'editor-and-native-bridge-ready', 'native-batch-profile-and-synthetic-inputs',
    'native-batch-two-file-open-and-folder-selection',
    'native-batch-partial-success-and-frozen-manifest',
    'native-batch-png-and-document-integrity', 'native-window-loop-returned',
})
BATCH_SELECTION_CANCEL_REQUIRED_CHECKS = frozenset({
    'editor-and-native-bridge-ready', 'native-batch-cancel-synthetic-inputs',
    'native-batch-open-cancelled', 'native-batch-folder-cancelled',
    'native-batch-selection-cancel-integrity', 'native-window-loop-returned',
})
BATCH_MIDJOB_CANCEL_REQUIRED_CHECKS = frozenset({
    'editor-and-native-bridge-ready', 'native-batch-midjob-synthetic-inputs',
    'native-batch-first-png-before-cancel', 'native-batch-midjob-cancel-and-late-return',
    'native-batch-cancelled-manifest-and-integrity', 'native-window-loop-returned',
})
BATCH_CLOSE_REQUIRED_CHECKS = frozenset({
    'editor-and-native-bridge-ready', 'native-close-synthetic-inputs',
    'native-close-declined-batch-integrity', 'native-close-approved-batch-integrity',
    'native-production-close-finalization', 'native-window-loop-returned',
})
BATCH_FREEZE_REQUIRED_CHECKS = frozenset({
    'editor-and-native-bridge-ready', 'native-freeze-profile-and-inputs',
    'native-freeze-prepared-boundary', 'native-freeze-edits-and-delayed-return',
    'native-freeze-png-manifest-and-editor-integrity', 'native-window-loop-returned',
})
BATCH_UNCERTAIN_REQUIRED_CHECKS = frozenset({
    'editor-and-native-bridge-ready', 'native-uncertain-synthetic-inputs',
    'native-lost-acknowledgement-stops', 'native-uncertain-manifest-stops',
    'native-uncertain-retained-files-and-editor', 'native-window-loop-returned',
})
EDITING_REQUIRED_CHECKS = frozenset({
    'editor-and-native-bridge-ready', 'native-edit-import', 'native-edit-rotation-history',
    'native-edit-save-and-disk', 'native-edit-reopen-exact', 'native-window-loop-returned',
})
RESIZE_REQUIRED_CHECKS = frozenset({'editor-and-native-bridge-ready', 'native-resize-success',
    'native-resize-rejection', 'native-resize-cancel-late', 'native-resize-manual', 'native-window-loop-returned'})
ELEMENTS_REQUIRED_CHECKS = frozenset({'editor-and-native-bridge-ready', 'native-elements-queued-history',
    'native-elements-expiry-prefix', 'native-elements-pointer-text', 'native-elements-pending-undo', 'native-window-loop-returned'})
from .acceptance_pointer import CHECKS as POINTER_CASE_CHECKS
POINTER_REQUIRED_CHECKS = POINTER_CASE_CHECKS | frozenset({'editor-and-native-bridge-ready','native-window-loop-returned'})
from .acceptance_editor_contract import CHECKS as EDITOR_CASE_CHECKS
EDITOR_REQUIRED_CHECKS = EDITOR_CASE_CHECKS | frozenset({'editor-and-native-bridge-ready','native-window-loop-returned'})
from .acceptance_save import CHECKS as SAVE_CASE_CHECKS
SAVE_REQUIRED_CHECKS = SAVE_CASE_CHECKS | frozenset({'editor-and-native-bridge-ready','native-window-loop-returned'})
from .acceptance_recovery_errors import CHECKS as RECOVERY_ERRORS_CASE_CHECKS
RECOVERY_ERRORS_REQUIRED_CHECKS = RECOVERY_ERRORS_CASE_CHECKS | frozenset({'editor-and-native-bridge-ready','native-window-loop-returned'})
from .acceptance_recovery import CHECKS as RECOVERY_CASE_CHECKS
RECOVERY_REQUIRED_CHECKS = RECOVERY_CASE_CHECKS | frozenset({'editor-and-native-bridge-ready','native-window-loop-returned'})
from .acceptance_recovery_restart import CHECKS as RESTART_CASE_CHECKS
RESTART_REQUIRED_CHECKS = RESTART_CASE_CHECKS | frozenset({'editor-and-native-bridge-ready','native-window-loop-returned'})
SCENARIO_CHECKS = {'recovery-restart': RESTART_REQUIRED_CHECKS,
    'recovery-producer': frozenset({'intentional-interruption-never-normal-acceptance'}),
    'recovery': RECOVERY_REQUIRED_CHECKS, 'recovery-errors': RECOVERY_ERRORS_REQUIRED_CHECKS, 'full': REQUIRED_CHECKS, 'drawing-png': DRAWING_PNG_REQUIRED_CHECKS,
                   'save': SAVE_REQUIRED_CHECKS,
                   'editor-contract': EDITOR_REQUIRED_CHECKS,
                   'pointer': POINTER_REQUIRED_CHECKS,
                   'elements': ELEMENTS_REQUIRED_CHECKS,
                   'resize': RESIZE_REQUIRED_CHECKS,
                   'editing': EDITING_REQUIRED_CHECKS,
                   'batch-png': BATCH_PNG_REQUIRED_CHECKS,
                   'batch-selection-cancel': BATCH_SELECTION_CANCEL_REQUIRED_CHECKS,
                   'batch-midjob-cancel': BATCH_MIDJOB_CANCEL_REQUIRED_CHECKS,
                   'batch-close': BATCH_CLOSE_REQUIRED_CHECKS,
                   'batch-freeze': BATCH_FREEZE_REQUIRED_CHECKS,
                    'batch-uncertain': BATCH_UNCERTAIN_REQUIRED_CHECKS,
                    'batch-uncertain-close': BATCH_UNCERTAIN_REQUIRED_CHECKS | frozenset({'native-production-close-finalization'})}
PRODUCTION_CLOSE_SCENARIOS = frozenset({'batch-close', 'batch-uncertain-close'})
PNG_EXPORT_STAGES = frozenset({
    'initial-export', 'digest', 'theme-checks', 'dark-export', 'comparison', 'restoration', 'complete',
})
PNG_ERROR_KINDS = frozenset({
    'Error', 'TypeError', 'RangeError', 'ReferenceError', 'SyntaxError', 'URIError', 'EvalError',
    'DOMException', 'SecurityError', 'InvalidStateError', 'NotSupportedError', 'OperationError',
    'AbortError', 'DataCloneError', 'QuotaExceededError', 'IndexSizeError', 'InvalidCharacterError',
    'NetworkError', 'TimeoutError', 'EncodingError', 'UnknownError', 'NotAllowedError', 'OtherError',
})


def png_export_observation(value):
    """Persist typed, allowlisted diagnostics, never PNG/SVG or arbitrary errors."""
    observation = {'resultType': type(value).__name__[:80]}
    if not isinstance(value, dict):
        return observation
    for key in ('ok', 'pngSignature'):
        observation[key] = value.get(key) if type(value.get(key)) is bool else None
    for key in ('width', 'height'):
        number = value.get(key)
        observation[key] = number if type(number) is int and 0 <= number <= 32768 else None
    stage = value.get('stage')
    observation['stage'] = stage if isinstance(stage, str) and stage in PNG_EXPORT_STAGES else 'unknown'
    error = value.get('errorKind')
    observation['errorKind'] = (error if isinstance(error, str) and error in PNG_ERROR_KINDS
                                else None if error is None else 'OtherError')
    return observation


def finalize_acceptance_report(report, *, policy=None):
    """A functional checkpoint cannot pass until all final receipts confirm it."""
    if policy is not None:
        # Pure in-memory receipt read after native cleanup, never UI dispatch.
        try:
            report['network_policy'] = policy.snapshot()
        except Exception as error:
            report['network_policy'] = {'status': 'unconfirmed', 'failure_code': 'snapshot_unavailable'}
            report['network_policy_snapshot_error_kind'] = type(error).__name__[:80]

    def mapping(value):
        return value if isinstance(value, dict) else {}

    def zero(value):
        return type(value) is int and value == 0

    failures = []
    if (report.get('status') != 'passed' or report.get('errors') != []
            or report.get('failures') or report.get('report_write_failures')):
        failures.append('native_report_failed')
    checks = report.get('checks')
    passed = {row.get('id') for row in checks if isinstance(row, dict)
              and isinstance(row.get('id'), str) and row.get('status') == 'passed'} if isinstance(checks, list) else set()
    scenario = report.get('scenario', 'full')
    required = SCENARIO_CHECKS.get(scenario) if isinstance(scenario, str) else None
    if required is None:
        failures.append('acceptance_scenario_unrecognized')
    elif not required.issubset(passed):
        failures.append('required_functional_checks_missing')
    policy = mapping(report.get('network_policy'))
    if (policy.get('status') != 'installed' or policy.get('installed_before_initial_navigation') is not True
            or not zero(policy.get('failures')) or policy.get('failure_code') is not None):
        failures.append('network_policy_unconfirmed')
    cleanup = mapping(report.get('cleanup'))
    session = mapping(cleanup.get('managed_session'))
    if (session.get('status') != 'removed' or cleanup.get('profile_directory_retained') is not False
            or session.get('browser_exit_kind') != 'Normal'
            or cleanup.get('collection_exit_kind') != 'Normal'
            or any(session.get(key) is not True for key in (
                'native_profile_matched', 'collection_exit_observed', 'collection_identity_matched',
                'control_disposed', 'runtime_cache_removed'))):
        failures.append('native_session_cleanup_unconfirmed')
    evaluator = mapping(report.get('evaluation_shutdown'))
    if (evaluator.get('workerExited') is not True or evaluator.get('timedOut') is not False
            or evaluator.get('outstandingCallMayComplete') is not False
            or evaluator.get('reportingErrorKinds') != []):
        failures.append('evaluation_shutdown_unconfirmed')
    dialogs = report.get('dialog_fixture_shutdown')
    if (not isinstance(dialogs, list) or (not dialogs and scenario not in ('resize', 'elements', 'pointer', 'editor-contract'))
            or (scenario in ('resize', 'elements', 'pointer', 'editor-contract') and len(dialogs) != 0)
            or (scenario == 'editing' and len(dialogs) != 3)
            or (scenario == 'save' and len(dialogs) != 7)
            or (scenario == 'batch-png' and len(dialogs) != 2)
            or (scenario == 'batch-midjob-cancel' and len(dialogs) != 2)
            or (scenario == 'batch-freeze' and len(dialogs) != 2)
            or (scenario in ('batch-uncertain', 'batch-uncertain-close') and len(dialogs) != 4)
            or (scenario == 'batch-close' and len(dialogs) != 4)
            or (scenario == 'batch-selection-cancel' and len(dialogs) != 3)
            or any(mapping(row).get('shutdownConfirmed') is not True for row in dialogs)):
        failures.append('dialog_fixture_shutdown_unconfirmed')
    for receipt in ('download_shutdown', 'downloads_final'):
        downloads = mapping(report.get(receipt))
        completed = downloads.get('completed')
        if (downloads.get('status') != 'closed' or downloads.get('shutdown_confirmed') is not True
                or not zero(downloads.get('active')) or type(completed) is not int
                or (completed != 0 if scenario in ('recovery-producer', 'recovery-restart', 'recovery', 'recovery-errors', 'save', 'editor-contract', 'pointer', 'elements', 'resize', 'editing', 'batch-png', 'batch-selection-cancel', 'batch-midjob-cancel', 'batch-close', 'batch-freeze', 'batch-uncertain', 'batch-uncertain-close') else completed < 1)
                or not zero(downloads.get('failures')) or downloads.get('failure_code') is not None):
            failures.append('download_shutdown_unconfirmed')
            break
    if report.get('image_preview_shutdown_confirmed') is not True:
        failures.append('image_worker_shutdown_unconfirmed')
    workers = mapping(report.get('worker_shutdown'))
    if workers.get('status') != 'confirmed' or workers.get('ownedProcessesExited') is not True:
        failures.append('scientific_worker_shutdown_unconfirmed')
    if 'process' in report:
        process = mapping(report['process'])
        if not zero(process.get('exit_code')) or process.get('forced_termination') is not False:
            failures.append('native_child_failed_or_timed_out')
    if scenario == 'batch-close':
        from .acceptance_batch_close import production_failures
        failures.extend(production_failures(report))
    if scenario == 'batch-uncertain-close':
        from .acceptance_uncertainty_close import production_failures
        failures.extend(production_failures(report))
    if scenario == 'batch-freeze':
        from .acceptance_batch_freeze import observation_failures
        failures.extend(observation_failures(report))
    if scenario == 'batch-uncertain':
        from .acceptance_batch_uncertain import observation_failures
        failures.extend(observation_failures(report))
    if scenario == 'editing':
        from .acceptance_editing import observation_failures
        failures.extend(observation_failures(report))
    if scenario == 'recovery-restart':
        from .acceptance_recovery_restart import observation_failures
        failures.extend(observation_failures(report))

    if scenario == 'recovery':
        from .acceptance_recovery import observation_failures
        failures.extend(observation_failures(report))

    if scenario == 'recovery-errors':
        from .acceptance_recovery_errors import observation_failures
        failures.extend(observation_failures(report))
    if scenario == 'save':
        from .acceptance_save import observation_failures
        failures.extend(observation_failures(report))
    if scenario == 'resize':
        from .acceptance_resize import observation_failures
        failures.extend(observation_failures(report))
    if scenario == 'elements':
        from .acceptance_elements import observation_failures
        failures.extend(observation_failures(report))
    if scenario == 'pointer':
        from .acceptance_pointer import observation_failures
        failures.extend(observation_failures(report))
    if scenario == 'editor-contract':
        from .acceptance_editor_contract import observation_failures
        failures.extend(observation_failures(report))
    # Stable failure codes supplement, never replace, the original observations.
    if failures and 'native_report_failed' not in failures:
        failures.insert(0, 'native_report_failed')
    report['acceptance_failures'] = failures
    report['status'] = 'failed' if failures else 'passed'
    return report


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    # The supervisor may read the last checkpoint after terminating a stuck
    # child. It must see either complete prior JSON or complete current JSON.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.nv-report-',
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        # A Windows reader that omits FILE_SHARE_DELETE can briefly prevent
        # atomic replacement. Keep the previous checkpoint intact and retry
        # only these access/sharing failures for at most 0.31 seconds of sleep.
        for delay in (0, .01, .02, .04, .08, .16):
            if delay:
                time.sleep(delay)
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33) or delay == .16:
                    raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def close_dialog_fixtures(choices):
    outcomes = []
    for choice in choices:
        try:
            outcomes.append(choice.close())
        except Exception as error:
            outcomes.append({'shutdownConfirmed': False, 'errorKind': type(error).__name__})
    return outcomes


def persist_cleanup_checkpoint(path, report):
    """A failed receipt write must not prevent attempts to close owned workers."""
    try:
        write_report(path, report)
        return True
    except Exception as error:
        report['status'] = 'failed'
        report['report_write_failures'] = report.get('report_write_failures', 0) + 1
        report['last_report_write_error_kind'] = type(error).__name__
        return False


def record_failure(report, error):
    # Native/JavaScript exception messages can include script contents or paths.
    # Fixed stages, sanitized evaluator metadata and our own line identify the
    # failing operation without persisting arbitrary exception text.
    failure = {'kind': type(error).__name__[:80]}
    for key in ('errno', 'winerror'):
        value = getattr(error, key, None)
        if type(value) is int and 0 <= value <= 65535:
            failure[key] = value
    for key in ('stage', 'last_completed_check', 'evaluation'):
        if key in report:
            # These are fixed harness labels and numeric evaluator metadata,
            # not JS results. Snapshot now: cleanup publishes later evaluations.
            failure[key] = copy.deepcopy(report[key])
    trace = error.__traceback__
    while trace is not None:
        # Frozen code objects retain relative filenames; __file__ is absolute.
        if Path(trace.tb_frame.f_code.co_filename).name == Path(__file__).name:
            failure['harnessLine'] = trace.tb_lineno
        trace = trace.tb_next
    report.setdefault('failures', []).append(failure)
    report['errors'].append(failure['kind'] + ': acceptance operation failed; see stage and failure metadata.')
    report['status'] = 'failed'


def observe_webview_parent_phase(report, capture, phase):
    """Keep separate immutable observations without changing cleanup outcomes."""
    if capture is None:
        return
    try:
        if type(phase) is not str or phase not in (
                'shutdown_return', 'window_loop_return', 'handle_wait_return'):
            raise ValueError('Unsupported parent WebView observation phase.')
        from .webview_parent_observations import ParentWebViewCapture, webview_parent_diagnostic
        packet = None
        if type(capture) is not ParentWebViewCapture:
            report['cleanup']['parent_observation_failed'] = True
        elif not report['cleanup'].get('parent_observation_failed', False):
            packet = ParentWebViewCapture.freeze(capture, phase)
        report['cleanup'].setdefault('parent_observations', {})[phase] = webview_parent_diagnostic(packet)
    except Exception:
        # Diagnostic failure cannot skip window destruction or handle disposal.
        try:
            report['cleanup']['parent_observation_failed'] = True
            from .webview_parent_observations import webview_parent_diagnostic
            if type(phase) is str and phase in (
                    'shutdown_return', 'window_loop_return', 'handle_wait_return'):
                report['cleanup'].setdefault('parent_observations', {})[phase] = webview_parent_diagnostic(None)
        except Exception:
            pass


def observe_webview_parent_handle(report, capture, status, exited=None):
    """Record the existing pinned-handle wait; never perform another probe."""
    if capture is None:
        return
    try:
        from .webview_parent_observations import ParentWebViewCapture
        if type(capture) is not ParentWebViewCapture:
            report['cleanup']['parent_observation_failed'] = True
            return
        ParentWebViewCapture.pinned_handle_probe(capture, status, exited=exited)
    except Exception:
        try:
            report['cleanup']['parent_observation_failed'] = True
        except Exception:
            pass


def run_child(report_path, profile, scenario='full'):
    if scenario not in SCENARIO_CHECKS:
        raise ValueError('Unsupported native acceptance scenario.')
    import webview
    from nivram import __version__
    from nivram.desktop import DesktopApi, InstanceLock
    from nivram.server import make_server
    from nivram.webview_policy import attach_policy
    from nivram.webview_cleanup import ManagedWebViewSession

    start = time.monotonic()
    report = {
        'status': 'failed', 'version': __version__, 'route': 'visible-native-WebView2-frozen' if getattr(sys, 'frozen', False) else 'visible-native-WebView2-source',
        'scenario': scenario,
        'fixture': ('Synthetic native rotation, reflection including meso, scale, keys, palette and theme.' if scenario == 'editor-contract'
                    else 'Synthetic local project saves, conflicts and explicit post-publication fault injection.' if scenario == 'save'
                    else 'Synthetic native erase, pointer editing, selection and fixed modifier keys.' if scenario == 'pointer'
                    else 'Synthetic single atoms; native element keys, hover, grouped history and cancellation.' if scenario == 'elements'
                    else 'Synthetic long chain and ions; native resize across held real render responses.' if scenario == 'resize'
                    else 'Synthetic isotope/charge/chiral drawing; native rotation, Undo/Redo, save and reopen.' if scenario == 'editing'
                    else 'Synthetic N[C@H](C)O; no private structures loaded.' if scenario == 'drawing-png'
                    else 'Two synthetic batches; lost write acknowledgement and uncertain manifest publication.' if scenario == 'batch-uncertain'
                    else 'Two synthetic ethanol projects; source and saved-profile edits after preparation.' if scenario == 'batch-freeze'
                    else 'Two synthetic ethanol batches; decline and approve real unsaved close.' if scenario == 'batch-close'
                    else 'Two synthetic ethanol projects; native mid-job cancellation with a delayed real preparation response.' if scenario == 'batch-midjob-cancel'
                    else 'Synthetic ethanol plus malformed MOL; native chooser cancellation only.' if scenario == 'batch-selection-cancel'
                    else 'One synthetic stereochemical .nivram and one malformed .mol; no private structures loaded.' if scenario == 'batch-png'
                    else 'Synthetic ethanol, SMILES CCO; no private structures loaded.'),
        'checks': [], 'errors': [], 'cleanup': {},
        'naming_ui_verified': False, 'naming_roundtrip_verified': False,
        'limitations': [
            ('Frozen executable acceptance; installation and clean-machine acceptance remain separate gates.' if getattr(sys, 'frozen', False) else 'Source runtime acceptance; this does not certify a packaged executable or installer.'),
            'A matched parser round trip does not establish a Preferred IUPAC Name.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
            'Unsaved-close coordination and external-provider behavior are separate gates.',
            'PNG checks use synthetic ethanol and a synthetic 20-carbon chain; they are not a whole-corpus visual-calibration claim.',
        ],
    }
    if scenario == 'editor-contract':
        report['limitations'] = [report['limitations'][0],
            'Owned WebView2 SDK input and actual trusted DOM observations; no global input or inspector port.',
            'Synthetic molecules only. Reflection rejection and cancellation hold real 200 responses and explicitly inject a 422 or late response.',
            'Exact document/history, coordinates and independently computed per-center stereo are asserted.',
            'No file picker or download. Physical keyboard/DPI, standalone browser lifecycle, packaging and installation remain separate.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'pointer':
        report['limitations'] = [report['limitations'][0],
            'Owned WebView2 SDK input; actual trusted DOM pointer/key observations and exact document/history assertions.',
            'DPR 1/2 are page-local device-scale emulation, not physical Windows DPI or hardware-input qualification.',
            'pointercancel and lostpointercapture are explicitly synthetic boundary injections after real trusted capture; late release is trusted.',
            'Import text is staged in its input; production Load, tools, selectors, edit/history and modal actions use the real controls.',
            'No file picker/download. Packaging, installation, prior native suites and wider integration remain separate gates.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'elements':
        report['limitations'] = [report['limitations'][0],
            'Trusted owned Windows key events and SDK hover on the native WebView2 control; immediate previews, 500 ms timing and exact document/history state.',
            'Undo/Redo uses production buttons. Modifier shortcuts, physical hardware input, IME and freeform pointer gestures remain separate.',
            'Held real render responses exercise queued commits and cancellation; no file picker or download is expected.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'resize':
        report['limitations'] = [report['limitations'][0],
            'Actual native window resize; real render delivery is held, rejected, or released after cancellation.',
            'Trusted fixed button clicks, one owned Escape key pair and accessible pan controls; freeform pointer gestures and element typing remain separate.',
            'No file picker or download is expected. Saving, reopening, installation and package qualification remain separate.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'save':
        report['limitations'] = [report['limitations'][0],
            'Synthetic projects only; seven actual owned native Save chooser returns and trusted fixed buttons.',
            'Production Windows storage covers conditional update, competing replacement before writer acquisition, and a real hard-link rejection.',
            'A second thread performs the controlled replacement. A separate held save checks newer editor state.',
            'One acknowledgement error is injected after the actual exclusive atomic writer returns and its published bytes are observed.',
            'Current local fixed NTFS only; network/removable storage, power loss, process-restart recovery, package and installed qualification remain separate.',
            'Zero WebView downloads; guard counters cover intercepted requests, not independent runtime background networking.',
        ]
    elif scenario == 'editing':
        report['limitations'] = [report['limitations'][0],
            'One synthetic isotope/charge/chiral document; trusted button clicks and three exact native picker returns.',
            'Whole-drawing clockwise rotation, Undo/Redo and reopening from a distinct saved comparison state only.',
            'Pointer dragging, keyboard element entry, selection boundaries, unsaved confirmation, process restart and installed acceptance remain separate gates.',
            'Zero WebView downloads are required; project saving uses the production desktop store.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'drawing-png':
        report['limitations'] = [report['limitations'][0],
            'One synthetic stereochemical drawing checks native profiles and PNGs; it is not whole-corpus visual calibration or Marvin JS parity.',
            'Native batch selection/publication, the broader acceptance sequence, installed acceptance, clean-machine and upgrade/rollback are separate gates.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'batch-close':
        report['limitations'] = [report['limitations'][0],
            'Two active synthetic batches test declined and approved unsaved close via the production lifecycle.',
            'The second real preparation response is delayed after the scientific worker finishes; active-worker interruption remains unverified.',
            'Late-write probes call the real desktop method directly; no post-close editor DOM inspection is possible.',
            'Four native chooser returns and both original native confirmation returns are required.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario in ('batch-uncertain', 'batch-uncertain-close'):
        report['limitations'] = [report['limitations'][0],
            'Two synthetic native batches use real PNG writes with acceptance-only fault injection.',
            'The first write acknowledgement is lost after publication; the other manifest acknowledgement is lost after its real atomic commit.',
            'The last committed manifest and terminal memory receipt are compared separately when they differ.',
            'Real native cancellation, no next preparation, retained files and complete editor snapshots are required.',
            'Zero WebView downloads and exactly four original native chooser returns are required.',
            'Installed use, whole-corpus calibration and other failure phases remain separate gates.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'batch-freeze':
        report['limitations'] = [report['limitations'][0],
            'Two real synthetic preparations; the second response is held after adoption and returned unchanged.',
            'Source replacement, a new saved-profile revision and external replacement of the selected revision occur only in the isolated profile.',
            'Full editor snapshots, original provenance, PNG pixels, collisions and retained external edits are checked.',
            'Acknowledgement loss, uncertain manifest publication, installed use and whole-corpus calibration remain separate gates.',
            'Zero WebView downloads and exactly two original native chooser returns are required.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'batch-midjob-cancel':
        report['limitations'] = [report['limitations'][0],
            'Cancel after one completed PNG with the second real preparation response delayed by an acceptance-only barrier.',
            'The scientific worker finishes before the delay; this does not qualify cancellation of an active scientific worker.',
            'A direct desktop-method probe checks rejection of a late PNG write separately from the native UI cancellation.',
            'Production window close, other cancellation phases, installed acceptance and upgrade/rollback remain separate gates.',
            'Zero WebView downloads and exactly two owned chooser shutdowns are required.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'batch-selection-cancel':
        report['limitations'] = [report['limitations'][0],
            'OPEN Cancel and OPEN followed by FOLDER Cancel only; mid-job cancel and production close remain separate gates.',
            'Cancellation must leave the editor, synthetic inputs and existing output unchanged; no batch output is expected.',
            'Zero WebView downloads and exactly three owned dialog fixture shutdowns are required.',
            'Installed acceptance, clean-machine and upgrade/rollback remain separate gates.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    elif scenario == 'batch-png':
        report['limitations'] = [report['limitations'][0],
            'Two synthetic files check native batch partial success and PNG publication; valid native MOL conversion is a separate gate.',
            'Batch PNG publication uses the desktop batch writer, so zero WebView downloads are required.',
            'The full acceptance sequence, whole-corpus visual calibration, Marvin JS parity, installed acceptance, clean-machine and upgrade/rollback remain separate gates.',
            'Guard counters cover intercepted content requests, not independent runtime background networking.',
        ]
    instance = InstanceLock(profile)
    if not instance.acquire():
        report['errors'].append('The isolated test profile is already in use.')
        write_report(report_path, report)
        return 1
    server = None
    api = None
    thread = None
    session = None
    webview_capture = None
    downloads = None
    policy = None
    evaluator = None
    close_coordinator = finalization = lifetime = process_job = None
    production_close = scenario in PRODUCTION_CLOSE_SCENARIOS
    close_facts_key = 'batch_close' if scenario == 'batch-close' else 'uncertainty_close'
    verify_done = threading.Event()
    choices = []
    runtime = {'environment': None, 'process': None, 'handler': None}
    exited = threading.Event()
    warning_codes = []

    class Warnings(logging.Handler):
        def emit(self, record):
            if 'Failed to delete user data folder' in record.getMessage():
                warning_codes.append('pywebview_private_profile_cleanup_failed')

    warnings = Warnings()
    logging.getLogger('pywebview').addHandler(warnings)
    try:
        if production_close:
            from .owned_process_job import OwnedProcessJob
            process_job = OwnedProcessJob.for_current_process()
        server = make_server()
        server.desktop_mode = True
        thread = threading.Thread(target=server.serve_forever, daemon=True, name='acceptance-loopback')
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/'
        api = DesktopApi(profile, preparation_service=server.import_preparation)
        api._server = server
        if scenario in ('recovery-producer', 'recovery-restart', 'recovery', 'recovery-errors', 'save', 'editor-contract', 'pointer', 'elements', 'resize', 'editing', 'batch-png', 'batch-selection-cancel', 'batch-midjob-cancel', 'batch-close', 'batch-freeze', 'batch-uncertain', 'batch-uncertain-close'):
            from .webview_parent_observations import ParentWebViewCapture
            webview_capture = ParentWebViewCapture()
            session = ManagedWebViewSession(profile, parent_observations=webview_capture)
        else:
            session = ManagedWebViewSession(profile)
        api._cleanup = session
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
        webview.settings['REMOTE_DEBUGGING_PORT'] = None
        window = webview.create_window('nivraM - native acceptance (' + ('synthetic native editor contract' if scenario == 'editor-contract'
            else 'synthetic native pointer' if scenario == 'pointer'
            else 'synthetic native elements' if scenario == 'elements'
            else 'synthetic native resize' if scenario == 'resize'
            else 'synthetic recovery' if scenario == 'recovery'
            else 'synthetic recovery errors' if scenario == 'recovery-errors'
            else 'synthetic native save' if scenario == 'save'
            else 'synthetic native editing' if scenario == 'editing'
            else 'synthetic drawing PNG' if scenario == 'drawing-png'
            else 'synthetic batch PNG' if scenario == 'batch-png'
            else 'synthetic batch chooser cancellation' if scenario == 'batch-selection-cancel'
            else 'synthetic batch production close' if scenario == 'batch-close'
            else 'synthetic batch uncertainty and production close' if scenario == 'batch-uncertain-close'
            else 'synthetic batch publication uncertainty' if scenario == 'batch-uncertain'
            else 'synthetic batch settings freeze' if scenario == 'batch-freeze'
            else 'synthetic batch mid-job cancellation' if scenario == 'batch-midjob-cancel' else 'synthetic ethanol') + ')',
            url=url, js_api=None, width=1280, height=800, min_size=(800, 600),
            hidden=False, background_color='#FFFFFF')
        from .native_bridge import expose_desktop_api
        report['native_bridge'] = expose_desktop_api(window, api)
        api._set_window(window)
        policy = attach_policy(window, url)
        from .webview_downloads import attach_downloads
        downloads = attach_downloads(window, url)
        api._downloads = downloads
        session.attach(window)

        def attach_exit_observer():
            control = window.native.webview
            def initialized(sender, args):
                if not args.IsSuccess:
                    return
                try:
                    from System.Diagnostics import Process
                    environment = sender.CoreWebView2.Environment
                    process = Process.GetProcessById(int(sender.CoreWebView2.BrowserProcessId))
                    _ = process.Handle  # Pin this process identity before it can exit.
                    def process_exited(_sender, event):
                        report['cleanup']['collection_exit_kind'] = str(event.BrowserProcessExitKind)
                        exited.set()
                    environment.BrowserProcessExited += process_exited
                    runtime.update(environment=environment, process=process, handler=process_exited)
                except Exception as error:
                    report['cleanup']['observer_error'] = type(error).__name__
            control.CoreWebView2InitializationCompleted += initialized
            window._acceptance_initialized = initialized
        window.events.before_show += attach_exit_observer
        if production_close:
            from .desktop import _attach_lifecycle
            close_coordinator, finalization, lifetime = _attach_lifecycle(
                window, api, server, thread, downloads, session)

        def check(identifier, **values):
            report['checks'].append({**values, 'id': identifier, 'status': 'passed'})
            report['last_completed_check'] = identifier
            write_report(report_path, report)

        from .acceptance_evaluator import BoundedEvaluator
        def observe_evaluation(observation):
            report['evaluation'] = observation
            if observation['status'] in ('failed', 'timed-out'):
                report['failed_evaluation'] = observation
            write_report(report_path, report)
        evaluator = BoundedEvaluator(window.evaluate_js, observe_evaluation)
        evaluate = evaluator

        def stage(identifier, *, best_effort=False):
            report['stage'] = identifier
            if best_effort:
                persist_cleanup_checkpoint(report_path, report)
            else:
                write_report(report_path, report)

        def choose(target, timeout=20, mode='file', *, parent_io=False, action='select', observation_binding=None):
            from .native_dialog_fixture import NativeFileChoice
            choice = NativeFileChoice(window, target, profile, timeout=timeout, mode=mode,
                                      progress=mode in ('open-multiple', 'folder'), parent_io=parent_io, action=action,
                                      observation_binding=observation_binding)
            choices.append(choice)
            return choice.start()

        def poll(expression, timeout=20):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                value = evaluate(expression, timeout=min(10, remaining), stage='poll-native-ui')
                if value:
                    return value
                time.sleep(.1)
            raise TimeoutError('The expected native UI state did not become ready.')

        def poll_png(stage, expression):
            # Acceptance diagnostics only: preserve the existing poll condition
            # and its default timeout; never collect source/DOM/chemical text.
            observation = {'stage': stage, 'status': 'waiting', 'timeout_seconds': 20}
            report['png_import_wait'] = observation
            write_report(report_path, report)
            try:
                value = poll(expression)
            except Exception as error:
                observation['status'] = 'failed'
                observation['failure_kind'] = 'timeout' if isinstance(error, TimeoutError) else 'evaluation_error'
                try:
                    observation['ui'] = evaluate("""(() => {
                        const $ = id => document.getElementById(id);
                        const number = (value, max = 2147483647) =>
                            Number.isSafeInteger(value) && value >= 0 ? Math.min(value, max) : null;
                        const flag = (id, key) => typeof $(id)?.[key] === 'boolean' ? $(id)[key] : null;
                        const inputs = [...document.querySelectorAll('#image-import-components input')];
                        const source = $('image-import-source'), snapshot = window.nivramDocument?.getSnapshot();
                        const status = $('image-import-status');
                        const statusText = status?.textContent ?? '';
                        const limitation = $('image-import-limit')?.textContent ?? '';
                        const statusCodes = new Map([
                            ['Choose a PNG to inspect locally…', 'inspection_pending'],
                            ['Select components explicitly, then preview them.', 'components_ready'],
                            ['Preview the explicitly selected components.', 'selection_changed'],
                            ['Preparing the selected components…', 'preview_pending'],
                            ['Review both panels and the warnings, then confirm before importing.', 'review_ready'],
                            ['Metadata preview prepared. Import is disabled until a bounded source or crop can be reviewed.', 'review_unavailable'],
                            ['Verifying the preserved source and importing…', 'accept_pending'],
                            ['No supported editable components were recovered. Optical recognition is unavailable in this build.', 'no_supported_components']
                        ]);
                        const errorCodes = new Map([
                            ['Image inspection exceeded its time limit; the drawing is unchanged.', 'worker_timeout'],
                            ['Image metadata could not be inspected within the supported limits.', 'worker_result_rejected'],
                            ['The image inspection returned an invalid source identity.', 'inspection_identity_rejected']
                        ]);
                        let limitationCode = 'other_or_none';
                        if (limitation === 'The original PNG could not be decoded for visual review. Import is disabled.')
                            limitationCode = 'source_decode_unavailable';
                        if (limitation === 'This source needs a bounded crop preview. Crop review is not yet available, so importing it into the editor is disabled.')
                            limitationCode = 'source_crop_required';
                        const hasError = status?.classList.contains('image-import-error') === true;
                        return {
                            schemaVersion: 1,
                            dialogOpen: flag('image-import-dialog', 'open'),
                            componentCount: number(inputs.length, 1025),
                            enabledComponentCount: number(inputs.filter(input => !input.disabled).length, 1025),
                            checkedComponentCount: number(inputs.filter(input => input.checked).length, 1025),
                            sourcePresent: source?.hasAttribute('src') === true,
                            sourceComplete: flag('image-import-source', 'complete'),
                            sourceNaturalWidth: number(source?.naturalWidth),
                            sourceNaturalHeight: number(source?.naturalHeight),
                            candidatePrimitiveCount: number($('image-import-candidate')?.querySelectorAll('path,line,text,circle,ellipse,polygon,polyline,rect').length, 65536),
                            previewDisabled: flag('image-import-preview', 'disabled'),
                            acknowledgeDisabled: flag('image-import-acknowledge', 'disabled'),
                            acknowledgeChecked: flag('image-import-acknowledge', 'checked'),
                            acceptDisabled: flag('image-import-accept', 'disabled'),
                            revision: number(snapshot?.revision, Number.MAX_SAFE_INTEGER),
                            dirty: typeof snapshot?.dirty === 'boolean' ? snapshot.dirty : null,
                            editorReady: $('request-status')?.textContent === 'Ready',
                            hasUiError: hasError,
                            statusCode: statusCodes.get(statusText) ?? 'other',
                            errorCode: hasError ? errorCodes.get(statusText) ?? 'other_ui_error' : 'none',
                            limitationCode
                        };
                    })()""")
                except Exception:
                    observation['ui'] = {'status': 'unavailable'}
                # An observation/report failure must not replace the original
                # failing poll. The normal final report write remains in place.
                try:
                    write_report(report_path, report)
                except Exception:
                    observation['persistence'] = 'failed'
                raise
            observation['status'] = 'passed'
            write_report(report_path, report)
            return value

        def verify():
            try:
                if not window.events._pywebviewready.wait(60):
                    raise TimeoutError('The native bridge did not become ready within 60 seconds.')
                ready = poll("""(() => {
                    const status = document.querySelector('#request-status')?.textContent;
                    const engine = document.querySelector('#engine-version')?.textContent;
                    return status === 'Ready' && engine?.includes('RDKit') &&
                        typeof window.pywebview?.api?.save_project === 'function' &&
                        window.nivramDocument ? {status, engine, agent:navigator.userAgent} : false;
                })()""")
                if policy.snapshot()['status'] != 'installed':
                    raise RuntimeError('Native request protection is not installed.')
                check('editor-and-native-bridge-ready', **ready)
                if scenario in ('recovery-producer','recovery-restart'):
                    from .acceptance_recovery_restart import run
                    run(evaluate=evaluate,poll=poll,choose=choose,check=check,stage=stage,
                        report=report,profile=profile,api=api,window=window,
                        producer=scenario=='recovery-producer')
                    report['status']='passed'
                    return

                if scenario == 'recovery':
                    from .acceptance_recovery import run
                    run(evaluate=evaluate,poll=poll,choose=choose,check=check,stage=stage,
                        report=report,profile=profile,api=api,window=window)
                    report['status']='passed'
                    return

                if scenario == 'recovery-errors':
                    from .acceptance_recovery_errors import run
                    run(evaluate=evaluate,poll=poll,choose=choose,check=check,stage=stage,
                        report=report,profile=profile,api=api,window=window)
                    report['status']='passed'
                    return
                if scenario == 'save':
                    from .acceptance_save import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window)
                    report['status'] = 'passed'
                    return
                if scenario == 'editor-contract':
                    from .acceptance_editor_contract import run
                    run(evaluate=evaluate,poll=poll,check=check,stage=stage,report=report,window=window)
                    report['status']='passed'
                    return
                if scenario == 'pointer':
                    from .acceptance_pointer import run
                    run(evaluate=evaluate,poll=poll,check=check,stage=stage,report=report,window=window)
                    report['status']='passed'
                    return
                if scenario == 'elements':
                    from .acceptance_elements import run
                    run(evaluate=evaluate, poll=poll, check=check, stage=stage, report=report, window=window)
                    report['status'] = 'passed'
                    return
                if scenario == 'resize':
                    from .acceptance_resize import run
                    run(evaluate=evaluate, poll=poll, check=check, stage=stage, report=report, window=window)
                    report['status'] = 'passed'
                    return
                if scenario == 'editing':
                    from .acceptance_editing import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window)
                    report['status'] = 'passed'
                    return
                if scenario == 'drawing-png':
                    from .acceptance_drawing_png import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window)
                    report['status'] = 'passed'
                    return
                if scenario == 'batch-close':
                    from .acceptance_batch_close import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window,
                        coordinator=close_coordinator, finalization=finalization, lifetime=lifetime)
                    report['status'] = 'passed'
                    return
                if scenario in ('batch-uncertain', 'batch-uncertain-close'):
                    from .acceptance_batch_uncertain import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window)
                    if production_close:
                        from .acceptance_uncertainty_close import request_close
                        request_close(evaluate=evaluate, report=report, profile=profile, api=api,
                                      window=window, coordinator=close_coordinator, lifetime=lifetime)
                    report['status'] = 'passed'
                    return
                if scenario == 'batch-freeze':
                    from .acceptance_batch_freeze import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window)
                    report['status'] = 'passed'
                    return
                if scenario == 'batch-midjob-cancel':
                    from .acceptance_batch_midjob import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window)
                    report['status'] = 'passed'
                    return
                if scenario == 'batch-selection-cancel':
                    from .acceptance_batch_cancel import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window)
                    report['status'] = 'passed'
                    return
                if scenario == 'batch-png':
                    from .acceptance_batch_png import run
                    run(evaluate=evaluate, poll=poll, choose=choose, check=check, stage=stage,
                        report=report, profile=profile, api=api, downloads=downloads, window=window)
                    report['status'] = 'passed'
                    return
                evaluate("""(() => {
                    document.getElementById('import-button').click();
                    document.getElementById('structure-input').value = 'CCO';
                    document.getElementById('load-structure').click(); return true;
                })()""")
                values = poll("""(() => {
                    const $ = id => document.getElementById(id);
                    const snapshot = window.nivramDocument.getSnapshot();
                    if ($('property-smiles').value !== 'CCO' || snapshot.document.graph.atoms.length !== 3 ||
                        snapshot.document.graph.bonds.length !== 2 || $('request-status').textContent !== 'Ready') return false;
                    return {formula:$('property-formula').textContent, mass:$('property-mass').textContent,
                        exactMass:$('property-exact-mass').textContent, logP:$('property-logp').textContent,
                        smiles:$('property-smiles').value, provenance:$('property-provenance').textContent};
                })()""")
                if (values['formula'] != 'C2H6O' or not values['mass'].startswith('46.0690') or
                        not values['exactMass'].startswith('46.041865') or values['logP'] != '-0.001'):
                    raise AssertionError('The real ethanol property panel failed its pinned-engine reference check.')
                check('synthetic-import-through-real-ui-and-loopback', atoms=3, bonds=2)
                check('properties-panel', **values)
                before = evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)')
                evaluate("""(() => {
                    const button = document.getElementById('calculate-name');
                    button.closest('details').open = true; button.click(); return true;
                })()""")
                from .acceptance_naming import (NamingPanelTimeout, naming_diagnostics, naming_verification,
                    require_naming_response_details, wait_for_naming)
                try:
                    naming_deadline = time.monotonic() + 35
                    def evaluate_naming(expression):
                        remaining = naming_deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('The naming acceptance deadline expired before evaluation.')
                        return evaluate(expression, timeout=min(10, remaining), stage='systematic-naming')
                    naming = wait_for_naming(evaluate_naming, timeout=35)
                except NamingPanelTimeout as error:
                    report['naming_observation'] = naming_diagnostics(error.snapshot, terminal=False)
                    write_report(report_path, report)
                    raise
                report['naming_observation'] = naming_diagnostics(naming, terminal=True)
                write_report(report_path, report)
                require_naming_response_details(naming)
                if naming['name'] == 'No accepted name':
                    report['naming_ui_verified'] = False
                    report['naming_roundtrip_verified'] = False
                    if not any(label in naming['details'].lower() for label in ('unavailable', 'unsupported', 'failed', 'timeout')):
                        raise AssertionError('Naming returned no name without an explicit capability/error state.')
                    report['checks'].append({'id': 'naming-panel', 'status': 'observed-unavailable',
                                             **report['naming_observation']})
                    report['limitations'].append('The optional naming engine did not produce an accepted name in this run.')
                else:
                    if naming['name'] != 'ethanol':
                        raise AssertionError('The optional naming engine produced an unexpected ethanol name.')
                    report.update(naming_verification(naming))
                    check('naming-panel', **report['naming_observation'])
                if evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)') != before:
                    raise AssertionError('Property/naming evaluation changed the document.')
                check('analysis-does-not-mutate-document')
                completed = threading.Event()
                exported = {}
                def export_complete(value):
                    exported['value'] = value
                    completed.set()
                stage('initial-png-export')
                evaluate("""(async () => {
                    let stage='initial-export', failedStage=null;
                    const observation={};
                    try {
                        const {exportPng} = await import('/modules/export.mjs');
                        const result = await exportPng(document.getElementById('drawing'),
                            {width:640, marginMode:'percent', margin:2});
                        observation.width=result.width;observation.height=result.height;
                        stage='digest';
                        const bytes = new Uint8Array(await result.blob.arrayBuffer());
                        observation.pngSignature=[137,80,78,71,13,10,26,10].every((value,index)=>bytes[index]===value);
                        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
                        stage='theme-checks';
                        const drawing=document.getElementById('drawing'),toggle=document.getElementById('theme-toggle');
                        const originalSvg=drawing.outerHTML;
                        const originalSnapshot=JSON.stringify(window.nivramDocument.getSnapshot());
                        if(!toggle||toggle.getAttribute('aria-pressed')!=='false')throw new Error('The native window did not start in light mode.');
                        let darkBytes,greyBonds;
                        try{
                            toggle.click();
                            if(document.documentElement.dataset.theme!=='dark'||toggle.getAttribute('aria-pressed')!=='true')
                                throw new Error('The native theme toggle did not activate.');
                            const bonds=[...drawing.querySelectorAll('path[class^="bond-"]')].filter(el=>el.style.stroke==='rgb(0, 0, 0)');
                            greyBonds=bonds.length;
                            if(!greyBonds||bonds.some(el=>getComputedStyle(el).stroke!=='rgb(214, 214, 214)'))
                                throw new Error('Native dark-mode carbon bonds did not render light grey.');
                            stage='dark-export';
                            const dark=await exportPng(drawing,{width:640,marginMode:'percent',margin:2});
                            darkBytes=new Uint8Array(await dark.blob.arrayBuffer());
                            stage='comparison';
                            if(dark.width!==result.width||dark.height!==result.height||darkBytes.length!==bytes.length||
                                darkBytes.some((value,index)=>value!==bytes[index]))throw new Error('Native dark mode changed exported PNG bytes.');
                        }catch(error){failedStage=stage;throw error;}
                        finally{
                            stage='restoration';
                            try{if(toggle.getAttribute('aria-pressed')==='true')toggle.click();}
                            catch(error){failedStage=stage;throw error;}
                        }
                        if(drawing.outerHTML!==originalSvg||JSON.stringify(window.nivramDocument.getSnapshot())!==originalSnapshot)
                            throw new Error('Native theme activation changed the drawing or document history.');
                        stage='complete';
                        return {ok:true, stage, errorKind:null, width:result.width, height:result.height, padding:result.padding,
                            bytes:bytes.length, type:result.blob.type,
                            theme:{greyBonds,identicalPngBytes:true,unchangedDrawingAndHistory:true,restoredLight:true},
                            pngBase64:btoa(Array.from(bytes,value=>String.fromCharCode(value)).join('')),
                            pngSignature:[137,80,78,71,13,10,26,10].every((value,index)=>bytes[index]===value),
                            sha256:Array.from(digest,value=>value.toString(16).padStart(2,'0')).join('')};
                    } catch (error) {
                        const kinds=new Set(['Error','TypeError','RangeError','ReferenceError','SyntaxError','URIError','EvalError',
                            'DOMException','SecurityError','InvalidStateError','NotSupportedError','OperationError','AbortError',
                            'DataCloneError','QuotaExceededError','IndexSizeError','InvalidCharacterError','NetworkError',
                            'TimeoutError','EncodingError','UnknownError','NotAllowedError']);
                        return {...observation,ok:false,stage:failedStage||stage,
                            errorKind:kinds.has(error?.name)?error.name:'OtherError'};
                    }
                })()""", callback=export_complete)
                if not completed.wait(30):
                    raise TimeoutError('Native SVG-blob rasterization did not complete within 30 seconds.')
                result = exported.get('value')
                report['png_export_observation'] = png_export_observation(result)
                stage('initial-png-export-validating')
                if not isinstance(result, dict) or not result.get('ok') or result.get('width') != 640 or not result.get('pngSignature'):
                    raise AssertionError('Native SVG-to-PNG export failed: ' + str(result))
                exported_png = base64.b64decode(result.pop('pngBase64'), validate=True)
                if hashlib.sha256(exported_png).hexdigest() != result['sha256']:
                    raise AssertionError('Native PNG transport changed the exported bytes.')
                check('svg-image-blob-to-png', **result)
                check('native-dark-mode-preserves-png-and-document', **result['theme'])
                if evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)') != before:
                    raise AssertionError('PNG export changed the document.')
                check('export-does-not-mutate-document')
                poll("!!document.getElementById('save-profile-button')")
                geometry = evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document.graph)')
                evaluate("""(() => {
                    document.querySelector('.profile-controls').open=true;
                    document.getElementById('profile-name').value='Synthetic acceptance profile';
                    document.getElementById('save-profile-button').click(); return true;
                })()""")
                poll("document.getElementById('profile-choice').options.length===2 && !document.getElementById('apply-profile-button').disabled")
                evaluate("""(() => {
                    document.getElementById('bond-width').value='1.5';
                    document.getElementById('apply-style').click();return true;
                })()""")
                poll("window.nivramDocument.getSnapshot().document.style.bondWidth===1.5 && document.getElementById('request-status').textContent==='Ready'")
                evaluate("document.getElementById('apply-profile-button').click(); true")
                poll("window.nivramDocument.getSnapshot().document.style.bondWidth===4.2 && document.getElementById('request-status').textContent==='Ready'")
                if evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document.graph)') != geometry:
                    raise AssertionError('Profile application changed molecular geometry or chemical fields.')
                check('native-profile-save-reopen-apply-preserves-graph')
                from .acceptance_preview import PREVIEW_SNAPSHOT, preview_observation, validate_preview_snapshot
                stage('exact-profile-png-preview-starting')
                evaluate("document.getElementById('preview-profile-button').click(); true")
                preview_result = poll(PREVIEW_SNAPSHOT, timeout=30)
                report['preview_observation'] = preview_observation(preview_result)
                stage('exact-profile-png-preview-validating')
                preview_result = validate_preview_snapshot(preview_result)
                evaluate("document.getElementById('png-preview-dialog').close(); true")
                if evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document.graph)') != geometry:
                    raise AssertionError('Profile preview mutated the molecular graph.')
                check('exact-profile-png-preview-and-pixel-inspection', **preview_result)
                fixtures = profile / 'synthetic fixtures'
                fixtures.mkdir(parents=True, exist_ok=True)
                project_path = fixtures / 'ethanol ü.nivram'
                stage('native-save-as-starting')
                choice = choose(project_path)
                evaluate('window.nivramNative.save(true); true', stage='native-save-as')
                stage('native-save-as-dialog-finishing')
                selection = choice.finish()
                stage('native-save-as-dialog-selected')
                poll("!window.nivramDocument.getSnapshot().dirty && document.getElementById('toast').textContent.startsWith('Project saved and verified:')")
                saved = api._store.open(project_path)
                actual = evaluate('window.nivramDocument.getSnapshot().document')
                if saved['document'] != actual:
                    raise AssertionError('Native Save As readback differs from the active project.')
                check('native-save-as-and-disk-readback', unicode_and_spaces=True, **selection)
                evaluate("""(() => {
                    document.getElementById('bond-width').value='2.5';
                    document.getElementById('apply-style').click();return true;
                })()""")
                poll("window.nivramDocument.getSnapshot().document.style.bondWidth===2.5 && window.nivramDocument.getSnapshot().dirty && document.getElementById('request-status').textContent==='Ready'")
                evaluate('window.nivramNative.save(); true')
                poll('!window.nivramDocument.getSnapshot().dirty')
                updated = api._store.open(project_path)
                if updated['document']['style']['bondWidth'] != 2.5 or updated['document']['graph'] != saved['document']['graph']:
                    raise AssertionError('Native save-to-existing did not preserve the intended content.')
                if api._store.open(project_path.with_suffix('.nivram.bak'))['document'] != saved['document']:
                    raise AssertionError('The durable previous project was not preserved in its backup.')
                check('native-save-existing-preserves-graph')
                choice = choose(project_path)
                evaluate('window.nivramNative.open(); true')
                selection = choice.finish()
                poll("document.getElementById('toast').textContent.startsWith('Opened ') && !window.nivramDocument.getSnapshot().dirty")
                if evaluate('window.nivramDocument.getSnapshot().document') != updated['document']:
                    raise AssertionError('Native Open did not restore the durable project exactly.')
                check('native-open-and-editor-readback', **selection)
                # Inspect the actual UI report and save those exact reviewed bytes.
                evaluate("document.getElementById('help-button').click(); document.getElementById('diagnostics-button').click(); true")
                reviewed = poll("""(() => {
                    const dialog=document.getElementById('diagnostics-dialog');
                    const value=dialog?.querySelector('pre')?.textContent;
                    return value?.startsWith('{') && Array.from(dialog.querySelectorAll('button')).some(b=>b.textContent==='Save reviewed report'&&!b.disabled) ? value : false;
                })()""")
                report_path_local = fixtures / 'reviewed diagnostics ü.json'
                choice = choose(report_path_local)
                evaluate("Array.from(document.querySelectorAll('#diagnostics-dialog button')).find(b=>b.textContent==='Save reviewed report').click(); true")
                selection = choice.finish()
                poll("document.querySelector('#diagnostics-dialog [role=status]').textContent==='The exact reviewed report was saved locally.'")
                if json.loads(report_path_local.read_text(encoding='utf-8')) != json.loads(reviewed):
                    raise AssertionError('Saved diagnostics do not match the exact reviewed report.')
                if any(value in reviewed for value in (str(profile), str(project_path), 'CCO', 'C2H6O')):
                    raise AssertionError('Diagnostics exposed synthetic content or an absolute personal path.')
                evaluate("document.getElementById('diagnostics-dialog').close(); document.getElementById('about-dialog').close(); true")
                check('native-inspect-and-save-diagnostics', **selection)
                # The source image is the actual ethanol PNG above; add only a
                # known synthetic metadata record, without re-encoding its pixels.
                metadata = b'SMILES\0CCO'
                chunk_type = b'tEXt'
                text_chunk = (struct.pack('>I', len(metadata)) + chunk_type + metadata +
                              struct.pack('>I', zlib.crc32(chunk_type + metadata)))
                source_png = exported_png[:-12] + text_chunk + exported_png[-12:]
                image_path = fixtures / 'synthetic ethanol metadata ü.png'
                image_path.write_bytes(source_png)
                pre_image = evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)')
                choice = choose(image_path)
                evaluate("document.getElementById('image-import-button').click(); true")
                selection = choice.finish()
                check('native-png-image-file-selected', **selection)
                poll_png('png-inspection-components-ready', "document.querySelectorAll('#image-import-components input').length===1 && !document.querySelector('#image-import-components input').disabled")
                if evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)') != pre_image:
                    raise AssertionError('PNG inspection changed the active project before acceptance.')
                evaluate("document.querySelector('#image-import-components input').click(); document.getElementById('image-import-preview').click(); true")
                poll_png('png-preview-review-ready', "!document.getElementById('image-import-acknowledge').disabled && document.getElementById('image-import-source').complete")
                if evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)') != pre_image:
                    raise AssertionError('PNG preview changed the active project before acceptance.')
                evaluate("document.getElementById('image-import-acknowledge').click(); document.getElementById('image-import-accept').click(); true")
                poll_png('png-accept-committed', "!document.getElementById('image-import-dialog').open && window.nivramDocument.getSnapshot().dirty && document.getElementById('property-smiles').value==='CCO'")
                source_digest = hashlib.sha256(source_png).hexdigest()
                if (profile / 'import-sources' / (source_digest + '.png')).read_bytes() != source_png or image_path.read_bytes() != source_png:
                    raise AssertionError('The inspected original PNG was not preserved byte-for-byte.')
                evaluate("document.getElementById('undo-button').click(); true")
                poll_png('png-undo-restored', 'JSON.stringify(window.nivramDocument.getSnapshot().document)===' + json.dumps(pre_image))
                check('native-png-metadata-review-accept-preserve-and-one-undo', source_sha256=source_digest, **selection)
                # Embed the exact small exported depiction in a streamed large
                # white canvas. The browser receives bounded derivatives only.
                from .acceptance_png import tiled_source
                large_source, detail_rect = tiled_source(exported_png)
                large_path = fixtures / 'synthetic large metadata.png'
                large_path.write_bytes(large_source)
                pre_large = evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)')
                choice = choose(large_path)
                evaluate("document.getElementById('image-import-button').click(); true")
                large_selection = choice.finish()
                poll_png('large-png-components-ready', "document.querySelectorAll('#image-import-components input').length===1 && !document.querySelector('#image-import-components input').disabled")
                if evaluate("document.getElementById('image-import-source').getAttribute('src')") is not None:
                    raise AssertionError('The large original was assigned to the browser.')
                evaluate("document.querySelector('#image-import-components input').click(); document.getElementById('image-import-preview').click(); true")
                poll_png('large-png-candidate-ready', "!document.getElementById('image-import-preview').disabled && document.getElementById('image-import-candidate').children.length>0")
                evaluate("document.getElementById('image-import-overview-button').click(); true")
                poll_png('large-png-overview-ready', "document.getElementById('image-import-overview').naturalWidth===960 && !document.getElementById('image-import-overview-button').disabled")
                if not evaluate("document.getElementById('image-import-acknowledge').disabled && document.getElementById('image-import-accept').disabled"):
                    raise AssertionError('A navigation overview authorized import.')
                check('native-large-png-overview-navigation-only', source_width=11559, source_height=9999,
                      overview_width=960, overview_height=evaluate("document.getElementById('image-import-overview').naturalHeight"))
                evaluate("""(rect => {
                    for (const [key,value] of Object.entries(rect)) {
                        const field=document.getElementById('image-import-crop-'+key);
                        field.value=String(value);field.dispatchEvent(new Event('input'));
                    }
                    document.getElementById('image-import-crop-button').click();return true;
                })(""" + json.dumps(detail_rect) + ')')
                poll_png('large-png-detail-ready', "!document.getElementById('image-import-acknowledge').disabled && document.getElementById('image-import-source').complete")
                detail_view = evaluate("""(() => {
                    const img=document.getElementById('image-import-source'),wrap=document.getElementById('image-import-source-wrap');
                    return {width:img.naturalWidth,height:img.naturalHeight,cssWidth:img.getBoundingClientRect().width,
                        cssHeight:img.getBoundingClientRect().height,background:getComputedStyle(wrap).backgroundColor,
                        scrollable:getComputedStyle(wrap).overflow==='auto'};
                })()""")
                if (detail_view['width'] != detail_rect['width'] or detail_view['height'] != detail_rect['height'] or
                        detail_view['cssWidth'] != detail_rect['width'] or detail_view['cssHeight'] != detail_rect['height'] or
                        detail_view['background'] != 'rgb(255, 255, 255)' or not detail_view['scrollable']):
                    raise AssertionError('Native detail review changed the source scale or white viewport.')
                if evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)') != pre_large:
                    raise AssertionError('Large PNG review changed the document before acceptance.')
                evaluate("document.getElementById('image-import-acknowledge').click(); document.getElementById('image-import-accept').click(); true")
                poll_png('large-png-accepted', "!document.getElementById('image-import-dialog').open && !!window.nivramDocument.getSnapshot().document.analysis.imageImport.reviewedRegion")
                large_digest = hashlib.sha256(large_source).hexdigest()
                large_provenance = evaluate('window.nivramDocument.getSnapshot().document.analysis.imageImport')
                if (large_provenance['sourceSha256'] != large_digest or large_provenance['reviewedRegion']['rect'] != detail_rect or
                        (profile / 'import-sources' / (large_digest + '.png')).read_bytes() != large_source or large_path.read_bytes() != large_source):
                    raise AssertionError('Large PNG acceptance did not preserve source bytes and exact crop provenance.')
                evaluate("document.getElementById('undo-button').click(); true")
                poll_png('large-png-undo-restored', 'JSON.stringify(window.nivramDocument.getSnapshot().document)===' + json.dumps(pre_large))
                if any((profile / 'image-review-cache').iterdir()):
                    raise AssertionError('Native source snapshot cache was not cleared after the preview worker exited.')
                check('native-large-png-detail-accept-preserve-and-one-undo', source_sha256=large_digest,
                      rect=detail_rect, detail_view=detail_view, reviewed_region=large_provenance['reviewedRegion'], **large_selection)
                evaluate("""(() => {
                    document.getElementById('import-button').click();
                    document.getElementById('structure-input').value='CCCCCCCCCCCCCCCCCCCC';
                    document.getElementById('load-structure').click();return true;
                })()""")
                poll("window.nivramDocument.getSnapshot().document.graph.atoms.length===20 && document.getElementById('request-status').textContent==='Ready'")
                evaluate("""(() => {
                    const width=document.getElementById('export-width');width.value='9999';width.dispatchEvent(new Event('input'));
                    document.getElementById('filename').value='synthetic chain';return true;
                })()""")
                before_download = evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)')
                before_download_id = downloads.snapshot()['last_id']
                png_path = fixtures / 'chain 9999 ü.png'
                choice = choose(png_path, timeout=35)
                evaluate("document.getElementById('export-button').click(); true")
                selection = choice.finish()
                download_receipt = downloads.wait_for_download(after_id=before_download_id, timeout_seconds=30)
                if download_receipt['state'] != 'Completed' or not download_receipt['settled']:
                    raise AssertionError('The native PNG download did not confirm completion: ' + str(download_receipt))
                from PIL import Image
                deadline = time.monotonic() + 20
                decoded = None
                while time.monotonic() < deadline:
                    try:
                        with Image.open(png_path) as saved_image:
                            saved_image.load()
                            decoded = saved_image.copy()
                        break
                    except (OSError, ValueError):
                        time.sleep(.1)
                if decoded is None or decoded.width != 9999:
                    raise AssertionError('The real native PNG save did not produce the requested 9999-pixel file.')
                try:
                    if 'A' in decoded.getbands() and decoded.getchannel('A').getextrema() != (255,255):
                        raise AssertionError('The saved native PNG contains transparent pixels.')
                    rgb = decoded.convert('RGB')
                    try:
                        w,h = rgb.size
                        for rectangle in ((0,0,w,200),(0,h-200,w,h),(0,0,200,h),(w-200,0,w,h)):
                            with rgb.crop(rectangle) as border:
                                if border.getextrema() != ((255,255),(255,255),(255,255)):
                                    raise AssertionError('A saved native PNG margin is not fully opaque white.')
                        if rgb.getextrema() == ((255,255),(255,255),(255,255)):
                            raise AssertionError('The saved native PNG contains no drawing.')
                    finally:
                        rgb.close()
                    dimensions = {'width': decoded.width, 'height': decoded.height}
                finally:
                    decoded.close()
                if evaluate('JSON.stringify(window.nivramDocument.getSnapshot().document)') != before_download:
                    raise AssertionError('The real native PNG download changed the document.')
                check('native-9999-png-save-decoded-margins-opacity-and-nonmutation',
                      **dimensions, margin=200, download=download_receipt,
                      sha256=hashlib.sha256(png_path.read_bytes()).hexdigest(), **selection)
                report['status'] = 'passed'
            except Exception as error:
                record_failure(report, error)
                if hasattr(error, 'dialog_result'):
                    report['native_dialog_failure'] = error.dialog_result
                # Persist the original failure before any native cleanup call.
                persist_cleanup_checkpoint(report_path, report)
            finally:
                stage('closing-native-dialog-fixtures', best_effort=True)
                report['dialog_fixture_shutdown'] = close_dialog_fixtures(choices)
                if any(not row.get('shutdownConfirmed') for row in report['dialog_fixture_shutdown']):
                    report['status'] = 'failed'
                    report['errors'].append('Owned native dialog fixture shutdown remains unconfirmed.')
                persist_cleanup_checkpoint(report_path, report)
                report['network_policy'] = policy.snapshot()
                report['downloads_before_close'] = downloads.snapshot()
                report['elapsed_before_close_seconds'] = round(time.monotonic()-start, 3)
                if production_close:
                    report['evaluation_shutdown'] = evaluator.close()
                    if not window.events.closed.is_set():
                        report.setdefault(close_facts_key, {})['fallbackDestroy'] = True
                        report['status'] = 'failed'
                        report['errors'].append('Production close did not finish; synthetic fallback teardown required.')
                        window.events.closing -= close_coordinator.request_close
                        window.destroy()
                else:
                    if window.events._pywebviewready.is_set():
                        try:
                            evaluate('window.nivramDocument?.cancelPending(); true', timeout=3, stage='cancel-pending-before-close')
                        except Exception as error:
                            report['cancel_pending_error'] = type(error).__name__
                            report['status'] = 'failed'
                            report['errors'].append('Pending native UI operations could not be confirmed cancelled.')
                    report['evaluation_shutdown'] = evaluator.close()
                    if not report['evaluation_shutdown']['workerExited']:
                        report['status'] = 'failed'
                        report['errors'].append('The native evaluation worker remains unresolved.')
                    stage('closing-native-downloads', best_effort=True)
                    try:
                        report['download_shutdown'] = downloads.shutdown(timeout_seconds=5)
                        if not report['download_shutdown']['shutdown_confirmed']:
                            report['status'] = 'failed'
                            report['errors'].append('Owned download shutdown remains unconfirmed.')
                    except Exception as error:
                        report['download_shutdown_error'] = type(error).__name__
                        report['status'] = 'failed'
                        report['errors'].append('Owned download shutdown failed; see download lifecycle metadata.')
                    stage('closing-managed-webview-session', best_effort=True)
                    try:
                        report['cleanup']['managed_session'] = session.shutdown(timeout_seconds=20)
                    except Exception as error:
                        report['cleanup']['managed_shutdown_error'] = type(error).__name__
                        report['status'] = 'failed'
                    observe_webview_parent_phase(report, webview_capture, 'shutdown_return')
                    stage('destroying-native-window', best_effort=True)
                    window.destroy()
                verify_done.set()

        webview.start(verify, gui='edgechromium', debug=False, private_mode=True,
                      storage_path=str(session.storage_path))
        if production_close:
            finished = verify_done.wait(15)
            report.setdefault(close_facts_key, {})['verifyThreadFinished'] = finished
            if not finished:
                raise TimeoutError('The close scenario verification thread did not settle after native exit.')
            report['native_lifetime'] = lifetime.finish(timeout_seconds=3)
            report['finalization'] = finalization.snapshot()
            report[close_facts_key]['batchManagerClosed'] = api._batch._closed is True
            report['download_shutdown'] = downloads.snapshot()
            report['cleanup']['managed_session'] = session.snapshot()
            report['production_close'] = {
                'startup_status': report['status'], 'network_policy': policy.snapshot(),
                'native_lifetime': report['native_lifetime'], 'finalization': report['finalization'],
                'webview_cleanup': session.snapshot(), 'download_shutdown': downloads.snapshot(),
                'owned_processes': process_job.snapshot()}
            if scenario == 'batch-close':
                from .acceptance_batch_close import production_failures
            else:
                from .acceptance_uncertainty_close import production_failures
            failures = production_failures(report)
            if failures:
                report['production_close_failures'] = failures
            # Keep the failure observed at this boundary; later diagnostics
            # must not recompute it into success or skip existing observations.
        observe_webview_parent_phase(report, webview_capture, 'window_loop_return')
        check('native-window-loop-returned')
        process = runtime['process']
        if process is not None:
            try:
                report['cleanup']['main_browser_handle_exited'] = bool(process.WaitForExit(5000))
                observe_webview_parent_handle(report, webview_capture, 'read',
                                              report['cleanup']['main_browser_handle_exited'])
            except Exception as error:
                report['cleanup']['main_browser_wait_error'] = type(error).__name__
                observe_webview_parent_handle(report, webview_capture, 'failed')
            finally:
                observe_webview_parent_phase(report, webview_capture, 'handle_wait_return')
                process.Dispose()
        else:
            observe_webview_parent_handle(report, webview_capture, 'unavailable')
            observe_webview_parent_phase(report, webview_capture, 'handle_wait_return')
        report['cleanup'].update(
            collection_exit_event_observed=exited.is_set(),
            vendor_warning_codes=warning_codes,
            profile_directory_retained=session.storage_path.exists(),
            deletion_attempted_by_test=bool(session.snapshot()['deletion_attempts']),
        )
        if report['cleanup']['profile_directory_retained']:
            report['limitations'].append('WebView2 profile residue remained; complete runtime-cache removal is not certified.')
        if production_close:
            if failures:
                raise AssertionError('Production close finalization was not confirmed.')
            check('native-production-close-finalization', stages=8, normalExit=True, watchdogRelease=False, ownedProcessesExited=True)
        if report['cleanup'].get('collection_exit_kind') == 'Failed':
            report['status'] = 'failed'
            report['errors'].append('WebView2 reported a failed browser-process exit during shutdown; functional checks alone do not pass lifecycle acceptance.')
    except Exception as error:
        record_failure(report, error)
        persist_cleanup_checkpoint(report_path, report)
    finally:
        if evaluator is not None:
            report['evaluation_shutdown'] = evaluator.close()
            if not report['evaluation_shutdown']['workerExited']:
                report['status'] = 'failed'
        if production_close and finalization is not None:
            if lifetime is not None:
                report['native_lifetime'] = lifetime.finish(timeout_seconds=3)
            if not finalization.snapshot()['started']:
                finalization.run(timeout_seconds=30)
            report['finalization'] = finalization.snapshot()
            report['image_preview_shutdown_confirmed'] = report['finalization']['stages'].get('images', {}).get('result') is True
            report['downloads_final'] = downloads.snapshot()
            report['worker_shutdown'] = server.worker_shutdown_receipt
        else:
            if api is not None:
                report['image_preview_shutdown_confirmed'] = api._images.close()
                if not report['image_preview_shutdown_confirmed']:
                    report['status'] = 'failed'
                    report['errors'].append('Owned PNG preview worker shutdown remains unconfirmed.')
                api._batch.close()
            if downloads is not None:
                report['downloads_final'] = downloads.snapshot()
            if server is not None:
                server.shutdown()
                server.server_close()
                report['worker_shutdown'] = server.worker_shutdown_receipt
                if not server.worker_shutdown_receipt.get('ownedProcessesExited'):
                    report['status'] = 'failed'
                    report['errors'].append('Owned scientific worker shutdown remains unconfirmed.')
            if thread is not None:
                thread.join(3)
        instance.close()
        logging.getLogger('pywebview').removeHandler(warnings)
        report['elapsed_seconds'] = round(time.monotonic()-start, 3)
        finalize_acceptance_report(report, policy=policy)
        write_report(report_path, report)
    return 0 if report['status'] == 'passed' else 1


def acceptance_child_command(report_path, profile, scenario='full'):
    if scenario not in SCENARIO_CHECKS:
        raise ValueError('Unsupported native acceptance scenario.')
    arguments = ['--report', str(report_path), '--child', '--profile', str(profile)]
    if scenario != 'full':
        arguments += ['--scenario', scenario]
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--acceptance-report', str(report_path), '--child', '--profile', str(profile),
                *(['--scenario', scenario] if scenario != 'full' else [])]
    from .native_dialog_fixture import _source_interpreter, _source_packages
    # Supervise the actual interpreter, not the Windows venv redirector. The
    # fixed bootstrap preserves this environment's packages without user-site
    # or PYTHONPATH loading and handles paths as separate arguments.
    bootstrap = ("import runpy,sys;sys.path[:0]=sys.argv[1:3];"
                 "sys.argv=['nivram.acceptance',*sys.argv[3:]];"
                 "runpy.run_module('nivram.acceptance',run_name='__main__')")
    return [_source_interpreter(), '-I', '-S', '-c', bootstrap,
            str(ROOT), str(_source_packages()), *arguments]


def acceptance_child_environment():
    if getattr(sys, 'frozen', False):
        return None
    from .native_dialog_fixture import _source_packages
    environment = dict(os.environ)
    environment.pop('PYTHONHOME', None)
    # The isolated direct interpreter ignores PYTHONPATH itself. Its existing
    # scientific worker entrypoints use sys.executable, so give those direct
    # children only our fixed source/pinned-package path, never user PYTHONPATH.
    environment['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(_source_packages())))
    environment['PYTHONNOUSERSITE'] = '1'
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--scenario', choices=tuple(SCENARIO_CHECKS), default='full',
                        help='Run the full sequence or a fixed native drawing/profile/PNG or batch/PNG check.')
    argv = sys.argv[1:]
    if '--acceptance-report' in argv:
        argv[argv.index('--acceptance-report')] = '--report'
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report_path = (args.report or ROOT / 'verification' / f'native-visible-{stamp}.json').resolve()
    profile = (args.profile or report_path.parent / f'{report_path.stem}-profile').resolve()
    if args.child:
        return run_child(report_path, profile, args.scenario)
    if report_path.exists() or profile.exists():
        parser.error('Use fresh report and profile paths; prior evidence is never overwritten.')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = report_path.with_suffix('.log')
    command = acceptance_child_command(report_path, profile, args.scenario)
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    started = time.monotonic()
    with log_path.open('x', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log,
                                   env=acceptance_child_environment(), **options)
        forced = False
        try:
            return_code = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            # Terminate only the child created here. Never enumerate or kill other
            # applications or unrelated WebView2 processes.
            forced = True
            process.kill()
            return_code = process.wait(timeout=10)
    try:
        def reject_constant(_value):
            raise ValueError('Non-finite native report value.')
        report = json.loads(report_path.read_text(encoding='utf-8'), parse_constant=reject_constant)
        if not isinstance(report, dict):
            raise ValueError('The native child report is not an object.')
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
        report = {'status': 'failed', 'errors': ['The native child completion report was missing or invalid.'],
                  'report_read_error_kind': type(error).__name__}
    report['process'] = {'exit_code': return_code, 'forced_termination': forced,
                         'elapsed_seconds': round(time.monotonic()-started, 3)}
    if report.get('scenario', 'full') != args.scenario:
        report['status'] = 'failed'
        report.setdefault('errors', []).append('The native child scenario differs from the selected fixed sequence.')
    if forced or return_code:
        report['status'] = 'failed'
    finalize_acceptance_report(report)
    write_report(report_path, report)
    if sys.stdout is not None:
        print(json.dumps({'status': report['status'], 'report': str(report_path), 'process': report['process']}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
