from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from bot.config import Settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.previous)

    def test_from_env_reads_max_failed_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["BOT_TOKEN"] = "123:test"
            os.environ["DB_PATH"] = str(Path(temp_dir) / "bot.sqlite3")
            os.environ["MAX_FAILED_ATTEMPTS"] = "4"
            settings = Settings.from_env()
            self.assertEqual(settings.max_failed_attempts, 4)

    def test_from_env_requires_bot_token(self) -> None:
        os.environ.pop("BOT_TOKEN", None)
        with self.assertRaises(ValueError):
            Settings.from_env()
