"""Read-only interpreter gate for the current Windows desktop source candidate."""
import platform
import struct
import sys
import sysconfig


def require_environment():
    if (sys.platform != 'win32' or sys.implementation.name != 'cpython'
            or sys.version_info[:3] != (3, 12, 14)
            or sysconfig.get_platform() != 'win-amd64'
            or platform.machine().lower() not in ('amd64', 'x86_64')
            or struct.calcsize('P') != 8
            or sysconfig.get_config_var('Py_GIL_DISABLED')):
        raise RuntimeError('Requires standard CPython 3.12.14 Windows x64 (not ARM64 or free-threaded).')
    if sys.getwindowsversion().build < 22000:
        raise RuntimeError('Requires Windows 11 build 22000 or newer.')


def main():
    try:
        require_environment()
    except RuntimeError as error:
        print(error)
        return 1
    print('Interpreter accepted: CPython 3.12.14 Windows x64.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
