"""Metadata-only local diagnostics: never inspect projects, molecules or paths."""
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys
import sysconfig
from . import __version__


def sanitize_build_info(stored):
    if not isinstance(stored, dict):
        raise ValueError('Invalid build metadata.')
    safe = {'status': 'build metadata present'}
    digest = stored.get('sourceSha256')
    if isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest):
        safe['sourceSha256'] = digest
    version = stored.get('version')
    if isinstance(version, str) and re.fullmatch('[A-Za-z0-9.+-]{1,80}', version):
        safe['version'] = version
    if isinstance(stored.get('builtAt'), str):
        try:
            safe['builtAt'] = datetime.fromisoformat(stored['builtAt']).isoformat()
        except ValueError:
            pass
    versions = {}
    if isinstance(stored.get('versions'), dict):
        for key, value in stored['versions'].items():
            if isinstance(value, str) and re.fullmatch('[A-Za-z0-9.+-]{1,80}', value):
                versions[key] = value
    return safe, versions


def sanitize_permissions(value):
    """Allow only bounded cached permission observations, never URIs or objects."""
    from .webview_downloads import DIAGNOSTIC_LIMIT, PERMISSION_TRACE_LIMIT, PERMISSION_STATUSES, PERMISSION_STATES
    status = value.get('status')
    result = {'status': status if isinstance(status, str) and status in PERMISSION_STATUSES else 'unrecognized', 'events': []}
    for key in ('seen', 'read_failures', 'dropped'):
        if type(value.get(key)) is int and 0 <= value[key] <= DIAGNOSTIC_LIMIT:
            result[key] = value[key]
    events = value.get('events')
    required = ('sequence', 'download_sequence', 'kind', 'is_user_initiated', 'state', 'origin_matches')
    if isinstance(events, list):
        for row in events[-PERMISSION_TRACE_LIMIT:]:
            if not isinstance(row, dict) or any(key not in row for key in required): continue
            if (type(row['sequence']) is not int or not 1 <= row['sequence'] <= DIAGNOSTIC_LIMIT
                    or type(row['download_sequence']) is not int or not 0 <= row['download_sequence'] <= DIAGNOSTIC_LIMIT
                    or type(row['kind']) is not int or not -1 <= row['kind'] <= 13
                    or type(row['is_user_initiated']) is not bool or type(row['origin_matches']) is not bool
                    or not isinstance(row['state'], str) or row['state'] not in PERMISSION_STATES):
                continue
            result['events'].append({key: row[key] for key in required})
    return result


def sanitize_downloads(value):
    """Strict allowlist over cached data; this function never inspects a native object."""
    from .webview_downloads import (DIAGNOSTIC_LIMIT, DIAGNOSTIC_TRACE_LIMIT, DIAGNOSTIC_EVENTS, DIAGNOSTIC_FORMATS,
        DIAGNOSTIC_FAILURES, DIAGNOSTIC_REASONS, DIAGNOSTIC_COUNTERS)
    if not isinstance(value, dict):
        return {'status': 'unconfigured'}
    statuses = ('attached', 'armed', 'installed', 'failed', 'closed', 'shutdown_unconfirmed')
    states = ('InProgress', 'Completed', 'Interrupted', 'Unknown')
    result = {'status': value.get('status') if value.get('status') in statuses else 'unrecognized'}
    for key in DIAGNOSTIC_COUNTERS:
        if type(value.get(key)) is int and 0 <= value[key] <= DIAGNOSTIC_LIMIT:
            result[key] = value[key]
    for key in ('shutdown_confirmed',):
        if type(value.get(key)) is bool: result[key] = value[key]
    if value.get('last_state') in states or value.get('last_state') is None:
        result['last_state'] = value.get('last_state')
    code = value.get('failure_code')
    if code is None or isinstance(code, str) and code in DIAGNOSTIC_FAILURES:
        result['failure_code'] = code
    trace = value.get('trace'); result['trace'] = []
    if isinstance(trace, list):
        counters = ('sequence', 'download_id', 'active'); flags = ('args_pending', 'dialog_pending', 'deferral_pending')
        required = ('event', 'format', 'reason', 'state', *counters, *flags)
        for row in trace[-DIAGNOSTIC_TRACE_LIMIT:]:
            if not isinstance(row, dict) or any(key not in row for key in required): continue
            event, kind, reason = row.get('event'), row.get('format'), row.get('reason')
            if (not isinstance(event, str) or event not in DIAGNOSTIC_EVENTS or
                    not isinstance(kind, str) or kind not in DIAGNOSTIC_FORMATS or
                    reason is not None and (not isinstance(reason, str) or reason not in DIAGNOSTIC_REASONS)):
                continue
            if (any(type(row.get(k)) is not int or not 0 <= row[k] <= DIAGNOSTIC_LIMIT for k in counters)
                    or any(type(row.get(k)) is not bool for k in flags)
                    or row.get('state') not in (*states, None)):
                continue
            result['trace'].append({k: row[k] for k in required})
    if isinstance(value.get('permission_observer'), dict):
        result['permission_observer'] = sanitize_permissions(value['permission_observer'])
    return result


