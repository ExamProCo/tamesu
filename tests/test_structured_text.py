import unittest
from pathlib import Path

from tamesu.errors import ConfigError
from tamesu.scoring import score_output
from tamesu.tasks.structured_text import render_template, validate_json_schema


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["label", "flag"],
    "properties": {
        "label": {"type": "string", "enum": ["a", "b"]},
        "flag": {"type": "boolean"},
    },
}


class StructuredTextTests(unittest.TestCase):
    def test_render_template_resolves_item_values(self) -> None:
        self.assertEqual(
            render_template(
                "Ticket: {{ item.input.message }}",
                {"item": {"input": {"message": "Hi"}}},
            ),
            "Ticket: Hi",
        )

    def test_render_template_rejects_unknown_expression(self) -> None:
        with self.assertRaises(ConfigError):
            render_template("{{ item.missing }}", {"item": {}}, Path("prompt.md"))

    def test_schema_validation_rejects_extra_and_invalid_fields(self) -> None:
        errors = validate_json_schema({"label": "c", "flag": "yes", "extra": 1}, SCHEMA)
        self.assertIn("output.extra is not allowed", errors)
        self.assertTrue(any("output.label" in error for error in errors))
        self.assertTrue(any("output.flag" in error for error in errors))

    def test_score_output_compares_complete_record(self) -> None:
        parsed, scores = score_output(
            '{"label":"a","flag":true}',
            expected={"label": "a", "flag": True},
            output_schema=SCHEMA,
        )
        self.assertEqual(parsed, {"label": "a", "flag": True})
        self.assertIs(scores["valid_json"], True)
        self.assertIs(scores["schema_valid"], True)
        self.assertIs(scores["exact_match"], True)
