"""Resolve installed system Arial without project paths or font substitution."""
from __future__ import annotations
import ctypes
from io import BytesIO
from pathlib import Path
import stat
import sys
from PIL import ImageFont

_FONT_ERROR = ('Accurate atom labels require the installed Windows Arial Regular font. '
               'The drawing could not be rendered; repair the system font and try again.')
_MAX_FONT_BYTES = 16 * 1024 * 1024
_LINUX_FONT_ERROR = ('Accurate atom labels require locally installed Arial Regular. '
                     'The Linux system font is missing, untrusted or invalid; '
                     'the drawing could not be rendered. No substitute font was selected.')
_LINUX_FONT_ROOTS = (Path('/usr/share/fonts'), Path('/usr/local/share/fonts'))
_LINUX_ARIAL_PATHS = (
    Path('/usr/share/fonts/truetype/msttcorefonts/Arial.ttf'),
    Path('/usr/share/fonts/truetype/msttcorefonts/arial.ttf'),
    Path('/usr/local/share/fonts/Arial.ttf'),
    Path('/usr/local/share/fonts/arial.ttf'),
)


def _windows_directory() -> Path:
    if sys.platform != 'win32':
        raise ValueError('Accurate atom labels currently require Windows and its Arial Regular font.')
    try:
        # Load only the Windows system DLL, independently of CWD and environment.
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
        lookup = kernel.GetWindowsDirectoryW
        lookup.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        lookup.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        count = lookup(buffer, len(buffer))
        if not 0 < count < len(buffer):
            raise ValueError(_FONT_ERROR)
        directory = Path(buffer.value)
        if not directory.is_absolute():
            raise ValueError(_FONT_ERROR)
        return directory
    except (OSError, AttributeError):
        raise ValueError(_FONT_ERROR) from None


def _validated_arial(path: Path, error: str) -> str:
    """Validate bounded bytes of the selected face, never Pillow search paths."""
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_FONT_BYTES:
            raise ValueError(error)
        # A path passed to truetype() may trigger Pillow's environment-based
        # fallback search. Validate only these bounded bytes of this exact file.
        with path.open('rb') as source:
            data = source.read(_MAX_FONT_BYTES + 1)
        if not 0 < len(data) <= _MAX_FONT_BYTES:
            raise ValueError(error)
        font = ImageFont.truetype(BytesIO(data), size=36)
        if tuple(part.casefold() for part in font.getname()) != ('arial', 'regular'):
            raise ValueError(error)
    except (OSError, ImportError, ValueError):
        raise ValueError(error) from None
    return str(path)


def _linux_system_path(path: Path) -> Path:
    """Permit one system-owned file alias inside the fixed system font roots.

    Use the filesystem root's owner, which also works in a user namespace where
    host root is mapped to another UID. A system administrator remains trusted,
    as with the existing Windows Fonts input; this is not a font authenticity
    or licensing certificate.
    """
    owner = Path('/').stat().st_uid

    def parent_directories(name):
        if (not name.is_absolute() or '..' in name.parts
                or not any(name.is_relative_to(root) for root in _LINUX_FONT_ROOTS)):
            raise ValueError(_LINUX_FONT_ERROR)
        # Inspect lexical components from the root before following any alias.
        # A missing directory is distinct from a present, redirected directory.
        for component in reversed(name.parents):
            info = component.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
                raise ValueError(_LINUX_FONT_ERROR)

    parent_directories(path)
    info = path.lstat()
    if info.st_uid != owner:
        raise ValueError(_LINUX_FONT_ERROR)
    if stat.S_ISLNK(info.st_mode):
        target = path.readlink()
        target = target if target.is_absolute() else path.parent / target
        try:
            parent_directories(target)
            info = target.lstat()
        except OSError:
            raise ValueError(_LINUX_FONT_ERROR) from None
        path = target
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise ValueError(_LINUX_FONT_ERROR)
    return path


def system_arial() -> str:
    """Return only readable system Arial; never substitute or redistribute it.

    Linux uses a bounded list of system installations, independent of CWD,
    environment, fontconfig and document/profile inputs. A present but invalid
    preferred file is an error, not permission to select another revision.
    Pillow validates the exact bytes before RDKit receives the same file path.
    The local revision remains an OS input, not a product-wide pinned revision.
    """
    if sys.platform.startswith('linux'):
        for path in _LINUX_ARIAL_PATHS:
            try:
                resolved = _linux_system_path(path)
            except FileNotFoundError:
                continue
            except (OSError, RuntimeError, ValueError):
                raise ValueError(_LINUX_FONT_ERROR) from None
            return _validated_arial(resolved, _LINUX_FONT_ERROR)
        raise ValueError(_LINUX_FONT_ERROR)
    return _validated_arial(_windows_directory() / 'Fonts' / 'arial.ttf', _FONT_ERROR)
