"""Unpromoted Windows conditional-save candidate; import performs no native I/O.

Only existing, local fixed-NTFS, non-reparse, single-link files are supported.
No fallback or retry exists. The caller owns project validation, durable backup,
post-commit pathname verification, and UI handling. See candidate-review.md.
"""
from __future__ import annotations

import ctypes
import hashlib
import ntpath
import os
import re
from pathlib import PureWindowsPath

MAX_BYTES = 4_000_000
TRANSACTION_TIMEOUT_MS = 10_000


class SaveError(OSError):
    """No saved receipt may be issued; inspect outcome before offering recovery."""

    def __init__(self, message, *, operation=None, win32=None,
                 outcome="not_committed", cleanup_errors=()):
        super().__init__(message)
        self.operation = operation
        self.win32 = win32
        self.outcome = outcome
        self.cleanup_errors = tuple(cleanup_errors)


class SaveUnsupported(SaveError):
    """This adapter cannot safely attempt the destination; choose Save As."""


class SaveConflict(SaveError):
    """Expected bytes are stale, missing, oversized, or unavailable to a writer."""


class SaveOutcomeUnknown(SaveError):
    """Commit was attempted but not acknowledged; do not retry or claim success."""


def _path(value):
    value = os.fspath(value)
    if not isinstance(value, str):
        raise SaveUnsupported("A Unicode absolute drive path is required.")
    value = value.replace("/", "\\")
    # Reject device/UNC paths, ADS, traversal and Win32 name normalization aliases.
    if not re.match(r"^[A-Za-z]:\\", value) or value.startswith("\\"):
        raise SaveUnsupported("Only absolute local drive paths are supported.")
    parts = value[3:].split("\\")
    if not parts or any(not p or p in (".", "..") or p.endswith((".", " "))
                        or any(ord(c) < 32 or c in '<>:"|?*' for c in p)
                        or re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", p, re.I)
                        for p in parts):
        raise SaveUnsupported("Unsupported or ambiguous destination path.")
    try:
        units = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise SaveUnsupported("Destination contains an invalid Unicode sequence.") from exc
    if units > 32759 or any(len(p.encode("utf-16-le")) // 2 > 255 for p in parts):
        raise SaveUnsupported("Destination exceeds the supported path length.")
    return value


def _failure(operation, code):
    # Access denied is deliberately an I/O error, not an inferred conflict.
    cls = SaveConflict if code in (2, 3, 32, 33, 6800) else SaveError
    if code in (1, 50, 120, 6805):
        cls = SaveUnsupported
    return cls(f"{operation} failed (Windows error {code}).", operation=operation, win32=code)


def conditional_write_existing(path, data, expected_sha256, backup, *, _api=None):
    """Commit bytes conditionally; return their digest only after clean closure.

    backup(old_bytes) must durably preserve those exact bytes before returning.
    It is trusted application code, invoked once while the target is protected,
    only after the expected hash matches. Its exception aborts this save.
    _api is a private pure-test seam, not an application-selected backend.
    """
    path = _path(path)
    if type(data) is not bytes or len(data) > MAX_BYTES:
        raise ValueError("Save bytes must be immutable and at most 4,000,000 bytes.")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("An expected SHA-256 from a prior open/save is required.")
    if not callable(backup):
        raise TypeError("A durable backup callback is required.")
    api = _api if _api is not None else _Win32()
    parents, transaction, target = [], None, None
    primary, cleanup = None, []
    commit_attempted = committed = False

    def close(handle):
        try:
            api.close(handle)
        except BaseException as exc:
            cleanup.append(exc)

    try:
        api.check_drive(path[:3])
        for parent in reversed(PureWindowsPath(path).parents):
            handle = api.open_directory(str(parent))
            parents.append(handle)  # Own immediately, before fallible inspection.
            api.check_directory(handle, str(parent))
        api.check_volume(parents[-1])
        transaction = api.begin()
        target = api.open_existing(path, transaction)  # Writer BEFORE any read.
        api.check_target(target, path)
        old = api.read(target, MAX_BYTES)
        if len(old) > MAX_BYTES:
            raise SaveConflict("Destination is oversized; choose Save As.")
        if hashlib.sha256(old).hexdigest() != expected_sha256:
            raise SaveConflict("Destination changed on disk; choose Save As or reopen it.")
        backup(old)
        api.write(target, data)
        if api.read(target, MAX_BYTES) != data:
            raise SaveError("Transactional readback does not match the complete save bytes.")
        # A failed CloseHandle has ambiguous ownership; never retry that handle.
        handle, target = target, None
        api.close(handle)
        commit_attempted = True
        api.commit(transaction)
        committed = True
    except BaseException as exc:
        primary = exc
    finally:
        if target is not None:
            close(target)
        if transaction is not None:
            if not committed:
                try:
                    api.rollback(transaction)
                except BaseException as exc:
                    cleanup.append(exc)
            close(transaction)
        for handle in reversed(parents):
            close(handle)

    if commit_attempted and not committed:
        raise SaveOutcomeUnknown("Commit was not acknowledged. Keep recovery data; inspect the destination before another save.",
                                 outcome="unknown", cleanup_errors=cleanup) from primary
    if cleanup:
        raise SaveError("Native save cleanup was not fully acknowledged; no saved receipt can be issued.",
                        outcome="committed" if committed else "not_committed",
                        cleanup_errors=cleanup) from primary
    if primary is not None:
        if isinstance(primary, SaveError):
            raise primary
        if isinstance(primary, (KeyboardInterrupt, SystemExit)):
            raise primary
        raise SaveError("Save preparation failed; the transaction was not committed.") from primary
    return hashlib.sha256(data).hexdigest()


class _Win32:
    """Minimal synchronous ctypes calls; construction is the native I/O boundary."""

    def __init__(self):
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise SaveUnsupported("This conditional writer requires 64-bit Windows.")
        from ctypes import wintypes as W
        self.W = W
        self.Info = type("Info", (ctypes.Structure,), {"_fields_": [(n, W.DWORD) for n in
            ("attrs", "cl", "ch", "al", "ah", "wl", "wh", "volume", "size_hi", "size_lo", "links", "index_hi", "index_lo")]})
        self.invalid = ctypes.c_void_p(-1).value
        try:
            # System32-only DLL search; do not load a similarly named local DLL.
            self.k = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
            self.t = ctypes.WinDLL("ktmw32.dll", use_last_error=True, winmode=0x800)
            signatures = {
                "CloseHandle": ([W.HANDLE], W.BOOL),
                "CreateFileW": ([W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
                "CreateFileTransactedW": ([W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE, W.HANDLE, ctypes.c_void_p, ctypes.c_void_p], W.HANDLE),
                "GetFileInformationByHandle": ([W.HANDLE, ctypes.POINTER(self.Info)], W.BOOL),
                "GetFinalPathNameByHandleW": ([W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD], W.DWORD),
                "GetDriveTypeW": ([W.LPCWSTR], W.UINT),
                "GetVolumeInformationByHandleW": ([W.HANDLE, W.LPWSTR, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD), W.LPWSTR, W.DWORD], W.BOOL),
                "ReadFile": ([W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p], W.BOOL),
                "WriteFile": ([W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p], W.BOOL),
                "SetFilePointerEx": ([W.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), W.DWORD], W.BOOL),
                "SetEndOfFile": ([W.HANDLE], W.BOOL),
                "FlushFileBuffers": ([W.HANDLE], W.BOOL),
            }
            for name, (args, result) in signatures.items():
                fn = getattr(self.k, name)
                fn.argtypes, fn.restype = args, result
            self.t.CreateTransaction.argtypes = [ctypes.c_void_p, ctypes.c_void_p, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.LPWSTR]
            self.t.CreateTransaction.restype = W.HANDLE
            for name in ("CommitTransaction", "RollbackTransaction"):
                getattr(self.t, name).argtypes = [W.HANDLE]
                getattr(self.t, name).restype = W.BOOL
        except (AttributeError, OSError) as exc:
            raise SaveUnsupported("Required Windows transaction APIs are unavailable.") from exc

    def _check(self, result, operation):
        if not result:
            raise _failure(operation, ctypes.get_last_error())
        return result

    def _handle(self, result, operation):
        if result in (None, 0, self.invalid):
            raise _failure(operation, ctypes.get_last_error())
        return result

    def close(self, handle):
        self._check(self.k.CloseHandle(handle), "CloseHandle")

    def check_drive(self, root):
        if self.k.GetDriveTypeW(root) != 3:  # DRIVE_FIXED; reject remote/removable/unknown.
            raise SaveUnsupported("Conditional saving requires a local fixed drive.")

    def open_directory(self, path):
        # GENERIC_READ (not attributes-only), SHARE_READ | SHARE_WRITE,
        # OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT. A sibling rename
        # may open this directory for write. Deny SHARE_DELETE and hold through
        # transaction cleanup to retain ordinary ancestor rename exclusion.
        return self._handle(self.k.CreateFileW("\\\\?\\" + path, 0x80000000, 3, None, 3,
                                             0x02200000, None), "Open parent directory")

    def _info(self, handle):
        info = self.Info()
        self._check(self.k.GetFileInformationByHandle(handle, ctypes.byref(info)), "GetFileInformationByHandle")
        return info

    def _same_path(self, handle, path):
        result = ctypes.create_unicode_buffer(32768)
        count = self._check(self.k.GetFinalPathNameByHandleW(handle, result, len(result), 0), "GetFinalPathNameByHandleW")
        if count >= len(result) or ntpath.normcase(result.value.rstrip("\\")) != ntpath.normcase(("\\\\?\\" + path).rstrip("\\")):
            raise SaveUnsupported("Resolved pathname differs from the requested local path.")

    def check_directory(self, handle, path):
        info = self._info(handle)
        if not info.attrs & 0x10 or info.attrs & 0x400:
            raise SaveUnsupported("A destination parent is not an ordinary non-reparse directory.")
        self._same_path(handle, path)

    def check_volume(self, handle):
        flags, name = self.W.DWORD(), ctypes.create_unicode_buffer(64)
        self._check(self.k.GetVolumeInformationByHandleW(handle, None, 0, None, None,
                    ctypes.byref(flags), name, len(name)), "GetVolumeInformationByHandleW")
        if name.value != "NTFS" or not flags.value & 0x00200000 or flags.value & 0x00080000:
            raise SaveUnsupported("Destination requires writable NTFS with transaction support.")

    def begin(self):
        return self._handle(self.t.CreateTransaction(None, None, 1, 0, 0, TRANSACTION_TIMEOUT_MS,
                                                     "nivraM conditional save"), "CreateTransaction")

    def open_existing(self, path, transaction):
        # GENERIC_READ | GENERIC_WRITE; share all; never create/truncate at open.
        return self._handle(self.k.CreateFileTransactedW("\\\\?\\" + path, 0xC0000000, 7, None,
                            3, 0x00200000, None, transaction, None, None), "CreateFileTransactedW")

    def check_target(self, handle, path):
        info = self._info(handle)
        if info.attrs & (0x10 | 0x400 | 0x1000):  # directory/reparse/offline
            raise SaveUnsupported("Destination is not an ordinary resident file.")
        if info.links != 1:
            raise SaveUnsupported("Content replacement of files with multiple hard links is unsupported.")
        self._same_path(handle, path)
        self.check_volume(handle)

    def _seek(self, handle):
        position = ctypes.c_longlong(-1)
        self._check(self.k.SetFilePointerEx(handle, 0, ctypes.byref(position), 0), "SetFilePointerEx")
        if position.value != 0:
            raise SaveError("The transactional file pointer did not reset to zero.")

    def read(self, handle, limit):
        info = self._info(handle)
        size = (info.size_hi << 32) | info.size_lo
        if size > limit:
            raise SaveConflict("Destination exceeds the 4 MB project limit.")
        self._seek(handle)
        buffer, count = ctypes.create_string_buffer(limit + 1), self.W.DWORD()
        self._check(self.k.ReadFile(handle, buffer, limit + 1, ctypes.byref(count), None), "ReadFile")
        if count.value != size:
            raise SaveError("Transactional read is incomplete or the file size changed.")
        return buffer.raw[:count.value]

    def write(self, handle, data):
        self._seek(handle)
        buffer, count = ctypes.create_string_buffer(data), self.W.DWORD()
        self._check(self.k.WriteFile(handle, buffer, len(data), ctypes.byref(count), None), "WriteFile")
        if count.value != len(data):
            raise SaveError("Transactional write was incomplete; no retry was attempted.")
        self._check(self.k.SetEndOfFile(handle), "SetEndOfFile")
        self._check(self.k.FlushFileBuffers(handle), "FlushFileBuffers")

    def commit(self, transaction):
        self._check(self.t.CommitTransaction(transaction), "CommitTransaction")

    def rollback(self, transaction):
        self._check(self.t.RollbackTransaction(transaction), "RollbackTransaction")
