"""Private, persistent key for deriving replayable P0 worker assignments."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path

from .durable_file_lock import exclusive_file_lock

FILE_NAME = "work-p0-assignment.key"


KEY_REASONS = ("missing", "unreadable", "acl_invalid", "invalid_bytes")


class P0AssignmentKeyUnavailable(ValueError):
    """Fail-closed key error with one bounded status reason."""

    def __init__(self, message, *, reason=None):
        super().__init__(message)
        if reason is None:
            # Messages are fixed module-owned strings: SID/ACL/DACL/permission
            # failures are ACL validation; grammar/size failures are bytes.
            if any(token in message for token in ("SID", "ACL", "permissions")):
                reason = "acl_invalid"
            elif message.startswith("invalid P0 assignment"):
                reason = "invalid_bytes"
            else:
                reason = "unreadable"
        if reason not in KEY_REASONS:
            raise ValueError("invalid P0 assignment key reason")
        self.reason = reason


class P0AssignmentKeyStore:
    def __init__(self, path):
        # Absolute spelling without resolving links: a restart must still inspect
        # and reject a link at the key pathname rather than silently follow it.
        self.path = Path(os.path.abspath(path))

    @classmethod
    def beside_ledger(cls, ledger_path):
        return cls(Path(ledger_path).with_name(FILE_NAME))

    @staticmethod
    def _validate(raw):
        # Exact on-disk grammar: one lowerhex64 value, no whitespace or suffix.
        if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
            raise P0AssignmentKeyUnavailable("invalid P0 assignment derivation key")
        return bytes.fromhex(raw)

    @staticmethod
    def _windows_libraries():
        return (ctypes.WinDLL("advapi32", use_last_error=True),
                ctypes.WinDLL("kernel32", use_last_error=True))

    @classmethod
    def _current_windows_sid(cls, *, libraries=None):
        if os.name != "nt":
            raise P0AssignmentKeyUnavailable("Windows SID requested on another platform")
        advapi, kernel = libraries or cls._windows_libraries()
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        advapi.OpenProcessToken.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
        advapi.OpenProcessToken.restype = wintypes.BOOL
        advapi.GetTokenInformation.argtypes = (
            wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD))
        advapi.GetTokenInformation.restype = wintypes.BOOL
        advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
        advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        token = wintypes.HANDLE()
        TOKEN_QUERY, TOKEN_USER = 0x0008, 1
        opened = False
        sid_text = wintypes.LPWSTR()
        try:
            if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
                raise ctypes.WinError(ctypes.get_last_error())
            opened = True
            size = wintypes.DWORD()
            first = advapi.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
            error = ctypes.get_last_error()
            if first or error != 122 or not size.value:  # ERROR_INSUFFICIENT_BUFFER
                raise ctypes.WinError(error)
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, TOKEN_USER, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_text)):
                raise ctypes.WinError(ctypes.get_last_error())
            value = sid_text.value
            if not value or re.fullmatch(r"S-1-(?:[0-9]+-)*[0-9]+", value) is None:
                raise P0AssignmentKeyUnavailable(
                    "P0 assignment key owner SID invalid; recovery required")
            return value
        except P0AssignmentKeyUnavailable:
            raise
        except OSError as error:
            raise P0AssignmentKeyUnavailable(
                "P0 assignment key owner SID unavailable; recovery required") from error
        finally:
            cleanup_failed = False
            if sid_text:
                cleanup_failed = bool(kernel.LocalFree(sid_text))
            if opened:
                cleanup_failed = not bool(kernel.CloseHandle(token)) or cleanup_failed
            if cleanup_failed and sys.exc_info()[0] is None:
                raise P0AssignmentKeyUnavailable(
                    "P0 assignment key SID cleanup failed; recovery required")

    @classmethod
    def _windows_acl_entries(cls, path, *, libraries=None):
        """Return exact non-inherited DACL entries as (type, flags, mask, SID)."""
        advapi, kernel = libraries or cls._windows_libraries()

        class AclSizeInformation(ctypes.Structure):
            _fields_ = (("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                        ("AclBytesFree", wintypes.DWORD))

        class AceHeader(ctypes.Structure):
            _fields_ = (("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte),
                        ("AceSize", wintypes.WORD))

        advapi.GetNamedSecurityInfoW.argtypes = (
            wintypes.LPWSTR, ctypes.c_uint, wintypes.DWORD, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p))
        advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi.GetAclInformation.argtypes = (
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_uint)
        advapi.GetAclInformation.restype = wintypes.BOOL
        advapi.GetAce.argtypes = (
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
        advapi.GetAce.restype = wintypes.BOOL
        advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
        advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
        SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, ACL_SIZE_INFORMATION = 1, 4, 2
        result = advapi.GetNamedSecurityInfoW(
            str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None,
            ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if result != 0 or not descriptor.value or not dacl.value:
            if descriptor.value:
                kernel.LocalFree(descriptor)
            raise P0AssignmentKeyUnavailable(
                "P0 assignment derivation key DACL unavailable; recovery required")
        cleanup_failed = False
        try:
            size = AclSizeInformation()
            if not advapi.GetAclInformation(
                    dacl, ctypes.byref(size), ctypes.sizeof(size), ACL_SIZE_INFORMATION):
                raise ctypes.WinError(ctypes.get_last_error())
            entries = []
            for index in range(size.AceCount):
                ace = ctypes.c_void_p()
                if not advapi.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
                    raise ctypes.WinError(ctypes.get_last_error())
                header = ctypes.cast(ace, ctypes.POINTER(AceHeader)).contents
                if header.AceSize < 12:
                    raise P0AssignmentKeyUnavailable(
                        "P0 assignment derivation key DACL invalid; recovery required")
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                if header.AceType not in (0, 1):  # only simple allow/deny carry SID at +8
                    entries.append((header.AceType, header.AceFlags, mask, None))
                    continue
                sid_pointer = ctypes.c_void_p(ace.value + 8)
                sid_text = wintypes.LPWSTR()
                if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_text)):
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    sid = sid_text.value
                    if not sid or re.fullmatch(r"S-1-(?:[0-9]+-)*[0-9]+", sid) is None:
                        raise P0AssignmentKeyUnavailable(
                            "P0 assignment derivation key DACL invalid; recovery required")
                    entries.append((header.AceType, header.AceFlags, mask, sid))
                finally:
                    cleanup_failed = bool(kernel.LocalFree(sid_text)) or cleanup_failed
            return tuple(entries)
        except P0AssignmentKeyUnavailable:
            raise
        except OSError as error:
            raise P0AssignmentKeyUnavailable(
                "P0 assignment derivation key DACL unavailable; recovery required") from error
        finally:
            cleanup_failed = bool(kernel.LocalFree(descriptor)) or cleanup_failed
            if cleanup_failed and sys.exc_info()[0] is None:
                raise P0AssignmentKeyUnavailable(
                    "P0 assignment key DACL cleanup failed; recovery required")

    @classmethod
    def _validate_windows_acl(cls, path):
        """Read-only exact DACL validation for every existing-key read."""
        if os.name != "nt":
            return
        sid = cls._current_windows_sid()
        allowed = {"S-1-5-18", "S-1-5-32-544", sid}
        entries = cls._windows_acl_entries(path)
        exact = (len(entries) == 3 and {entry[3] for entry in entries} == allowed
                 and all(entry[:3] == (0, 0, 0x1F01FF) for entry in entries))
        if not exact:
            raise P0AssignmentKeyUnavailable(
                "P0 assignment derivation key ACL has unexpected entries; recovery required")

    @classmethod
    def _restrict_windows_acl(cls, path):
        """Creation/restore-only hardening followed by read-only validation."""
        if os.name != "nt":
            return
        sid = cls._current_windows_sid()
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r",
             "*S-1-5-18:(F)", "*S-1-5-32-544:(F)", f"*{sid}:(F)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False,
        )
        if result.returncode != 0:
            raise P0AssignmentKeyUnavailable(
                "P0 assignment derivation key ACL unavailable; recovery required")
        cls._validate_windows_acl(path)

    def _open_existing(self):
        if self.path.is_symlink():
            raise P0AssignmentKeyUnavailable(
                "P0 assignment derivation key link refused; recovery required",
                reason="unreadable")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        facts = os.fstat(descriptor)
        if not stat.S_ISREG(facts.st_mode) or facts.st_size != 64:
            os.close(descriptor)
            raise P0AssignmentKeyUnavailable("invalid P0 assignment derivation key")
        if os.name != "nt" and facts.st_mode & 0o077:
            os.close(descriptor)
            raise P0AssignmentKeyUnavailable(
                "P0 assignment derivation key permissions are not private; recovery required")
        if os.name == "nt":
            try:
                self._validate_windows_acl(self.path)
                # Re-stat the already-open handle after the pathname DACL read;
                # pathname replacement cannot select different key bytes.
                validated = os.fstat(descriptor)
                if ((facts.st_dev, facts.st_ino, facts.st_size)
                        != (validated.st_dev, validated.st_ino, validated.st_size)
                        or not stat.S_ISREG(validated.st_mode)):
                    raise P0AssignmentKeyUnavailable(
                        "P0 assignment derivation key changed during ACL validation; recovery required",
                        reason="unreadable")
            except Exception:
                os.close(descriptor)
                raise
        return descriptor

    def read(self):
        """Read an existing key; never rotate or create during assignment replay."""
        descriptor = None
        try:
            descriptor = self._open_existing()
            with os.fdopen(descriptor, "r", encoding="ascii", newline="") as handle:
                descriptor = None
                return self._validate(handle.read(65))
        except P0AssignmentKeyUnavailable:
            raise
        except FileNotFoundError as error:
            raise P0AssignmentKeyUnavailable(
                "P0 assignment derivation key unavailable; recovery required",
                reason="missing") from error
        except UnicodeError as error:
            raise P0AssignmentKeyUnavailable(
                "invalid P0 assignment derivation key", reason="invalid_bytes") from error
        except OSError as error:
            raise P0AssignmentKeyUnavailable(
                "P0 assignment derivation key unavailable; recovery required",
                reason="unreadable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def ensure_before_first_intent_locked(self):
        """Accepted create-once lifecycle while the caller holds the key lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return self.read()
        key = secrets.token_hex(32)
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(8) + ".tmp")
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii", newline="") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "nt":
                self._restrict_windows_acl(temporary)
            else:
                temporary.chmod(0o600)
            # Publish without replacement. An external creator racing the
            # advisory lock cannot replace or select this process's key.
            os.link(temporary, self.path)
            if os.name != "nt":
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return bytes.fromhex(key)

    def ensure_before_first_intent(self):
        """Create once before any P0 launch intent exists, then read-only forever."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            return self.ensure_before_first_intent_locked()
