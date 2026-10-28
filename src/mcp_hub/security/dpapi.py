from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


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
        source_buffer = ctypes.create_string_buffer(data)
        source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        destination = DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if unprotect:
            function = crypt32.CryptUnprotectData
            function.argtypes = [
                ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
            ]
            ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination))
        else:
            function = crypt32.CryptProtectData
            function.argtypes = [
                ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
            ]
            ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree(destination.pbData)
