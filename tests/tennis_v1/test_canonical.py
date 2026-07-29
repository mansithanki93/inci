import unittest

from tennis_v1.canonical import CanonicalJsonError, canonical_json_bytes


class CanonicalJsonTests(unittest.TestCase):
    def test_rejects_float_values_at_the_hash_boundary(self) -> None:
        """A numeric value that could serialize differently must not be hashed."""
        with self.assertRaises(CanonicalJsonError):
            canonical_json_bytes({"limit": 1.0})

    def test_emits_one_sorted_ascii_safe_representation(self) -> None:
        """Equivalent mappings must have one stable byte sequence to hash."""
        self.assertEqual(
            canonical_json_bytes({"z": "é", "a": [True, None, 2]}),
            b'{"a":[true,null,2],"z":"\\u00e9"}',
        )

    def test_rejects_tuples_instead_of_silently_rewriting_them_as_lists(self) -> None:
        """Only JSON lists, not Python sequence lookalikes, are canonical input."""
        with self.assertRaises(CanonicalJsonError):
            canonical_json_bytes({"ids": ("reviewer-a",)})


if __name__ == "__main__":
    unittest.main()
