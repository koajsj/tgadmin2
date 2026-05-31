from __future__ import annotations

import unittest

from bot.command_parsers import parse_auto_delete_command, parse_timeout_command


class TimeoutCommandTests(unittest.TestCase):
    def test_parse_timeout_success(self) -> None:
        value, error = parse_timeout_command("/set_timeout 300")
        self.assertEqual(value, 300)
        self.assertIsNone(error)

    def test_parse_timeout_invalid_range(self) -> None:
        value, error = parse_timeout_command("/set_timeout 30")
        self.assertIsNone(value)
        self.assertIn("60", error or "")

    def test_parse_timeout_invalid_integer(self) -> None:
        value, error = parse_timeout_command("/set_timeout abc")
        self.assertIsNone(value)
        self.assertIsNotNone(error)

    def test_parse_auto_delete_success(self) -> None:
        value, error = parse_auto_delete_command("/set_autodelete 45")
        self.assertEqual(value, 45)
        self.assertIsNone(error)

    def test_parse_auto_delete_invalid_range(self) -> None:
        value, error = parse_auto_delete_command("/set_autodelete 90000")
        self.assertIsNone(value)
        self.assertIn("86400", error or "")
