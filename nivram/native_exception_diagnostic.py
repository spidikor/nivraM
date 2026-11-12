"""Fixed exception categories and numeric HRESULTs; no messages or raw names."""
MANAGED_CATEGORIES = {
    'System.Windows.Automation.ElementNotEnabledException': 'managed-element-not-enabled',
    'System.Windows.Automation.ElementNotAvailableException': 'managed-element-not-available',
    'System.InvalidOperationException': 'managed-invalid-operation',
    'System.Runtime.InteropServices.COMException': 'managed-com',
    'System.TimeoutException': 'managed-timeout',
    'System.NotSupportedException': 'managed-not-supported',
    'System.UnauthorizedAccessException': 'managed-access-denied',
    'System.Reflection.TargetInvocationException': 'managed-invocation-wrapper',
}
PYTHON_CATEGORIES = {
    AttributeError: 'python-attribute', RuntimeError: 'python-runtime',
    NotImplementedError: 'python-not-implemented', TypeError: 'python-type',
    ValueError: 'python-value', TimeoutError: 'python-timeout', OSError: 'python-os',
}
CATEGORIES = frozenset(MANAGED_CATEGORIES.values()) | frozenset(PYTHON_CATEGORIES.values()) | {
    'managed-other', 'python-other', 'metadata-unavailable',
}


def validate_exception_detail(value):
    if (type(value) is not dict or set(value) != {'category', 'hresult'}
            or type(value['category']) is not str or value['category'] not in CATEGORIES):
        return None
    number = value['hresult']
    if number is not None and (type(number) is not int or not -2**31 <= number < 2**31):
        return None
    if not value['category'].startswith('managed-') and number is not None:
        return None
    return dict(value)


def capture_exception_detail(error):
    category = PYTHON_CATEGORIES.get(type(error))
    if category is not None:
        return {'category': category, 'hresult': None}
    try:
        # UI Automation has already loaded System on the native failure path.
        # A Python-only caller does not initialize CLR merely for diagnostics.
        from System import Exception as ManagedException
    except ImportError:
        return {'category': 'python-other', 'hresult': None}
    except Exception:
        return {'category': 'metadata-unavailable', 'hresult': None}
    try:
        if not isinstance(error, ManagedException):
            return {'category': 'python-other', 'hresult': None}
        category = MANAGED_CATEGORIES.get(str(error.GetType().FullName), 'managed-other')
        raw = error.HResult
        number = int(raw) if not isinstance(raw, bool) else None
        if number is not None and not -2**31 <= number < 2**31:
            number = None
        return {'category': category, 'hresult': number}
    except Exception:
        return {'category': 'metadata-unavailable', 'hresult': None}
