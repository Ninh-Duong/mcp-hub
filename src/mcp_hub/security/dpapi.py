from __future__ import annotations

import ctypes
import os
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any, NamedTuple

from cryptography.fernet import Fernet, InvalidToken


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class _Api(NamedTuple):
    protect: Any
    unprotect: Any
    local_free: Any


_api: _Api | None = None
_api_lock = threading.Lock()


def _load_api() -> _Api:
    """Load crypt32/kernel32 and declare the function signatures once per process."""
    global _api
    with _api_lock:
        if _api is None:
            crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            protect = crypt32.CryptProtectData
            protect.argtypes = [
                ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
            ]
            unprotect = crypt32.CryptUnprotectData
            unprotect.argtypes = [
                ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
            ]
            local_free = kernel32.LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            _api = _Api(protect, unprotect, local_free)
        return _api


WINDOWS_ONLY_MESSAGE = "investigation jobs require Windows DPAPI"


def dpapi_available() -> bool:
    """DPAPI exists only on Windows; elsewhere the job store cannot encrypt and jobs are refused."""
    return os.name == "nt"


class DPAPIProtector:
    """Encrypt bytes for the current Windows user with DPAPI."""

    @property
    def available(self) -> bool:
        return dpapi_available()

    def protect(self, data: bytes) -> bytes:
        return self._invoke(data, unprotect=False)

    def unprotect(self, data: bytes) -> bytes:
        return self._invoke(data, unprotect=True)

    @staticmethod
    def _invoke(data: bytes, *, unprotect: bool) -> bytes:
        if not dpapi_available():
            raise OSError("DPAPI job storage is available on Windows only")
        api = _api or _load_api()
        source_buffer = ctypes.create_string_buffer(data)
        source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        destination = DataBlob()
        function = api.unprotect if unprotect else api.protect
        ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            api.local_free(destination.pbData)


class FernetProtector:
    """Encrypt bytes on POSIX systems with a user-only (0600) Fernet key file."""

    def __init__(self, key_path: Path) -> None:
        self._key_path = Path(key_path)
        self._lock = threading.Lock()
        self._fernet: Fernet | None = None

    @property
    def available(self) -> bool:
        return True

    def _cipher(self) -> Fernet:
        with self._lock:
            if self._fernet is None:
                self._fernet = Fernet(self._load_or_create_key())
            return self._fernet

    def _load_or_create_key(self) -> bytes:
        if self._key_path.is_file():
            return self._key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{self._key_path.name}.", dir=self._key_path.parent)
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            Path(tmp).unlink(missing_ok=True)
            raise
        try:
            with handle:
                handle.write(key)
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            os.replace(tmp, self._key_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return key

    def protect(self, data: bytes) -> bytes:
        return self._cipher().encrypt(data)

    def unprotect(self, data: bytes) -> bytes:
        try:
            return self._cipher().decrypt(data)
        except InvalidToken as exc:
            raise ValueError("Could not decrypt job data") from exc


def default_protector(key_path: Path) -> DPAPIProtector | FernetProtector:
    """Use Windows DPAPI on Windows and a user-restricted local Fernet key elsewhere."""
    # ponytail: Windows uses OS user-bound DPAPI; POSIX uses a 0600 local Fernet key beside the store.
    return DPAPIProtector() if dpapi_available() else FernetProtector(key_path)
