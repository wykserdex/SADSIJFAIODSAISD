import os
import unittest

# config.py требует BOT_TOKEN даже для импорта парсера.
os.environ.setdefault("BOT_TOKEN", "test-token")

from user_client import parse_chat_target, parse_lines_report


class TargetParserTests(unittest.TestCase):
    def test_supported_formats(self):
        self.assertEqual(parse_chat_target("@example"), ("username", "example"))
        self.assertEqual(parse_chat_target("https://t.me/example"), ("username", "example"))
        self.assertEqual(parse_chat_target("-100123"), ("id", -100123))
        self.assertEqual(parse_chat_target("https://t.me/+invite_hash"), ("invite", "invite_hash"))

    def test_comments_duplicates_and_invalid_lines(self):
        targets, duplicates, invalid = parse_lines_report(
            "# comment\n@example\n@example\nnot a target\n-100123\n"
        )
        self.assertEqual(targets, [("username", "example"), ("id", -100123)])
        self.assertEqual(duplicates, 1)
        self.assertEqual(invalid, 1)

    def test_empty_line_is_ignored(self):
        self.assertIsNone(parse_chat_target("   "))


if __name__ == "__main__":
    unittest.main()
