from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.db import SCHEMA_SQL
from bot.services.verification import build_challenge, normalize_response
from bot.storage import Repository


class VerificationTests(unittest.TestCase):
    def test_build_challenge_returns_expected_shape(self) -> None:
        challenge = build_challenge("Alice Example")
        self.assertEqual(len(challenge.display_text.split()), 3)
        self.assertIn("-", challenge.expected_response)

    def test_normalize_response(self) -> None:
        self.assertEqual(normalize_response(" maple-42-river "), "MAPLE-42-RIVER")
        self.assertEqual(normalize_response("maple-42-river"), "MAPLE-42-RIVER")

    def test_expired_lookup_returns_pending_item(self) -> None:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA_SQL)
        repository = Repository(connection, Path(":memory:"), 600, "kick", 0)
        now = datetime.now(timezone.utc) - timedelta(seconds=1)
        connection.execute(
            """
            INSERT INTO verification_challenges (
                chat_id, user_id, user_chat_instance, join_message_id, prompt_message_id,
                status, start_token, challenge_text, expected_response, attempt_count,
                expires_at, passed_at, invalidated_at, created_at, updated_at
            ) VALUES (1, 2, NULL, NULL, NULL, 'pending', 'token-1', 'river 42 maple',
                'MAPLE-42-RIVER', 0, ?, NULL, NULL, ?, ?)
            """,
            (now.isoformat(), now.isoformat(), now.isoformat()),
        )
        connection.commit()
        items = repository.list_expired_pending_challenges()
        self.assertEqual(len(items), 1)
        connection.close()
