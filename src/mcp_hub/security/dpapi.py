from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Any, NamedTuple


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


class DPAPIProtector:
    """Encrypt bytes for the current Windows user with DPAPI."""

    def protect(self, data: bytes) -> bytes:
        return self._invoke(data, unprotect=False)

    def unprotect(self, data: bytes) -> bytes:
        return self._invoke(data, unprotect=True)

    @staticmethod
    def _invoke(data: bytes, *, unprotect: bool) -> bytes:
        if os.name != "nt":
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
