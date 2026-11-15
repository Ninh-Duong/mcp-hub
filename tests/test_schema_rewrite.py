"""Schema rewrites: the `hub_plan_id` argument the Hub adds to every published tool, and argument validation."""
from __future__ import annotations

import copy
import unittest
from typing import Any

from jsonschema import Draft202012Validator

from mcp_hub.schema_rewrite import PLAN_ID_ARG, with_plan_id

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


if __name__ == "__main__":
    unittest.main()
