from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import bot.storage.repository as repository_module
from bot.db import SCHEMA_SQL
from bot.storage import Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA_SQL)
        self.repository = Repository(self.connection, Path(":memory:"), 600, "kick", 0)

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

    def test_active_private_challenge_stays_bound_to_selected_task(self) -> None:
        first = self.repository.create_challenge(
            chat_id=1,
            user_id=7,
            user_chat_instance=None,
            join_message_id=10,
            start_token="token-a",
            challenge_text="river 42 maple",
            expected_response="MAPLE-42-RIVER",
            timeout_seconds=600,
        )
        second = self.repository.create_challenge(
            chat_id=2,
            user_id=7,
            user_chat_instance=None,
            join_message_id=11,
            start_token="token-b",
            challenge_text="ember 33 pine",
            expected_response="PINE-33-EMBER",
            timeout_seconds=600,
        )

        self.repository.set_active_private_challenge(7, first.id)

        active = self.repository.get_active_private_challenge_for_user(7)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.id, first.id)
        self.assertEqual(self.repository.count_pending_challenges_for_user(7), 2)
        self.assertEqual(second.user_id, 7)

    def test_record_user_seen_only_counts_group_messages_when_requested(self) -> None:
        profile = self.repository.record_user_seen(88, "alice", "Alice", seen_at="2026-06-06T08:00:00+00:00")
        self.assertEqual(profile.total_messages, 0)

        profile = self.repository.record_user_seen(
            88,
            "alice",
            "Alice",
            seen_at="2026-06-06T08:01:00+00:00",
            count_message=True,
        )
        self.assertEqual(profile.total_messages, 1)

        profile = self.repository.record_user_seen(88, "alice", "Alice", seen_at="2026-06-06T08:02:00+00:00")
        self.assertEqual(profile.total_messages, 1)

    def test_get_verification_stats_uses_utc_day_boundary(self) -> None:
        fixed_now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
        self.connection.execute(
            """
            INSERT INTO verification_challenges (
                chat_id, user_id, user_chat_instance, join_message_id, prompt_message_id,
                status, start_token, challenge_text, expected_response, attempt_count,
                expires_at, passed_at, invalidated_at, created_at, updated_at
            ) VALUES (?, ?, NULL, NULL, NULL, 'passed', ?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                1,
                101,
                "today-token",
                "river 42 maple",
                "MAPLE-42-RIVER",
                (fixed_now + timedelta(minutes=10)).isoformat(),
                "2026-06-06T00:05:00+00:00",
                "2026-06-06T00:05:00+00:00",
                "2026-06-06T00:00:30+00:00",
                "2026-06-06T00:05:00+00:00",
            ),
        )
        self.connection.execute(
            """
            INSERT INTO verification_challenges (
                chat_id, user_id, user_chat_instance, join_message_id, prompt_message_id,
                status, start_token, challenge_text, expected_response, attempt_count,
                expires_at, passed_at, invalidated_at, created_at, updated_at
            ) VALUES (?, ?, NULL, NULL, NULL, 'passed', ?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                1,
                102,
                "yesterday-token",
                "ember 33 pine",
                "PINE-33-EMBER",
                fixed_now.isoformat(),
                "2026-06-05T23:59:59+00:00",
                "2026-06-05T23:59:59+00:00",
                "2026-06-05T23:50:00+00:00",
                "2026-06-05T23:59:59+00:00",
            ),
        )
        self.connection.commit()

        with patch.object(repository_module, "utc_now", return_value=fixed_now):
            stats = self.repository.get_verification_stats()

        self.assertEqual(stats.total, 2)
        self.assertEqual(stats.today, 1)

    def test_list_configurable_groups_includes_preconfigured_group_without_profile(self) -> None:
        self.repository.ensure_group_settings(-100123)

        groups = self.repository.list_configurable_groups()

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].chat_id, -100123)
        self.assertFalse(groups[0].tracked)

    def test_group_alias_can_be_set_and_cleared(self) -> None:
        self.repository.ensure_group_settings(-100456)

        self.repository.set_group_alias(-100456, "业务群")
        self.assertEqual(self.repository.get_group_alias(-100456), "业务群")

        self.repository.set_group_alias(-100456, None)
        self.assertIsNone(self.repository.get_group_alias(-100456))

    def test_touch_group_profile_auto_creates_group_settings(self) -> None:
        profile = self.repository.touch_group_profile(
            -100789,
            title="Auto Group",
            member_count=12,
            admin_count=2,
        )

        settings = self.repository.get_group_settings(-100789)

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(profile.chat_id, -100789)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.timeout_seconds, 600)

    def test_group_summaries_include_aliases(self) -> None:
        self.repository.ensure_group_settings(-100321)
        self.repository.set_group_alias(-100321, "Business Group")
        self.repository.touch_group_profile(
            -100321,
            title="Original Group",
            member_count=42,
            admin_count=3,
        )

        config = self.repository.get_configurable_group(-100321)
        tracked = self.repository.get_group_summary(-100321)
        groups = self.repository.list_groups()

        self.assertIsNotNone(config)
        self.assertIsNotNone(tracked)
        assert config is not None
        assert tracked is not None
        self.assertEqual(config.alias, "Business Group")
        self.assertEqual(tracked.alias, "Business Group")
        self.assertEqual(groups[0].alias, "Business Group")

    def test_owner_dashboard_summary_aggregates_core_counts(self) -> None:
        fixed_now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
        self.repository.ensure_group_settings(-100111)
        self.repository.ensure_group_settings(-100222)
        self.repository.touch_group_profile(
            -100111,
            title="Tracked Group",
            member_count=10,
            admin_count=2,
        )
        self.repository.record_user_seen(
            1,
            "alice",
            "Alice",
            seen_at=(fixed_now - timedelta(days=1)).isoformat(),
        )
        self.repository.record_user_seen(
            2,
            "bob",
            "Bob",
            seen_at=(fixed_now - timedelta(days=9)).isoformat(),
        )
        self.repository.create_challenge(
            chat_id=-100111,
            user_id=1,
            user_chat_instance=None,
            join_message_id=1,
            start_token="summary-token",
            challenge_text="river 42 maple",
            expected_response="MAPLE-42-RIVER",
            timeout_seconds=600,
        )

        with patch.object(repository_module, "utc_now", return_value=fixed_now):
            summary = self.repository.get_owner_dashboard_summary()

        self.assertEqual(summary.tracked_groups, 1)
        self.assertEqual(summary.configurable_groups, 2)
        self.assertEqual(summary.users, 2)
        self.assertEqual(summary.active_users, 1)
        self.assertEqual(summary.verification_stats.total, 1)
