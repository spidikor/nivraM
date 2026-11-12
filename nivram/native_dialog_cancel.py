"""Intentional Cancel on an explicitly identified synthetic OPEN/FOLDER/SAVE chooser.

No path is typed and no chooser return is substituted. This worker receipt
proves one posted IDCANCEL and observed closure; the acceptance scenario must
independently verify the original native API return and production batch state.
"""
from __future__ import annotations

from . import native_dialog_worker as worker
from .native_dialog_progress import ProgressEmitter


def cancel_fixture(request, backend, *, clock, sleep, progress=None):
    if progress is not None and type(progress) is not ProgressEmitter:
        raise ValueError('Invalid native progress emitter.')
    request = worker.validate_request(request, expected_parent=request['parentPid'])
    if request['schemaVersion'] != 3 or request['dialogAction'] != 'cancel':
        raise ValueError('An explicit intentional cancellation request is required.')
    mode = request['dialogMode']
    result = {
        'schemaVersion': 2, 'nonce': request['nonce'], 'selected': False,
        'dialog_action': 'cancel', 'cancelled': False, 'cancel_posted': False,
        'reason': 'supported_folder_control_not_found' if mode == 'folder' else 'supported_filename_control_not_found',
        'scope': worker.SCOPE, 'stage': 'validate', 'control_count': 0, 'dialog_count': 0,
        'selection_mode': mode, 'selection_count': 2 if mode == 'open-multiple' else 1,
        'filename_field_verified': False, 'dialog_close_confirmed': False,
        'label_counts': {'filename': 0, 'folder': 0, 'other': 0}, 'idok_label_kind': 'unavailable',
    }
    emitted = set()

    def checkpoint(event):
        if progress is not None and event not in emitted:
            emitted.add(event)
            ProgressEmitter.emit(progress, event)

    # Same parent cap and result-emission reserve as the selection fixture.
    deadline = clock() + request['timeout'] - min(1, request['timeout'] * .1)
    try:
        while clock() < deadline:
            if not worker._owner_valid(backend, request):
                result['reason'] = 'parent_or_owner_changed'
                return result
            result['stage'] = 'find-dialog'
            checkpoint('find_dialog_enter')
            dialogs = [hwnd for hwnd in backend.windows() if worker._owned_dialog(backend, hwnd, request)]
            result['dialog_count'] = len(dialogs)
            if len(dialogs) > 1:
                result['reason'] = 'ambiguous_owned_dialog'
                return result
            if dialogs:
                dialog = dialogs[0]
                result['stage'] = 'identify-field'
                checkpoint('identify_field_enter')
                controls = backend.windows(dialog)
                result['control_count'] = len(controls)
                result['label_counts'] = {'filename': 0, 'folder': 0, 'other': 0}
                result['idok_label_kind'] = worker._idok_label_kind(backend, controls, dialog, request)
                fields = []
                for field in controls:
                    if not worker._owned_field(backend, field, dialog, request):
                        continue
                    label = (backend.label(field, diagnostic_context='edit') if type(backend) is worker.WindowsBackend
                             else backend.label(field))
                    category = ('filename' if label in worker._mode_labels('open-multiple') else
                                'folder' if label in worker._mode_labels('folder') else 'other')
                    result['label_counts'][category] += 1
                    if label in worker._mode_labels(mode):
                        fields.append(field)
                if len(fields) > 1:
                    result['reason'] = 'ambiguous_filename_field'
                    return result
                expected_button = {'folder':'folder', 'open-multiple':'open', 'save':'save'}[mode]
                # A filename field alone cannot distinguish OPEN from SAVE;
                # require the request's exact button category before touching it.
                if len(fields) == 1 and result['idok_label_kind'] == expected_button:
                    field = fields[0]
                    if not worker._mode_field_valid(backend, field, dialog, request, mode):
                        result['reason'] = 'parent_or_owner_changed'
                        return result
                    if worker._idok_label_kind(backend, controls, dialog, request) != expected_button:
                        result['reason'] = 'cancel_route_changed'
                        return result
                    # No accessibility call after the last identity/deadline
                    # check and before the single owned-window action.
                    checkpoint('cancel_enter')
                    worker.validate_request(request, expected_parent=request['parentPid'])
                    if (not worker._owned_field(backend, field, dialog, request)
                            or not backend.enabled(dialog) or clock() >= deadline):
                        result['reason'] = 'parent_or_owner_changed'
                        return result
                    if not backend.submit(dialog, 2):
                        result['reason'] = 'cancellation_post_failed'
                        return result
                    result['cancel_posted'] = True
                    checkpoint('cancel_returned')
                    result['stage'] = 'confirm-close'
                    checkpoint('confirm_close_enter')
                    closed, reason = worker._confirm_dialog_closed(request, backend, dialog, deadline,
                                                                   clock=clock, sleep=sleep)
                    # The inherited observer checks the clock at loop entry;
                    # identity/Win32 reads may cross the remaining budget.
                    # Capture the observation boundary before receipt telemetry.
                    if closed and clock() >= deadline:
                        closed, reason = False, 'dialog_close_unconfirmed'
                    checkpoint('confirm_close_returned')
                    result.update(cancelled=closed, dialog_close_confirmed=closed,
                                  reason='intentional_cancel' if closed else reason)
                    return result
            sleep(min(.05, max(0, deadline - clock())))
    except Exception:
        result['reason'] = 'native_operation_failed'
    # No unqualified or repeated cancel in finally; failure is retained and the
    # supervisor remains responsible for its exact owned process tree.
    return result
