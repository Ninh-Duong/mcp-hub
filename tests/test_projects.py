from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path


class ProjectConfigTest(unittest.TestCase):
    def test_empty_project_example_is_safe_to_copy_before_user_adds_aliases(self) -> None:
        from mcp_hub.config import HUB_ROOT
        from mcp_hub.jobs.projects import load_projects

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "projects.json"
            shutil.copy2(HUB_ROOT / "config" / "projects.example.json", path)
            self.assertEqual(load_projects(path=path, private_root=root), {})

    def test_loads_registered_aliases_and_validates_default_environment(self) -> None:
        from mcp_hub.jobs.projects import load_projects

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "projects.json"
            path.write_text(json.dumps({
                "version": 1,
                "projects": {"orders": {"default_env": None, "allowed_envs": ["dev", "stg", "prod"]}},
            }), encoding="utf-8")
            projects = load_projects(path=path, private_root=root)

        self.assertEqual(projects["orders"].default_env, None)
        self.assertEqual(projects["orders"].allowed_envs, ("dev", "stg", "prod"))

    def test_rejects_invalid_environment_and_unknown_project_configuration(self) -> None:
        from mcp_hub.jobs.projects import ProjectConfigError, load_projects

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "projects.json"
            for payload in (
                {"version": 1, "projects": {"orders": {"default_env": "prod-eu", "allowed_envs": ["prod"]}}},
                {"version": 1, "projects": {"../escape": {"allowed_envs": ["dev"]}}},
            ):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(ProjectConfigError):
                    load_projects(path=path, private_root=root)


if __name__ == "__main__":
    unittest.main()
