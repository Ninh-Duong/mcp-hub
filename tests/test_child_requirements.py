"""child_requirements: the hub-requirements.json format, and which required keys are still empty."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcp_hub import child_requirements as req


def groups_of(raw: object) -> tuple[req.Group, ...]:
    return req.parse_requirements(raw)


OPENOBSERVE_OR_SEQ = {
    "version": 1,
    "groups": [{"label": "OpenObserve or Seq", "anyOf": [
        ["openobserve.default_env", "openobserve.dev.url", "openobserve.dev.token"],
        ["seq.server_url", "seq.api_key"],
    ]}],
}


class ReadRequirementsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, value: object) -> None:
        (self.repo / req.REQUIREMENTS_FILE).write_text(json.dumps(value), encoding="utf-8")

    def test_no_file_means_no_requirements(self) -> None:
        self.assertEqual(req.read_requirements(self.repo), req.Requirements(req.NONE))

    def test_a_valid_file_is_declared_with_its_groups(self) -> None:
        self.write(OPENOBSERVE_OR_SEQ)
        state = req.read_requirements(self.repo)
        self.assertEqual(state.status, req.DECLARED)
        self.assertEqual(state.groups[0].label, "OpenObserve or Seq")
        self.assertEqual(len(state.groups[0].alternatives), 2)

    def test_a_byte_order_mark_is_accepted(self) -> None:
        (self.repo / req.REQUIREMENTS_FILE).write_text(json.dumps({"version": 1, "groups": []}), encoding="utf-8-sig")
        self.assertEqual(req.read_requirements(self.repo).status, req.DECLARED)

    def test_broken_json_is_invalid(self) -> None:
        (self.repo / req.REQUIREMENTS_FILE).write_text("{not json", encoding="utf-8")
        self.assertEqual(req.read_requirements(self.repo), req.Requirements(req.INVALID))

    def test_malformed_files_are_invalid_not_errors(self) -> None:
        bad_files = [
            [],
            {"version": 2, "groups": []},
            {"version": True, "groups": []},
            {"version": 1, "groups": "x"},
            {"version": 1, "groups": [{"anyOf": [["a.b"]]}]},
            {"version": 1, "groups": [{"label": "x", "anyOf": []}]},
            {"version": 1, "groups": [{"label": "x", "anyOf": [[]]}]},
            {"version": 1, "groups": [{"label": "x", "anyOf": [["bad key!"]]}]},
            {"version": 1, "groups": [{"label": "x", "anyOf": [["a..b"]]}]},
        ]
        for value in bad_files:
            with self.subTest(value=value):
                self.write(value)
                self.assertEqual(req.read_requirements(self.repo).status, req.INVALID)


class MissingKeysTest(unittest.TestCase):
    def test_no_groups_means_nothing_is_missing(self) -> None:
        self.assertEqual(req.missing_keys((), {}), ())

    def test_a_satisfied_alternative_hides_the_others(self) -> None:
        values = {"seq": {"server_url": "https://seq.example.com", "api_key": "secret-value"}}
        self.assertEqual(req.missing_keys(groups_of(OPENOBSERVE_OR_SEQ), values), ())

    def test_the_closest_alternative_is_reported_when_none_is_satisfied(self) -> None:
        values = {"openobserve": {"default_env": "dev", "dev": {"url": ""}}}
        self.assertEqual(req.missing_keys(groups_of(OPENOBSERVE_OR_SEQ), values), ("openobserve.dev.url", "openobserve.dev.token"))

    def test_the_closest_alternative_is_reported_for_seq_when_nothing_is_filled(self) -> None:
        self.assertEqual(req.missing_keys(groups_of(OPENOBSERVE_OR_SEQ), {}), ("seq.server_url", "seq.api_key"))

    def test_empty_whitespace_null_and_placeholder_values_count_as_missing(self) -> None:
        raw = {"version": 1, "groups": [{"label": "jira", "anyOf": [["jira.base_url", "jira.api_token"]]}]}
        values = {"jira": {"base_url": "   ", "api_token": req.SECRET_PLACEHOLDER}}
        self.assertEqual(req.missing_keys(groups_of(raw), values), ("jira.base_url", "jira.api_token"))
        self.assertEqual(req.missing_keys(groups_of(raw), {"jira": {"base_url": None, "api_token": ""}}),
                         ("jira.base_url", "jira.api_token"))

    def test_numbers_and_booleans_count_as_filled(self) -> None:
        raw = {"version": 1, "groups": [{"label": "limit", "anyOf": [["jira.max_mb", "jira.enabled"]]}]}
        self.assertEqual(req.missing_keys(groups_of(raw), {"jira": {"max_mb": 15, "enabled": False}}), ())

    def test_a_key_is_reported_once_even_in_several_groups(self) -> None:
        raw = {"version": 1, "groups": [
            {"label": "a", "anyOf": [["x.one"]]},
            {"label": "b", "anyOf": [["x.one", "x.two"]]},
        ]}
        self.assertEqual(req.missing_keys(groups_of(raw), {}), ("x.one", "x.two"))

    def test_lookup_stops_at_a_non_object(self) -> None:
        self.assertIsNone(req.lookup({"openobserve": "text"}, "openobserve.dev.url"))

    def test_the_result_names_keys_and_never_values(self) -> None:
        values = {"openobserve": {"default_env": "dev", "dev": {"url": "https://logs.example.com", "token": ""}}}
        missing = req.missing_keys(groups_of(OPENOBSERVE_OR_SEQ), values)
        self.assertEqual(missing, ("openobserve.dev.token",))
        self.assertNotIn("logs.example.com", json.dumps(missing))


class ParseErrorsTest(unittest.TestCase):
    def test_parse_requirements_rejects_a_list_of_groups_as_top_level(self) -> None:
        with self.assertRaises(req.RequirementsError):
            req.parse_requirements([])

    def test_a_group_label_is_required(self) -> None:
        with self.assertRaises(req.RequirementsError):
            req.parse_requirements({"version": 1, "groups": [{"label": "  ", "anyOf": [["a.b"]]}]})


if __name__ == "__main__":
    unittest.main()
