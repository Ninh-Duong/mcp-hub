"""Schema rewrites: the `hub_plan_id` argument the Hub adds to every published tool, and argument validation."""
from __future__ import annotations

import copy
import unittest
import uuid
from typing import Any
from unittest import mock

from jsonschema import Draft202012Validator

from mcp_hub.schema_rewrite import PLAN_ID_ARG, validate_args, with_plan_id

FLAT = {"type": "object", "properties": {"env": {"type": "string"}}, "required": ["env"]}
FLAT_CLOSED = {"type": "object", "properties": {"env": {"type": "string"}}, "additionalProperties": False}
ANY_OF = {
    "anyOf": [{"required": ["env"]}, {"required": ["q"]}],
    "properties": {"env": {"type": "string"}, "q": {"type": "string"}},
}
ONE_OF_CLOSED = {
    "properties": {"env": {"type": "string"}, "q": {"type": "string"}},
    "additionalProperties": False,
    "oneOf": [{"required": ["env"]}, {"required": ["q"]}],
}
ALL_OF_CLOSED = {
    "properties": {"env": {"type": "string"}},
    "additionalProperties": False,
    "allOf": [{"required": ["env"]}],
}
REF_TO_DEFINITION = {
    "$ref": "#/$defs/Args",
    "$defs": {"Args": {"type": "object", "properties": {"env": {"type": "string"}}, "required": ["env"]}},
}
SHAPES: dict[str, dict[str, Any]] = {
    "flat": FLAT,
    "flat closed": FLAT_CLOSED,
    "anyOf": ANY_OF,
    "oneOf closed": ONE_OF_CLOSED,
    "allOf closed": ALL_OF_CLOSED,
    "$ref to an open definition": REF_TO_DEFINITION,
}
PLAN_ID = "plan-" + "a" * 32


class WithPlanIdTest(unittest.TestCase):
    def test_a_flat_root_gains_the_argument_in_place(self) -> None:
        schema = with_plan_id(FLAT)
        self.assertEqual(set(schema["properties"]), {"env", PLAN_ID_ARG})
        self.assertEqual(schema["required"], ["env"])
        self.assertEqual(with_plan_id(None)["type"], "object")
        self.assertIn(PLAN_ID_ARG, with_plan_id(None)["properties"])

    def test_a_composed_root_is_wrapped_in_allof_and_stays_open(self) -> None:
        schema = with_plan_id(ANY_OF)
        self.assertEqual(schema["allOf"], [ANY_OF])
        self.assertEqual(set(schema["properties"]), {PLAN_ID_ARG})
        self.assertNotIn("additionalProperties", schema)

    def test_a_closed_composed_root_moves_the_closure_outside(self) -> None:
        schema = with_plan_id(ONE_OF_CLOSED)
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["properties"]), {"env", "q", PLAN_ID_ARG})
        self.assertNotIn("additionalProperties", schema["allOf"][0])

    def test_definitions_move_to_the_wrapper_so_references_resolve(self) -> None:
        self.assertIn("$defs", with_plan_id(REF_TO_DEFINITION))
        self.assertNotIn("$defs", with_plan_id(REF_TO_DEFINITION)["allOf"][0])

    def test_each_shape_accepts_the_plan_id_and_keeps_the_original_rules(self) -> None:
        for name, original in SHAPES.items():
            with self.subTest(name):
                wrapped = Draft202012Validator(with_plan_id(original))
                source = Draft202012Validator(original)
                self.assertTrue(wrapped.is_valid({"env": "stg", PLAN_ID_ARG: PLAN_ID}))
                self.assertFalse(wrapped.is_valid({"env": "stg", PLAN_ID_ARG: 5}))
                for instance in ({"env": "stg"}, {"env": "stg", "bogus": 1}, {}):
                    self.assertEqual(wrapped.is_valid(instance), source.is_valid(instance), instance)

    def test_the_original_schema_is_not_changed(self) -> None:
        for original in SHAPES.values():
            with self.subTest(original=original):
                before = copy.deepcopy(original)
                with_plan_id(original)
                self.assertEqual(original, before)


NESTED = {
    "type": "object",
    "properties": {"filters": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    "required": ["env"],
}


class ValidateArgsTest(unittest.TestCase):
    def test_a_wrong_nested_type_gives_its_dotted_path(self) -> None:
        self.assertEqual(validate_args(NESTED, {"env": "stg", "filters": {"limit": "ten"}}), ["filters.limit"])

    def test_a_missing_required_key_gives_the_root(self) -> None:
        self.assertEqual(validate_args(NESTED, {"filters": {"limit": 5}}), ["<root>"])

    def test_an_array_item_gives_its_index(self) -> None:
        schema = {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}}
        self.assertEqual(validate_args(schema, {"ids": [1, "x"]}), ["ids.1"])

    def test_several_errors_are_all_reported(self) -> None:
        paths = validate_args(NESTED, {"filters": {"limit": "ten"}})
        self.assertEqual(sorted(paths), ["<root>", "filters.limit"])

    def test_a_valid_argument_gives_no_paths(self) -> None:
        self.assertEqual(validate_args(NESTED, {"env": "stg", "filters": {"limit": 5}}), [])

    def test_a_broken_schema_gives_no_paths(self) -> None:
        self.assertEqual(validate_args({"type": 5}, {"env": "stg"}), [])

    def test_no_schema_gives_no_paths(self) -> None:
        self.assertEqual(validate_args(None, {"env": "stg"}), [])


class ValidateArgsNeverRaisesTest(unittest.TestCase):
    def test_an_unresolvable_reference_gives_no_paths(self) -> None:
        self.assertEqual(validate_args({"$ref": "#/$defs/Missing"}, {"env": "stg"}), [])

    def test_a_schema_that_is_not_an_object_gives_no_paths(self) -> None:
        self.assertEqual(validate_args(["not", "a", "schema"], {"env": "stg"}), [])

    def test_arguments_that_are_not_an_object_give_the_root_path(self) -> None:
        schema = {"type": "object", "properties": {"env": {"type": "string"}}}
        self.assertEqual(validate_args(schema, ["stg"]), ["<root>"])

    def test_the_schema_is_checked_once_for_repeated_calls(self) -> None:
        schema = {"type": "object", "description": uuid.uuid4().hex, "properties": {"env": {"type": "string"}}}
        with mock.patch.object(Draft202012Validator, "check_schema", wraps=Draft202012Validator.check_schema) as check:
            first = validate_args(schema, {"env": "stg"})
            second = validate_args(schema, {"env": 5})
        self.assertEqual((first, second), ([], ["env"]))
        check.assert_called_once()


if __name__ == "__main__":
    unittest.main()
