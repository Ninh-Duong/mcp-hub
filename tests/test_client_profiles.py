"""Client profile loading, matching, capability downgrade, and session execution validation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcp_hub.client_profiles import (
    ClientProfile,
    default_session_execution,
    load_client_profiles,
    resolve_client_profile,
    validate_session_execution,
)
from mcp_hub.config import ConfigError


def _valid_profile(key: str = "custom", **overrides) -> dict:
    base = {
        "modes": ["multi_agent", "single_agent"],
        "default_mode": "multi_agent",
        "mechanism": "subagent",
        "orchestrate_models": ["pro", "inherit"],
        "default_orchestrate_model": "pro",
        "execute_models": ["flash", "pro"],
        "default_execute_model": "flash",
    }
    base.update(overrides)
    return base


class LoadClientProfilesTest(unittest.TestCase):
    def test_loads_default_catalog_with_all_six_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = load_client_profiles(private_root=tmp)
        expected = {"antigravity", "claude-code", "codex", "cursor", "github-copilot", "gemini-cli"}
        self.assertEqual(catalog.default.key, "default")
        self.assertEqual(catalog.default.source, "default")
        self.assertEqual(set(catalog.profiles), expected)
        for profile in catalog.profiles.values():
            self.assertEqual(profile.source, "default")

    def test_merges_private_overrides_and_allows_omitted_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            override = {
                "version": 1,
                "default": _valid_profile(
                    modes=["single_agent"],
                    default_mode="single_agent",
                    mechanism="inline",
                    orchestrate_models=["sonnet", "inherit"],
                    default_orchestrate_model="sonnet",
                    execute_models=["sonnet", "inherit"],
                    default_execute_model="sonnet",
                ),
                "profiles": {
                    "antigravity": _valid_profile(orchestrate_models=["pro", "flash"], default_orchestrate_model="pro"),
                    "my-client": _valid_profile(modes=["single_agent"], default_mode="single_agent", mechanism="inline"),
                },
            }
            (root / "client_profiles.json").write_text(json.dumps(override), encoding="utf-8")
            catalog = load_client_profiles(private_root=root)
        self.assertEqual(catalog.default.source, "private")
        self.assertEqual(catalog.default.default_orchestrate_model, "sonnet")
        self.assertEqual(catalog.profiles["antigravity"].source, "private")
        self.assertEqual(catalog.profiles["antigravity"].match, ("antigravity",))
        self.assertEqual(catalog.profiles["my-client"].source, "private")
        self.assertEqual(catalog.profiles["my-client"].match, ("my-client",))
        self.assertEqual(catalog.profiles["codex"].source, "default")

    def test_rejects_path_outside_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            root.mkdir()
            with self.assertRaisesRegex(ConfigError, "inside config/private"):
                load_client_profiles(path=Path(tmp) / "outside.json", private_root=root)

    def test_rejects_malformed_or_invalid_private_profiles(self) -> None:
        cases = {
            "bad json": "{not json",
            "non-object": json.dumps([]),
            "wrong version": json.dumps({"version": 2}),
            "duplicate models": json.dumps({
                "version": 1,
                "profiles": {"custom": _valid_profile(orchestrate_models=["pro", "pro"])},
            }),
            "duplicate modes": json.dumps({
                "version": 1,
                "profiles": {"custom": _valid_profile(modes=["single_agent", "single_agent"], default_mode="single_agent")},
            }),
            "unknown mode": json.dumps({
                "version": 1,
                "profiles": {"custom": _valid_profile(modes=["swarm"], default_mode="swarm")},
            }),
            "default model not in list": json.dumps({
                "version": 1,
                "profiles": {"custom": _valid_profile(default_execute_model="unknown")},
            }),
            "invalid mechanism": json.dumps({
                "version": 1,
                "profiles": {"custom": _valid_profile(mechanism="bad mechanism!")},
            }),
            "invalid profile key": json.dumps({
                "version": 1,
                "profiles": {"Invalid Key": _valid_profile()},
            }),
            "default key in profiles map": json.dumps({
                "version": 1,
                "profiles": {"default": _valid_profile()},
            }),
            "unknown top-level key": json.dumps({"version": 1, "api_key": "nope"}),
            "unknown profile field": json.dumps({
                "version": 1,
                "profiles": {"custom": _valid_profile(token="nope")},
            }),
            "sensitive profile key": json.dumps({
                "version": 1,
                "profiles": {"my_api_key": _valid_profile()},
            }),
        }
        for label, content in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "client_profiles.json").write_text(content, encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_client_profiles(private_root=tmp)


class ResolveAndValidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = load_client_profiles(private_root=self.tmp.name)

    def test_matches_known_clients_and_falls_back_to_default(self) -> None:
        expected = {
            "Antigravity": "antigravity",
            "agy": "antigravity",
            "Claude Code": "claude-code",
            "claude-code": "claude-code",
            "Codex": "codex",
            "codex-mcp-client": "codex",
            "Cursor": "cursor",
            "GitHub Copilot": "github-copilot",
            "Visual Studio Code": "github-copilot",
            "Gemini CLI": "gemini-cli",
        }
        for raw_name, key in expected.items():
            with self.subTest(raw_name=raw_name):
                self.assertEqual(resolve_client_profile(self.catalog, raw_name, shared_service=True).key, key)
        for fallback in ("unknown", "mcp"):
            with self.subTest(fallback=fallback):
                self.assertEqual(resolve_client_profile(self.catalog, fallback, shared_service=True).key, "default")

    def test_explicit_client_override_and_unknown_error(self) -> None:
        profile = resolve_client_profile(self.catalog, "unknown", "antigravity", shared_service=True)
        self.assertEqual(profile.key, "antigravity")
        self.assertEqual(resolve_client_profile(self.catalog, "unknown", "default", shared_service=True).key, "default")
        with self.assertRaisesRegex(ValueError, "Unknown client profile"):
            resolve_client_profile(self.catalog, "unknown", "missing-client", shared_service=True)

    def test_isolated_stdio_downgrades_multi_agent_profiles(self) -> None:
        for key in ("antigravity", "claude-code", "codex", "cursor", "github-copilot"):
            with self.subTest(key=key):
                profile = resolve_client_profile(self.catalog, key, shared_service=False)
                self.assertEqual(profile.modes, ("single_agent",))
                self.assertEqual(profile.default_mode, "single_agent")
                self.assertEqual(profile.mechanism, "inline")

    def test_default_session_execution(self) -> None:
        multi = resolve_client_profile(self.catalog, "Antigravity", shared_service=True)
        multi_exec = default_session_execution(multi)
        self.assertEqual((multi_exec.mode, multi_exec.mechanism, multi_exec.orchestrate_model, multi_exec.execute_model),
                         ("multi_agent", "invoke_subagent", "pro", "flash"))

        single = resolve_client_profile(self.catalog, "Antigravity", shared_service=False)
        single_exec = default_session_execution(single)
        self.assertEqual(single_exec.mode, "single_agent")
        self.assertEqual(single_exec.mechanism, "inline")
        self.assertEqual(single_exec.execute_model, single.default_orchestrate_model)

    def test_validate_session_execution_accepts_valid_and_rejects_invalid(self) -> None:
        profile = resolve_client_profile(self.catalog, "Antigravity", shared_service=True)
        multi = validate_session_execution(profile, "multi_agent", "pro", "flash_lite")
        self.assertEqual((multi.mode, multi.mechanism, multi.orchestrate_model, multi.execute_model),
                         ("multi_agent", "invoke_subagent", "pro", "flash_lite"))

        single_omitted = validate_session_execution(profile, "single_agent", "pro", None)
        self.assertEqual((single_omitted.mode, single_omitted.mechanism, single_omitted.execute_model),
                         ("single_agent", "inline", "pro"))
        single_empty = validate_session_execution(profile, "single_agent", "flash", "")
        self.assertEqual(single_empty.execute_model, "flash")

        bad_cases = (
            (None, "pro", "flash"),
            ("swarm", "pro", "flash"),
            ("multi_agent", None, "flash"),
            ("multi_agent", "bad-model", "flash"),
            ("multi_agent", "pro", None),
            ("multi_agent", "pro", "bad-model"),
            ("single_agent", "pro", "bad-model"),
        )
        for mode, orch, exe in bad_cases:
            with self.subTest(mode=mode, orch=orch, exe=exe), self.assertRaises(ValueError):
                validate_session_execution(profile, mode, orch, exe)

    def test_has_choices_flag(self) -> None:
        self.assertFalse(self.catalog.default.has_choices)
        for key in ("antigravity", "claude-code", "codex", "cursor", "github-copilot", "gemini-cli"):
            with self.subTest(key=key):
                self.assertTrue(self.catalog.profiles[key].has_choices)
        minimal = ClientProfile("min", ("min",), ("single_agent",), "single_agent", "inline", ("inherit",), "inherit", ("inherit",), "inherit")
        self.assertFalse(minimal.has_choices)


if __name__ == "__main__":
    unittest.main()
