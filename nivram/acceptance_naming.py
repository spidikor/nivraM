"""Observe naming UI completion without loading the application or chemistry.

These helpers decide when to stop waiting and retain bounded diagnostics. They
do not certify a generated name; acceptance.py keeps the ethanol success gate.
"""
from __future__ import annotations

import re
import time


NAMING_PANEL_SNAPSHOT = """(() => {
    const result = document.getElementById('name-result');
    const details = document.getElementById('name-details');
    const cancel = document.getElementById('cancel-name');
    if (!result || !details || !cancel) return null;
    return {name:result.textContent.slice(0,1024),
        details:details.textContent.slice(0,8192), cancelHidden:cancel.hidden,
        nameTruncated:result.textContent.length > 1024,
        detailsTruncated:details.textContent.length > 8192};
})()"""

_PENDING_NAMES = frozenset(('', 'Calculating locally\u2026', 'Calculating locally.',
    'Calculate a name for the current structure.'))
# This acceptance uses public ethanol, but a transport error can still contain
# an OS path, URL, or credential assignment. Omit that whole field from reports.
_SENSITIVE_TEXT = re.compile(
    r"(?:\b[a-z]:[\\/]|\\\\|\b[a-z][a-z0-9+.-]*://|"
    r"(?:^|[\s'\"(])/(?:[^\s]+)|"
    r"\b(?:token|authorization|secret|password)\s*[:=])", re.IGNORECASE)


class NamingPanelTimeout(TimeoutError):
    def __init__(self, snapshot):
        super().__init__('The naming panel did not reach a terminal state within its acceptance deadline.')
        self.snapshot = snapshot


def is_naming_terminal(snapshot):
    """The frontend hides Cancel in finally, including its error branches."""
    return (isinstance(snapshot, dict) and snapshot.get('cancelHidden') is True
            and isinstance(snapshot.get('name'), str)
            and snapshot['name'] not in _PENDING_NAMES)


def wait_for_naming(evaluate, *, timeout=35, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    last = None
    while clock() < deadline:
        last = evaluate(NAMING_PANEL_SNAPSHOT)
        if is_naming_terminal(last):
            return last
        sleep(min(.1, max(0, deadline - clock())))
    raise NamingPanelTimeout(last)


def naming_diagnostics(snapshot, *, terminal):
    """Keep exact safe visible text, bounded and without other page/job data."""
    source = snapshot if isinstance(snapshot, dict) else {}
    result = {'terminal': bool(terminal),
              'cancelHidden': source.get('cancelHidden') if type(source.get('cancelHidden')) is bool else None}
    for field, limit in (('name', 1024), ('details', 8192)):
        value = source.get(field)
        value = value if isinstance(value, str) else ''
        # Inspect before clipping so a long prefix cannot conceal sensitive text.
        redacted = bool(_SENSITIVE_TEXT.search(value))
        result[field] = '[Sensitive diagnostic text omitted.]' if redacted else value[:limit]
        result[field + 'Redacted'] = redacted
        result[field + 'Truncated'] = len(value) > limit or source.get(field + 'Truncated') is True
    name = source.get('name')
    if not terminal:
        result['kind'] = 'poll-timeout'
    elif name == 'Local naming timed out. Try again or continue editing.':
        result['kind'] = 'client-timeout'
    elif name == 'Cancelled':
        result['kind'] = 'cancelled'
    elif name == 'No accepted name':
        result['kind'] = 'no-accepted-name'
    elif not source.get('details'):
        result['kind'] = 'terminal-error'
    else:
        result['kind'] = 'response'
    return result


def require_naming_response_details(snapshot):
    """A terminal message with no response details is never a naming pass."""
    if not isinstance(snapshot.get('details'), str) or not snapshot['details']:
        diagnostic = naming_diagnostics(snapshot, terminal=True)
        raise AssertionError('The naming panel ended without response details: ' + diagnostic['name'])


def naming_verification(snapshot):
    """Separate the ethanol UI check from its displayed graph-match status.

    The backend's round_trip_matched status renders as 'round trip matched'.
    Neither flag establishes a Preferred IUPAC Name or wider engine accuracy.
    """
    source = snapshot if isinstance(snapshot, dict) else {}
    details = source.get('details')
    ui_verified = (is_naming_terminal(source) and source.get('name') == 'ethanol'
                   and isinstance(details, str) and bool(details))
    status, separator, _ = details.partition(' \u00b7 ') if isinstance(details, str) else ('', '', '')
    return {'naming_ui_verified': ui_verified,
            'naming_roundtrip_verified': bool(ui_verified and separator and status == 'round trip matched')}
