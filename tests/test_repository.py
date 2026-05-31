from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from bot.db import SCHEMA_SQL
from bot.storage import Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA_SQL)
        self.repository = Repository(self.connection, 600, "kick", 0)

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

    def test_ensure_group_settings_uses_default_auto_delete(self) -> None:
        settings = self.repository.ensure_group_settings(123)
        self.assertEqual(settings.auto_delete_seconds, 0)

    def test_mark_failed_updates_state(self) -> None:
        challenge = self.repository.create_challenge(
            chat_id=1,
            user_id=5,
            user_chat_instance=None,
            join_message_id=12,
            start_token="token-failed",
            challenge_text="river 42 maple",
            expected_response="MAPLE-42-RIVER",
            timeout_seconds=600,
        )
        updated = self.repository.mark_failed(challenge.id)
        self.assertEqual(updated.status, "failed")

    def test_get_pending_challenge_for_user_prefers_latest_updated(self) -> None:
        now = datetime.now(timezone.utc)
        self.connection.execute(
            """
            INSERT INTO verification_challenges (
                chat_id, user_id, user_chat_instance, join_message_id, prompt_message_id,
                status, start_token, challenge_text, expected_response, attempt_count,
                expires_at, passed_at, invalidated_at, created_at, updated_at
            ) VALUES (?, ?, NULL, NULL, NULL, 'pending', ?, ?, ?, 0, ?, NULL, NULL, ?, ?)
            """,
            (
                1,
                9,
                "older-token",
                "river 42 maple",
                "MAPLE-42-RIVER",
                (now + timedelta(minutes=10)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO verification_challenges (
                chat_id, user_id, user_chat_instance, join_message_id, prompt_message_id,
                status, start_token, challenge_text, expected_response, attempt_count,
                expires_at, passed_at, invalidated_at, created_at, updated_at
            ) VALUES (?, ?, NULL, NULL, NULL, 'pending', ?, ?, ?, 0, ?, NULL, NULL, ?, ?)
            """,
            (
                2,
                9,
                "newer-token",
                "ember 33 pine",
                "PINE-33-EMBER",
                (now + timedelta(minutes=10)).isoformat(),
                now.isoformat(),
                (now + timedelta(seconds=5)).isoformat(),
            ),
        )
        self.connection.commit()

        challenge = self.repository.get_pending_challenge_for_user(9)
        self.assertIsNotNone(challenge)
        self.assertEqual(challenge.start_token, "newer-token")
