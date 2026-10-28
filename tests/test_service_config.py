from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class ServiceConfigTest(unittest.TestCase):
    def test_creates_private_localhost_config_without_exposing_token_in_repr(self) -> None:
        from mcp_hub.service_config import load_service_config

        with tempfile.TemporaryDirectory() as directory:
            private_root = Path(directory)
            path = private_root / "service.json"
            config = load_service_config(path=path, private_root=private_root)

            self.assertEqual(config.host, "127.0.0.1")
            self.assertEqual(config.port, 8765)
            self.assertGreaterEqual(len(config.token), 32)
            self.assertNotIn(config.token, repr(config))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["token"], config.token)

    def test_rejects_config_path_outside_private_root(self) -> None:
        from mcp_hub.service_config import ServiceConfigError, load_service_config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ServiceConfigError):
                load_service_config(path=root / "outside.json", private_root=root / "private")

    def test_refuses_public_bind_and_short_token_in_existing_config(self) -> None:
        from mcp_hub.service_config import ServiceConfigError, load_service_config

        with tempfile.TemporaryDirectory() as directory:
            private_root = Path(directory)
            path = private_root / "service.json"
            path.write_text(json.dumps({
                "version": 1,
                "host": "0.0.0.0",
                "port": 8765,
                "token": "short",
            }), encoding="utf-8")

            with self.assertRaises(ServiceConfigError):
                load_service_config(path=path, private_root=private_root)

    def test_refuses_custom_port_that_would_mismatch_local_client_entries(self) -> None:
        from mcp_hub.service_config import ServiceConfigError, load_service_config

        with tempfile.TemporaryDirectory() as directory:
            private_root = Path(directory)
            path = private_root / "service.json"
            path.write_text(json.dumps({
                "version": 1,
                "host": "127.0.0.1",
                "port": 9876,
                "token": "test-token-value-long-enough-for-validation-12345",
            }), encoding="utf-8")
            with self.assertRaises(ServiceConfigError):
                load_service_config(path=path, private_root=private_root)


if __name__ == "__main__":
    unittest.main()
