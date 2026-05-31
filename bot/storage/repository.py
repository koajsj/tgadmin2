from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from bot.models import AuditLogRecord, GroupSettingsRecord, VerificationChallenge


class Repository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        default_timeout: int,
        default_expire_action: str,
        default_auto_delete_seconds: int,
    ):
        self._connection = connection
        self._lock = threading.RLock()
        self._default_timeout = default_timeout
        self._default_expire_action = default_expire_action
        self._default_auto_delete_seconds = default_auto_delete_seconds

    def ensure_group_settings(self, chat_id: int) -> GroupSettingsRecord:
        with self._lock:
            current = self.get_group_settings(chat_id)
            if current:
                return current
            now = utc_now().isoformat()
            self._connection.execute(
                """
                INSERT INTO group_settings (
                    chat_id, enabled, timeout_seconds, expire_action, auto_delete_seconds, created_at, updated_at
                )
                VALUES (?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    self._default_timeout,
                    self._default_expire_action,
                    self._default_auto_delete_seconds,
                    now,
                    now,
                ),
            )
            self._connection.commit()
            return self.get_group_settings(chat_id)

    def get_group_settings(self, chat_id: int) -> GroupSettingsRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM group_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return _row_to_group(row) if row else None

    def update_group_settings(
        self,
        chat_id: int,
        *,
        enabled: bool | None = None,
        timeout_seconds: int | None = None,
        expire_action: str | None = None,
        auto_delete_seconds: int | None = None,
    ) -> GroupSettingsRecord:
        with self._lock:
            current = self.ensure_group_settings(chat_id)
            next_enabled = current.enabled if enabled is None else enabled
            next_timeout = current.timeout_seconds if timeout_seconds is None else timeout_seconds
            next_action = current.expire_action if expire_action is None else expire_action
            next_auto_delete = (
                current.auto_delete_seconds
                if auto_delete_seconds is None
                else auto_delete_seconds
            )
            now = utc_now().isoformat()
            self._connection.execute(
                """
                UPDATE group_settings
                SET enabled = ?, timeout_seconds = ?, expire_action = ?, auto_delete_seconds = ?, updated_at = ?
                WHERE chat_id = ?
                """,
                (1 if next_enabled else 0, next_timeout, next_action, next_auto_delete, now, chat_id),
            )
            self._connection.commit()
            return self.get_group_settings(chat_id)

    def get_active_challenge(self, chat_id: int, user_id: int, now: datetime | None = None) -> VerificationChallenge | None:
        now_value = (now or utc_now()).isoformat()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM verification_challenges
                WHERE chat_id = ? AND user_id = ? AND status = 'pending' AND expires_at > ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id, user_id, now_value),
            ).fetchone()
            return _row_to_challenge(row) if row else None

    def get_pending_challenge_by_token(self, start_token: str) -> VerificationChallenge | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM verification_challenges WHERE start_token = ?",
                (start_token,),
            ).fetchone()
            return _row_to_challenge(row) if row else None

    def get_pending_challenge_for_user(self, user_id: int) -> VerificationChallenge | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM verification_challenges
                WHERE user_id = ? AND status = 'pending'
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return _row_to_challenge(row) if row else None

    def create_challenge(
        self,
        *,
        chat_id: int,
        user_id: int,
        user_chat_instance: str | None,
        join_message_id: int | None,
        start_token: str,
        challenge_text: str,
        expected_response: str,
        timeout_seconds: int,
    ) -> VerificationChallenge:
        now = utc_now()
        expires_at = now + timedelta(seconds=timeout_seconds)
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO verification_challenges (
                    chat_id, user_id, user_chat_instance, join_message_id, prompt_message_id,
                    status, start_token, challenge_text, expected_response, attempt_count,
                    expires_at, passed_at, invalidated_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, 'pending', ?, ?, ?, 0, ?, NULL, NULL, ?, ?)
                """,
                (
                    chat_id,
                    user_id,
                    user_chat_instance,
                    join_message_id,
                    start_token,
                    challenge_text,
                    expected_response,
                    expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._connection.commit()
            challenge_id = self._connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            return self.get_challenge_by_id(challenge_id)

    def get_challenge_by_id(self, challenge_id: int) -> VerificationChallenge:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM verification_challenges WHERE id = ?",
                (challenge_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"challenge {challenge_id} not found")
            return _row_to_challenge(row)

    def set_prompt_message_id(self, challenge_id: int, prompt_message_id: int) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE verification_challenges
                SET prompt_message_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (prompt_message_id, utc_now().isoformat(), challenge_id),
            )
            self._connection.commit()

    def set_user_chat_instance(self, challenge_id: int, user_chat_instance: str | None) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE verification_challenges
                SET user_chat_instance = ?, updated_at = ?
                WHERE id = ?
                """,
                (user_chat_instance, utc_now().isoformat(), challenge_id),
            )
            self._connection.commit()

    def increment_attempt_count(self, challenge_id: int) -> int:
        with self._lock:
            self._connection.execute(
                """
                UPDATE verification_challenges
                SET attempt_count = attempt_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (utc_now().isoformat(), challenge_id),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT attempt_count FROM verification_challenges WHERE id = ?",
                (challenge_id,),
            ).fetchone()
            return int(row["attempt_count"])

    def mark_passed(self, challenge_id: int) -> VerificationChallenge:
        now = utc_now().isoformat()
        with self._lock:
            self._connection.execute(
                """
                UPDATE verification_challenges
                SET status = 'passed', passed_at = ?, invalidated_at = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, now, now, challenge_id),
            )
            self._connection.commit()
            return self.get_challenge_by_id(challenge_id)

    def mark_expired(self, challenge_id: int) -> VerificationChallenge:
        now = utc_now().isoformat()
        with self._lock:
            self._connection.execute(
                """
                UPDATE verification_challenges
                SET status = 'expired', invalidated_at = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, now, challenge_id),
            )
            self._connection.commit()
            return self.get_challenge_by_id(challenge_id)

    def mark_cancelled(self, challenge_id: int) -> VerificationChallenge:
        now = utc_now().isoformat()
        with self._lock:
            self._connection.execute(
                """
                UPDATE verification_challenges
                SET status = 'cancelled', invalidated_at = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, now, challenge_id),
            )
            self._connection.commit()
            return self.get_challenge_by_id(challenge_id)

    def mark_failed(self, challenge_id: int) -> VerificationChallenge:
        now = utc_now().isoformat()
        with self._lock:
            self._connection.execute(
                """
                UPDATE verification_challenges
                SET status = 'failed', invalidated_at = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, now, challenge_id),
            )
            self._connection.commit()
            return self.get_challenge_by_id(challenge_id)

    def list_expired_pending_challenges(self, now: datetime | None = None, limit: int = 100) -> list[VerificationChallenge]:
        now_value = (now or utc_now()).isoformat()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM verification_challenges
                WHERE status = 'pending' AND expires_at <= ?
                ORDER BY expires_at ASC
                LIMIT ?
                """,
                (now_value, limit),
            ).fetchall()
            return [_row_to_challenge(row) for row in rows]

    def count_pending_challenges(self, chat_id: int) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS count FROM verification_challenges
                WHERE chat_id = ? AND status = 'pending'
                """,
                (chat_id,),
            ).fetchone()
            return int(row["count"])

    def find_pending_challenge(self, chat_id: int, user_id: int) -> VerificationChallenge | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM verification_challenges
                WHERE chat_id = ? AND user_id = ? AND status = 'pending'
                ORDER BY id DESC LIMIT 1
                """,
                (chat_id, user_id),
            ).fetchone()
            return _row_to_challenge(row) if row else None

    def append_audit_log(
        self, action: str, *, chat_id: int | None, user_id: int | None, details: dict[str, Any]
    ) -> AuditLogRecord:
        now = utc_now().isoformat()
        payload = json.dumps(details, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO audit_logs (chat_id, user_id, action, details_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, user_id, action, payload, now),
            )
            self._connection.commit()
            log_id = self._connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            row = self._connection.execute("SELECT * FROM audit_logs WHERE id = ?", (log_id,)).fetchone()
            return _row_to_audit(row)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_group(row: sqlite3.Row) -> GroupSettingsRecord:
    return GroupSettingsRecord(
        chat_id=int(row["chat_id"]),
        enabled=bool(row["enabled"]),
        timeout_seconds=int(row["timeout_seconds"]),
        expire_action=str(row["expire_action"]),
        auto_delete_seconds=int(row["auto_delete_seconds"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_challenge(row: sqlite3.Row) -> VerificationChallenge:
    return VerificationChallenge(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        user_id=int(row["user_id"]),
        user_chat_instance=row["user_chat_instance"],
        join_message_id=row["join_message_id"],
        prompt_message_id=row["prompt_message_id"],
        status=str(row["status"]),
        start_token=str(row["start_token"]),
        challenge_text=str(row["challenge_text"]),
        expected_response=str(row["expected_response"]),
        attempt_count=int(row["attempt_count"]),
        expires_at=str(row["expires_at"]),
        passed_at=row["passed_at"],
        invalidated_at=row["invalidated_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_audit(row: sqlite3.Row) -> AuditLogRecord:
    return AuditLogRecord(
        id=int(row["id"]),
        chat_id=row["chat_id"],
        user_id=row["user_id"],
        action=str(row["action"]),
        details_json=str(row["details_json"]),
        created_at=str(row["created_at"]),
    )
