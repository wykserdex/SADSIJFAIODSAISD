import unittest

from osint_tools import UnsafeTarget, normalize_public_url


class OsintSafetyTests(unittest.TestCase):
    def test_local_targets_are_rejected(self):
        with self.assertRaises(UnsafeTarget):
            normalize_public_url("http://127.0.0.1/")
        with self.assertRaises(UnsafeTarget):
            normalize_public_url("http://localhost/")

    def test_non_http_scheme_is_rejected(self):
        with self.assertRaises(UnsafeTarget):
            normalize_public_url("file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
