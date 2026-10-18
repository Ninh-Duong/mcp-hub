import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mcp_hub.config import HUB_ROOT, PRIVATE_ROOT, ConfigError, load_config


class LoadConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(dir=PRIVATE_ROOT))
        (self.tmp / "child" / "bin").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def load(self, server: dict):
        rel = self.tmp.relative_to(HUB_ROOT).as_posix()
        (self.tmp / "child.json").write_text(json.dumps(server), encoding="utf-8")
        hub = {
            "version": 1,
            "servers": {"child": {"enabled": True, "configFile": f"{rel}/child.json"}},
            "features": {"f": {"enabled": True, "tools": [{"server": "child", "name": "t"}]}},
        }
        (self.tmp / "hub.json").write_text(json.dumps(hub), encoding="utf-8")
        return load_config(self.tmp / "hub.json").servers["child"]

    def server(self, **extra) -> dict:
        rel = self.tmp.relative_to(HUB_ROOT).as_posix()
        return {"transport": "stdio", "command": "bin/run.exe", "cwd": f"{rel}/child", **extra}

    def test_relative_cwd_and_command(self) -> None:
        child = self.load(self.server())
        self.assertEqual(child.cwd, (self.tmp / "child").resolve())
        self.assertEqual(Path(child.command), (self.tmp / "child" / "bin" / "run.exe").resolve())

    def test_bare_command_stays_on_path(self) -> None:
        self.assertEqual(self.load(self.server(command="python")).command, "python")

    def test_unfilled_placeholder_is_rejected_without_value(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            self.load(self.server(env={"API_TOKEN": "<SET_BY_USER>"}))
        self.assertIn("unfilled", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
