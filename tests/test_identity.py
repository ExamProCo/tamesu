import unittest

from tamesu.identity import digest_value


class IdentityTests(unittest.TestCase):
    def test_digest_value_ignores_mapping_order(self) -> None:
        self.assertEqual(
            digest_value({"a": 1, "b": {"c": 2}}),
            digest_value({"b": {"c": 2}, "a": 1}),
        )

    def test_digest_value_changes_with_content(self) -> None:
        self.assertNotEqual(digest_value({"a": 1}), digest_value({"a": 2}))