def sanitize_reference_exports(value):
    """Copy a bounded frontend activation snapshot; never retain event objects."""
    limit = 1_000_000_000
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1
            or any(type(value.get(key)) is not int or not 0 <= value[key] <= limit for key in ('seen', 'dropped'))
            or not isinstance(value.get('events'), list)):
        return {'status': 'unavailable', 'events': []}
    result = {'status': 'observing', 'schema': 1, 'seen': value['seen'], 'dropped': value['dropped'], 'events': []}
    required = ('sequence', 'format', 'trusted', 'handler_active', 'dispatch_active', 'dispatch_observed')
    for row in value['events'][-4:]:
        if not isinstance(row, dict) or any(key not in row for key in required):
            continue
        if (type(row['sequence']) is not int or not 1 <= row['sequence'] <= limit
                or type(row['format']) is not str or row['format'] not in ('json', 'csv', 'png')
                or any(row[key] is not None and type(row[key]) is not bool
                       for key in ('trusted', 'handler_active', 'dispatch_active'))
                or type(row['dispatch_observed']) is not bool
                or not row['dispatch_observed'] and row['dispatch_active'] is not None):
            continue
        result['events'].append({key: row[key] for key in required})
    return result


def diagnostics_report(*, cleanup=None, naming=None, downloads=None, reference_exports=None):
    versions = {}
    for name in ('rdkit', 'numpy', 'pillow', 'pywebview', 'pythonnet', 'nispo'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'metadata unavailable in this package'
    build = {'status': 'development source; no build-bound identity'}
    build_path = Path(__file__).resolve().parent / 'data' / 'build-info.json'
    if build_path.is_file():
        try:
            if build_path.stat().st_size <= 32_768:
                stored = json.loads(build_path.read_text(encoding='utf-8'))
                # Only an explicit allowlist can appear in the default report.
                build, bundled_versions = sanitize_build_info(stored)
                versions.update({name: version for name, version in bundled_versions.items() if name in versions})
        except (OSError, ValueError, TypeError):
            build = {'status': 'build metadata unreadable'}
    return {'schema': 'nivram-diagnostics/1', 'createdAt': datetime.now(timezone.utc).isoformat(),
            'applicationVersion': __version__, 'build': build,
            'runtime': {'python': platform.python_version(), 'target': sysconfig.get_platform(),
                        'operatingSystem': sys.platform, 'windowsVersion': platform.version()},
            'versions': versions,
            'configuration': {'externalProviders': 'per-request consent', 'telemetry': 'none',
                              'projectPersistence': 'native-local',
                              'cleanupBackend': 'RDKit; Marvin JS parity unverified'},
            'namingVerifier': naming or {'status': 'unconfigured'},
            'webviewSession': cleanup,
            'webviewDownloads': sanitize_downloads(downloads),
            'referenceExports': sanitize_reference_exports(reference_exports),
            'privacy': {'molecularContentIncluded': False, 'personalPathsIncluded': False,
                        'credentialsIncluded': False, 'automaticUpload': False},
            'limitations': ['Diagnostics describe configuration and observed lifecycle state; they are not a complete security or scientific validation report.']}
