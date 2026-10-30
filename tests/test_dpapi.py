from __future__ import annotations

import ctypes
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mcp_hub.security import dpapi
from mcp_hub.security.dpapi import DPAPIProtector


class _FakeCryptFunction:
    """Stands in for CryptProtectData/CryptUnprotectData: reads the input blob and fills the output blob."""

    def __init__(self, transform, ok: int = 1) -> None:
        self.transform = transform
        self.ok = ok
        self.inputs: list[bytes] = []
        self.buffer = None

    def __call__(self, source_ref, *rest):
        source = source_ref._obj
        data = ctypes.string_at(source.pbData, source.cbData)
        self.inputs.append(data)
        if not self.ok:
            return 0
        output = self.transform(data)
        self.buffer = ctypes.create_string_buffer(output, len(output))
        destination = rest[-1]._obj
        destination.cbData = len(output)
        destination.pbData = ctypes.cast(self.buffer, ctypes.POINTER(ctypes.c_ubyte))
        return 1


class DPAPIProtectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protect_fn = _FakeCryptFunction(lambda data: b"sealed:" + data[::-1])
        self.unprotect_fn = _FakeCryptFunction(lambda data: data.removeprefix(b"sealed:")[::-1])
        self.kernel32 = SimpleNamespace(LocalFree=Mock())
        self.loaded: list[str] = []
        # Every test starts without the process-wide cache, and leaves none of its fakes in it.
        cache = patch.object(dpapi, "_api", None)
        cache.start()
        self.addCleanup(cache.stop)

    def _windows(self, protect_fn=None):
        crypt32 = SimpleNamespace(
            CryptProtectData=protect_fn or self.protect_fn, CryptUnprotectData=self.unprotect_fn,
        )
        libraries = {"crypt32": crypt32, "kernel32": self.kernel32}

        def win_dll(name, use_last_error=False):
            self.loaded.append(name)
            return libraries[name]

        return (
            patch.object(dpapi, "os", SimpleNamespace(name="nt")),
            patch.object(dpapi.ctypes, "WinDLL", win_dll, create=True),
        )

    def test_non_windows_platform_is_rejected(self) -> None:
        with patch.object(dpapi, "os", SimpleNamespace(name="posix")):
            with self.assertRaisesRegex(OSError, "Windows only"):
                DPAPIProtector().protect(b"data")

    def test_protect_and_unprotect_use_the_matching_api_and_free_the_output(self) -> None:
        platform, windll = self._windows()
        with platform, windll:
            sealed = DPAPIProtector().protect(b"secret job data")
            opened = DPAPIProtector().unprotect(sealed)

        self.assertEqual(sealed, b"sealed:" + b"secret job data"[::-1])
        self.assertEqual(opened, b"secret job data")
        self.assertEqual(self.protect_fn.inputs, [b"secret job data"])
        self.assertEqual(self.unprotect_fn.inputs, [sealed])
        self.assertEqual(self.kernel32.LocalFree.call_count, 2)

    def test_libraries_and_signatures_are_loaded_once(self) -> None:
        platform, windll = self._windows()
        with platform, windll:
            protector = DPAPIProtector()
            for _ in range(3):
                protector.unprotect(protector.protect(b"value"))

        self.assertEqual(self.loaded, ["crypt32", "kernel32"])
        self.assertEqual(len(self.protect_fn.argtypes), 7)
        self.assertEqual(len(self.unprotect_fn.argtypes), 7)
        self.assertEqual(self.kernel32.LocalFree.argtypes, [ctypes.c_void_p])

    def test_api_failure_raises_the_windows_error(self) -> None:
        platform, windll = self._windows(_FakeCryptFunction(lambda data: data, ok=0))
        with platform, windll, \
                patch.object(dpapi.ctypes, "get_last_error", lambda: 13, create=True), \
                patch.object(dpapi.ctypes, "WinError", lambda code: OSError(code, "denied"), create=True):
            with self.assertRaises(OSError) as caught:
                DPAPIProtector().protect(b"data")
        self.assertEqual(caught.exception.errno, 13)
        self.kernel32.LocalFree.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "DPAPI is available on Windows only")
    def test_real_round_trip_on_windows(self) -> None:
        protector = DPAPIProtector()
        sealed = protector.protect(b"round-trip value")
        self.assertNotEqual(sealed, b"round-trip value")
        self.assertEqual(protector.unprotect(sealed), b"round-trip value")


if __name__ == "__main__":
    unittest.main()
