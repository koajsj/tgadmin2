from __future__ import annotations

import sqlite3
import unittest

from bot.db import SCHEMA_SQL
from bot.storage import Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA_SQL)
        self.repository = Repository(self.connection, 600, "kick")

    def tearDown(self) -> None:
        self.connection.close()

    def test_create_single_active_challenge_per_user(self) -> None:
        challenge = self.repository.create_challenge(
            chat_id=1,
            user_id=2,
            user_chat_instance=None,
            join_message_id=10,
            start_token="token-1",
            challenge_text="river 42 maple",
            expected_response="MAPLE-42-RIVER",
            timeout_seconds=600,
        )
        self.assertEqual(challenge.status, "pending")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.create_challenge(
                chat_id=1,
                user_id=2,
                user_chat_instance=None,
                join_message_id=11,
                start_token="token-2",
                challenge_text="ember 33 pine",
                expected_response="PINE-33-EMBER",
                timeout_seconds=600,
            )

    def test_mark_passed_updates_state(self) -> None:
        challenge = self.repository.create_challenge(
            chat_id=1,
            user_id=2,
            user_chat_instance=None,
            join_message_id=10,
            start_token="token-1",
            challenge_text="river 42 maple",
            expected_response="MAPLE-42-RIVER",
            timeout_seconds=600,
        )
        updated = self.repository.mark_passed(challenge.id)
        self.assertEqual(updated.status, "passed")
        self.assertIsNotNone(updated.passed_at)
