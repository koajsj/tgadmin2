from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bot.models import (
    AuditLogRecord,
    ConfigGroupSummary,
    DatabaseSnapshot,
    GitSnapshot,
    GroupProfileRecord,
    GroupSettingsRecord,
    GroupSummary,
    RuntimeSnapshot,
    UserProfileRecord,
    UserSummary,
    VerificationChallenge,
    VerificationStats,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS group_settings (
    chat_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    timeout_seconds INTEGER NOT NULL,
    expire_action TEXT NOT NULL,
    auto_delete_seconds INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    user_chat_instance TEXT,
    join_message_id INTEGER,
    prompt_message_id INTEGER,
    status TEXT NOT NULL,
    start_token TEXT NOT NULL UNIQUE,
    challenge_text TEXT NOT NULL,
    expected_response TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    passed_at TEXT,
    invalidated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_challenge_unique
ON verification_challenges(chat_id, user_id)
WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_challenges_token
ON verification_challenges(start_token);

CREATE INDEX IF NOT EXISTS idx_challenges_expiry
ON verification_challenges(status, expires_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    user_id INTEGER,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
ON audit_logs(created_at);

CREATE INDEX IF NOT EXISTS idx_audit_logs_action
ON audit_logs(action);

CREATE TABLE IF NOT EXISTS group_profiles (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    member_count INTEGER NOT NULL DEFAULT 0,
    admin_count INTEGER NOT NULL DEFAULT 0,
    verification_enabled INTEGER NOT NULL DEFAULT 1,
    auto_delete_seconds INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT,
    last_active_at TEXT,
    risk_level TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_group_profiles_last_active_at
ON group_profiles(last_active_at);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    joined_at TEXT,
    banned_at TEXT,
    is_banned INTEGER NOT NULL DEFAULT 0,
    total_messages INTEGER NOT NULL DEFAULT 0,
    verification_successes INTEGER NOT NULL DEFAULT 0,
    verification_failures INTEGER NOT NULL DEFAULT 0,
    last_verification_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_last_seen_at
ON user_profiles(last_seen_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Repository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        db_path: Path,
        default_timeout: int,
        default_expire_action: str,
        default_auto_delete_seconds: int,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._connection = connection
        self._db_path = db_path
        self._lock = threading.RLock()
        self._default_timeout = default_timeout
        self._default_expire_action = default_expire_action
        self._default_auto_delete_seconds = default_auto_delete_seconds
        if initialize_schema:
            self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._connection.executescript(SCHEMA_SQL)
            self._ensure_group_settings_columns()
            self._connection.commit()

    def _ensure_group_settings_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(group_settings)").fetchall()
        }
        if "auto_delete_seconds" not in columns:
            self._connection.execute(
                "ALTER TABLE group_settings ADD COLUMN auto_delete_seconds INTEGER NOT NULL DEFAULT 0"
            )

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

    def count_configurable_groups(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM group_settings").fetchone()
            return int(row["count"])

    def list_configurable_groups(self, limit: int = 20, offset: int = 0) -> list[ConfigGroupSummary]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT
                    gs.chat_id,
                    gs.enabled,
                    gs.timeout_seconds,
                    gs.expire_action,
                    gs.auto_delete_seconds,
                    gs.updated_at,
                    gp.title,
                    gp.last_active_at,
                    gp.chat_id AS profile_chat_id
                FROM group_settings gs
                LEFT JOIN group_profiles gp ON gp.chat_id = gs.chat_id
                ORDER BY COALESCE(gp.last_active_at, gs.updated_at) DESC, gs.chat_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [_row_to_config_group(row) for row in rows]

    def get_configurable_group(self, chat_id: int) -> ConfigGroupSummary | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT
                    gs.chat_id,
                    gs.enabled,
                    gs.timeout_seconds,
                    gs.expire_action,
                    gs.auto_delete_seconds,
                    gs.updated_at,
                    gp.title,
                    gp.last_active_at,
                    gp.chat_id AS profile_chat_id
                FROM group_settings gs
                LEFT JOIN group_profiles gp ON gp.chat_id = gs.chat_id
                WHERE gs.chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
            return _row_to_config_group(row) if row else None

    def set_group_alias(self, chat_id: int, alias: str | None) -> None:
        key = f"group_alias:{chat_id}"
        normalized = (alias or "").strip()
        if normalized:
            self.set_app_setting(key, normalized)
        else:
            self.delete_app_setting(key)

    def get_group_alias(self, chat_id: int) -> str | None:
        raw = self.get_app_setting(f"group_alias:{chat_id}")
        return raw.strip() if raw else None

    def get_active_challenge(
        self, chat_id: int, user_id: int, now: datetime | None = None
    ) -> VerificationChallenge | None:
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

    def count_pending_challenges_for_user(self, user_id: int) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM verification_challenges
                WHERE user_id = ? AND status = 'pending'
                """,
                (user_id,),
            ).fetchone()
            return int(row["count"])

    def set_active_private_challenge(self, user_id: int, challenge_id: int) -> None:
        self.set_app_setting(f"active_private_challenge:{user_id}", str(challenge_id))

    def clear_active_private_challenge(self, user_id: int) -> None:
        self.delete_app_setting(f"active_private_challenge:{user_id}")

    def get_active_private_challenge_for_user(self, user_id: int) -> VerificationChallenge | None:
        raw = self.get_app_setting(f"active_private_challenge:{user_id}")
        if not raw:
            return None
        try:
            challenge_id = int(raw)
        except ValueError:
            self.clear_active_private_challenge(user_id)
            return None
        try:
            challenge = self.get_challenge_by_id(challenge_id)
        except KeyError:
            self.clear_active_private_challenge(user_id)
            return None
        if challenge.user_id != user_id:
            self.clear_active_private_challenge(user_id)
            return None
        if challenge.status != "pending" or challenge.expires_at_dt() <= utc_now():
            self.clear_active_private_challenge(user_id)
            return None
        return challenge

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

    def list_expired_pending_challenges(
        self, now: datetime | None = None, limit: int = 100
    ) -> list[VerificationChallenge]:
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

    def list_audit_logs(self, limit: int = 20, offset: int = 0) -> list[AuditLogRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM audit_logs
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [_row_to_audit(row) for row in rows]

    def count_audit_logs(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM audit_logs").fetchone()
            return int(row["count"])

    def count_recent_errors(self, hours: int = 24) -> int:
        since = (utc_now() - timedelta(hours=hours)).isoformat()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM audit_logs
                WHERE created_at >= ?
                  AND (action LIKE '%failed%' OR action LIKE '%error%' OR action LIKE '%exception%')
                """,
                (since,),
            ).fetchone()
            return int(row["count"])

    def count_recent_restarts(self, days: int = 7) -> int:
        since = (utc_now() - timedelta(days=days)).isoformat()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM audit_logs
                WHERE created_at >= ?
                  AND action IN ('service_restarted', 'update_completed', 'group_restart_requested')
                """,
                (since,),
            ).fetchone()
            return int(row["count"])

    def upsert_group_profile(
        self,
        *,
        chat_id: int,
        title: str,
        member_count: int,
        admin_count: int,
        verification_enabled: bool,
        auto_delete_seconds: int,
        joined_at: str | None = None,
        last_active_at: str | None = None,
        risk_level: str | None = None,
    ) -> GroupProfileRecord:
        now = utc_now().isoformat()
        with self._lock:
            existing = self.get_group_profile(chat_id)
            created_at = existing.created_at if existing else now
            joined_value = joined_at if joined_at is not None else (existing.joined_at if existing else now)
            last_active_value = last_active_at if last_active_at is not None else (existing.last_active_at if existing else now)
            risk_value = risk_level if risk_level is not None else _group_risk_level(
                member_count=member_count,
                admin_count=admin_count,
                verification_enabled=verification_enabled,
                auto_delete_seconds=auto_delete_seconds,
            )
            self._connection.execute(
                """
                INSERT INTO group_profiles (
                    chat_id, title, member_count, admin_count, verification_enabled,
                    auto_delete_seconds, joined_at, last_active_at, risk_level, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    member_count = excluded.member_count,
                    admin_count = excluded.admin_count,
                    verification_enabled = excluded.verification_enabled,
                    auto_delete_seconds = excluded.auto_delete_seconds,
                    joined_at = COALESCE(group_profiles.joined_at, excluded.joined_at),
                    last_active_at = excluded.last_active_at,
                    risk_level = excluded.risk_level,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    title,
                    member_count,
                    admin_count,
                    1 if verification_enabled else 0,
                    auto_delete_seconds,
                    joined_value,
                    last_active_value,
                    risk_value,
                    created_at,
                    now,
                ),
            )
            self._connection.commit()
            return self.get_group_profile(chat_id)

    def touch_group_profile(
        self,
        chat_id: int,
        *,
        title: str | None = None,
        member_count: int | None = None,
        admin_count: int | None = None,
        verification_enabled: bool | None = None,
        auto_delete_seconds: int | None = None,
        last_active_at: str | None = None,
        risk_level: str | None = None,
    ) -> GroupProfileRecord:
        current = self.get_group_profile(chat_id)
        title_value = title if title is not None else (current.title if current else "")
        member_value = member_count if member_count is not None else (current.member_count if current else 0)
        admin_value = admin_count if admin_count is not None else (current.admin_count if current else 0)
        enabled_value = (
            verification_enabled if verification_enabled is not None else (current.verification_enabled if current else True)
        )
        auto_delete_value = (
            auto_delete_seconds if auto_delete_seconds is not None else (current.auto_delete_seconds if current else 0)
        )
        joined_value = current.joined_at if current else None
        return self.upsert_group_profile(
            chat_id=chat_id,
            title=title_value,
            member_count=member_value,
            admin_count=admin_value,
            verification_enabled=enabled_value,
            auto_delete_seconds=auto_delete_value,
            joined_at=joined_value,
            last_active_at=last_active_at or utc_now().isoformat(),
            risk_level=risk_level,
        )

    def get_group_profile(self, chat_id: int) -> GroupProfileRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM group_profiles WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return _row_to_group_profile(row) if row else None

    def list_groups(self, limit: int = 20, offset: int = 0) -> list[GroupSummary]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT gp.*, gs.enabled AS settings_enabled, gs.auto_delete_seconds AS settings_auto_delete
                FROM group_profiles gp
                LEFT JOIN group_settings gs ON gs.chat_id = gp.chat_id
                ORDER BY COALESCE(gp.last_active_at, gp.created_at) DESC, gp.chat_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            summaries: list[GroupSummary] = []
            for row in rows:
                summaries.append(
                    GroupSummary(
                        chat_id=int(row["chat_id"]),
                        title=str(row["title"]),
                        member_count=int(row["member_count"]),
                        admin_count=int(row["admin_count"]),
                        verification_enabled=bool(row["settings_enabled"] if row["settings_enabled"] is not None else row["verification_enabled"]),
                        auto_delete_seconds=int(row["settings_auto_delete"] if row["settings_auto_delete"] is not None else row["auto_delete_seconds"]),
                        joined_at=row["joined_at"],
                        last_active_at=row["last_active_at"],
                        risk_level=str(row["risk_level"]),
                    )
                )
            return summaries

    def count_groups(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM group_profiles").fetchone()
            return int(row["count"])

    def get_latest_group_chat_id(self) -> int | None:
        group = self.list_groups(limit=1, offset=0)
        if not group:
            return None
        return group[0].chat_id

    def sum_group_member_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(SUM(member_count), 0) AS total FROM group_profiles"
            ).fetchone()
            return int(row["total"])

    def sum_group_admin_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(SUM(admin_count), 0) AS total FROM group_profiles"
            ).fetchone()
            return int(row["total"])

    def get_group_summary(self, chat_id: int) -> GroupSummary | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT gp.*, gs.enabled AS settings_enabled, gs.auto_delete_seconds AS settings_auto_delete
                FROM group_profiles gp
                LEFT JOIN group_settings gs ON gs.chat_id = gp.chat_id
                WHERE gp.chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
            if not row:
                return None
            return GroupSummary(
                chat_id=int(row["chat_id"]),
                title=str(row["title"]),
                member_count=int(row["member_count"]),
                admin_count=int(row["admin_count"]),
                verification_enabled=bool(row["settings_enabled"] if row["settings_enabled"] is not None else row["verification_enabled"]),
                auto_delete_seconds=int(row["settings_auto_delete"] if row["settings_auto_delete"] is not None else row["auto_delete_seconds"]),
                joined_at=row["joined_at"],
                last_active_at=row["last_active_at"],
                risk_level=str(row["risk_level"]),
            )

    def upsert_user_profile(
        self,
        *,
        user_id: int,
        username: str | None,
        full_name: str,
        seen_at: str | None = None,
        joined_at: str | None = None,
        banned_at: str | None = None,
        is_banned: bool | None = None,
        total_messages: int | None = None,
        verification_successes: int | None = None,
        verification_failures: int | None = None,
        last_verification_at: str | None = None,
    ) -> UserProfileRecord:
        now = utc_now().isoformat()
        with self._lock:
            existing = self.get_user_profile(user_id)
            first_seen = existing.first_seen_at if existing else (seen_at or now)
            last_seen = seen_at or now
            joined_value = joined_at if joined_at is not None else (existing.joined_at if existing else None)
            banned_value = banned_at if banned_at is not None else (existing.banned_at if existing else None)
            banned_flag = is_banned if is_banned is not None else (existing.is_banned if existing else False)
            total_messages_value = (
                total_messages if total_messages is not None else (existing.total_messages if existing else 0)
            )
            successes_value = (
                verification_successes
                if verification_successes is not None
                else (existing.verification_successes if existing else 0)
            )
            failures_value = (
                verification_failures
                if verification_failures is not None
                else (existing.verification_failures if existing else 0)
            )
            verification_at = (
                last_verification_at
                if last_verification_at is not None
                else (existing.last_verification_at if existing else None)
            )
            self._connection.execute(
                """
                INSERT INTO user_profiles (
                    user_id, username, full_name, first_seen_at, last_seen_at, joined_at,
                    banned_at, is_banned, total_messages, verification_successes,
                    verification_failures, last_verification_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    first_seen_at = MIN(user_profiles.first_seen_at, excluded.first_seen_at),
                    last_seen_at = excluded.last_seen_at,
                    joined_at = COALESCE(user_profiles.joined_at, excluded.joined_at),
                    banned_at = excluded.banned_at,
                    is_banned = excluded.is_banned,
                    total_messages = excluded.total_messages,
                    verification_successes = excluded.verification_successes,
                    verification_failures = excluded.verification_failures,
                    last_verification_at = excluded.last_verification_at,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    username,
                    full_name,
                    first_seen,
                    last_seen,
                    joined_value,
                    banned_value,
                    1 if banned_flag else 0,
                    total_messages_value,
                    successes_value,
                    failures_value,
                    verification_at,
                    existing.created_at if existing else now,
                    now,
                ),
            )
            self._connection.commit()
            return self.get_user_profile(user_id)

    def record_user_seen(
        self,
        user_id: int,
        username: str | None,
        full_name: str,
        *,
        seen_at: str | None = None,
        count_message: bool = False,
    ) -> UserProfileRecord:
        current = self.get_user_profile(user_id)
        total_messages = current.total_messages if current else 0
        if count_message:
            total_messages += 1
        return self.upsert_user_profile(
            user_id=user_id,
            username=username,
            full_name=full_name,
            seen_at=seen_at,
            total_messages=total_messages,
        )

    def record_verification_result(
        self,
        *,
        user_id: int,
        username: str | None,
        full_name: str,
        success: bool,
        seen_at: str | None = None,
    ) -> UserProfileRecord:
        current = self.get_user_profile(user_id)
        successes = (current.verification_successes if current else 0) + (1 if success else 0)
        failures = (current.verification_failures if current else 0) + (0 if success else 1)
        return self.upsert_user_profile(
            user_id=user_id,
            username=username,
            full_name=full_name,
            seen_at=seen_at,
            verification_successes=successes,
            verification_failures=failures,
            last_verification_at=seen_at,
        )

    def mark_user_banned(
        self,
        user_id: int,
        *,
        username: str | None = None,
        full_name: str = "",
        banned_at: str | None = None,
    ) -> UserProfileRecord:
        current = self.get_user_profile(user_id)
        return self.upsert_user_profile(
            user_id=user_id,
            username=username if username is not None else (current.username if current else None),
            full_name=full_name or (current.full_name if current else ""),
            banned_at=banned_at or utc_now().isoformat(),
            is_banned=True,
        )

    def get_user_profile(self, user_id: int) -> UserProfileRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return _row_to_user_profile(row) if row else None

    def list_users(self, limit: int = 20, offset: int = 0) -> list[UserSummary]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM user_profiles
                ORDER BY last_seen_at DESC, user_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [_row_to_user_summary(row) for row in rows]

    def count_users(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM user_profiles").fetchone()
            return int(row["count"])

    def count_active_users(self, days: int = 7) -> int:
        since = (utc_now() - timedelta(days=days)).isoformat()
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM user_profiles WHERE last_seen_at >= ?",
                (since,),
            ).fetchone()
            return int(row["count"])

    def count_banned_users(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM user_profiles WHERE is_banned = 1",
            ).fetchone()
            return int(row["count"])

    def count_verified_users(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM user_profiles WHERE verification_successes > 0"
            ).fetchone()
            return int(row["count"])

    def count_failed_verification_users(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM user_profiles WHERE verification_failures > 0"
            ).fetchone()
            return int(row["count"])

    def count_recent_new_users(self, days: int = 1) -> int:
        since = (utc_now() - timedelta(days=days)).isoformat()
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM user_profiles WHERE first_seen_at >= ?",
                (since,),
            ).fetchone()
            return int(row["count"])

    def count_recent_banned_users(self, days: int = 7) -> int:
        since = (utc_now() - timedelta(days=days)).isoformat()
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM user_profiles WHERE banned_at >= ?",
                (since,),
            ).fetchone()
            return int(row["count"])

    def get_user_summary(self, user_id: int) -> UserSummary | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return _row_to_user_summary(row) if row else None

    def count_total_messages(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(SUM(total_messages), 0) AS total FROM user_profiles"
            ).fetchone()
            return int(row["total"])

    def get_verification_stats(self) -> VerificationStats:
        total = self._scalar(
            "SELECT COUNT(*) FROM verification_challenges"
        )
        today_since = datetime.combine(
            utc_now().date(),
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).isoformat()
        last_24h_since = (utc_now() - timedelta(days=1)).isoformat()
        last_7d_since = (utc_now() - timedelta(days=7)).isoformat()
        success = self._scalar("SELECT COUNT(*) FROM verification_challenges WHERE status = 'passed'")
        failure = self._scalar("SELECT COUNT(*) FROM verification_challenges WHERE status = 'failed'")
        timeout = self._scalar("SELECT COUNT(*) FROM verification_challenges WHERE status = 'expired'")
        today = self._scalar(
            "SELECT COUNT(*) FROM verification_challenges WHERE passed_at >= ?",
            (today_since,),
        )
        last_24h = self._scalar(
            "SELECT COUNT(*) FROM verification_challenges WHERE created_at >= ?",
            (last_24h_since,),
        )
        last_7d = self._scalar(
            "SELECT COUNT(*) FROM verification_challenges WHERE created_at >= ?",
            (last_7d_since,),
        )
        success_rate = (success / total * 100.0) if total else 0.0
        failure_rate = (failure / total * 100.0) if total else 0.0
        timeout_rate = (timeout / total * 100.0) if total else 0.0
        return VerificationStats(
            total=total,
            today=today,
            last_24h=last_24h,
            last_7d=last_7d,
            success=success,
            failure=failure,
            timeout=timeout,
            success_rate=success_rate,
            failure_rate=failure_rate,
            timeout_rate=timeout_rate,
        )

    def get_app_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row else default

    def set_app_setting(self, key: str, value: str) -> None:
        now = utc_now().isoformat()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            self._connection.commit()

    def delete_app_setting(self, key: str) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            self._connection.commit()

    def get_bool_setting(self, key: str, default: bool = False) -> bool:
        raw = self.get_app_setting(key)
        if raw is None:
            return default
        return raw.lower() in {"1", "true", "yes", "on"}

    def vacuum(self) -> None:
        with self._lock:
            self._connection.execute("VACUUM")

    def quick_check(self) -> tuple[bool, str | None]:
        with self._lock:
            try:
                row = self._connection.execute("PRAGMA quick_check").fetchone()
            except sqlite3.DatabaseError as exc:
                return False, str(exc)
            result = str(row[0]) if row else "unknown"
            return result == "ok", None if result == "ok" else result

    def integrity_check(self) -> tuple[bool, str | None]:
        with self._lock:
            try:
                row = self._connection.execute("PRAGMA integrity_check").fetchone()
            except sqlite3.DatabaseError as exc:
                return False, str(exc)
            result = str(row[0]) if row else "unknown"
            return result == "ok", None if result == "ok" else result

    def repair_database(self) -> tuple[bool, str | None]:
        with self._lock:
            ok, detail = self.integrity_check()
            if ok:
                return True, None
            try:
                self._connection.execute("REINDEX")
                self._connection.execute("PRAGMA optimize")
                self._connection.commit()
            except sqlite3.DatabaseError as exc:
                return False, str(exc)
            ok, detail = self.integrity_check()
            return ok, detail

    def database_size_bytes(self) -> int:
        try:
            return self._db_path.stat().st_size
        except OSError:
            return 0

    def table_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()
            return int(row["count"])

    def build_database_snapshot(self) -> DatabaseSnapshot:
        ok, error = self.quick_check()
        return DatabaseSnapshot(
            path=str(self._db_path),
            size_bytes=self.database_size_bytes(),
            table_count=self.table_count(),
            integrity_ok=ok,
            connection_ok=True,
            error=error,
        )

    def build_runtime_snapshot(
        self,
        *,
        hostname: str,
        platform: str,
        uptime_seconds: int,
        cpu_percent: float,
        memory_total: int,
        memory_used: int,
        memory_percent: float,
        disk_total: int,
        disk_used: int,
        disk_percent: float,
        net_sent: int,
        net_recv: int,
        load_1m: float | None,
        load_5m: float | None,
        load_15m: float | None,
    ) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            hostname=hostname,
            platform=platform,
            uptime_seconds=uptime_seconds,
            cpu_percent=cpu_percent,
            memory_total=memory_total,
            memory_used=memory_used,
            memory_percent=memory_percent,
            disk_total=disk_total,
            disk_used=disk_used,
            disk_percent=disk_percent,
            net_sent=net_sent,
            net_recv=net_recv,
            load_1m=load_1m,
            load_5m=load_5m,
            load_15m=load_15m,
        )

    def build_git_snapshot(
        self,
        *,
        branch: str,
        current_revision: str,
        latest_revision: str | None,
        is_dirty: bool,
    ) -> GitSnapshot:
        return GitSnapshot(
            branch=branch,
            current_revision=current_revision,
            latest_revision=latest_revision,
            is_dirty=is_dirty,
        )

    def cleanup_audit_logs(self, older_than: datetime) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM audit_logs WHERE created_at < ?",
                (older_than.isoformat(),),
            )
            self._connection.commit()
            return cursor.rowcount

    def cleanup_old_challenges(self, older_than: datetime) -> int:
        with self._lock:
            cursor = self._connection.execute(
                """
                DELETE FROM verification_challenges
                WHERE status != 'pending' AND updated_at < ?
                """,
                (older_than.isoformat(),),
            )
            self._connection.commit()
            return cursor.rowcount

    def list_recent_group_events(self, chat_id: int, limit: int = 5) -> list[AuditLogRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM audit_logs
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ).fetchall()
            return [_row_to_audit(row) for row in rows]

    def list_recent_user_events(self, user_id: int, limit: int = 5) -> list[AuditLogRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM audit_logs
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [_row_to_audit(row) for row in rows]

    def _scalar(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._lock:
            row = self._connection.execute(sql, params).fetchone()
            return int(row[0]) if row else 0


def _group_risk_level(
    *,
    member_count: int,
    admin_count: int,
    verification_enabled: bool,
    auto_delete_seconds: int,
) -> str:
    if not verification_enabled:
        return "high"
    if member_count >= 1000 and admin_count < 2:
        return "high"
    if auto_delete_seconds == 0 and member_count >= 100:
        return "medium"
    return "low"


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


def _row_to_group_profile(row: sqlite3.Row) -> GroupProfileRecord:
    return GroupProfileRecord(
        chat_id=int(row["chat_id"]),
        title=str(row["title"]),
        member_count=int(row["member_count"]),
        admin_count=int(row["admin_count"]),
        verification_enabled=bool(row["verification_enabled"]),
        auto_delete_seconds=int(row["auto_delete_seconds"]),
        joined_at=row["joined_at"],
        last_active_at=row["last_active_at"],
        risk_level=str(row["risk_level"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_user_profile(row: sqlite3.Row) -> UserProfileRecord:
    return UserProfileRecord(
        user_id=int(row["user_id"]),
        username=row["username"],
        full_name=str(row["full_name"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        joined_at=row["joined_at"],
        banned_at=row["banned_at"],
        is_banned=bool(row["is_banned"]),
        total_messages=int(row["total_messages"]),
        verification_successes=int(row["verification_successes"]),
        verification_failures=int(row["verification_failures"]),
        last_verification_at=row["last_verification_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_user_summary(row: sqlite3.Row) -> UserSummary:
    last_seen = str(row["last_seen_at"])
    return UserSummary(
        user_id=int(row["user_id"]),
        username=row["username"],
        full_name=str(row["full_name"]),
        total_messages=int(row["total_messages"]),
        verification_successes=int(row["verification_successes"]),
        verification_failures=int(row["verification_failures"]),
        last_seen_at=last_seen,
        joined_at=row["joined_at"],
        banned_at=row["banned_at"],
        is_banned=bool(row["is_banned"]),
        active=_is_recent(last_seen, days=7),
    )


def _row_to_config_group(row: sqlite3.Row) -> ConfigGroupSummary:
    return ConfigGroupSummary(
        chat_id=int(row["chat_id"]),
        title=str(row["title"] or ""),
        tracked=row["profile_chat_id"] is not None,
        enabled=bool(row["enabled"]),
        timeout_seconds=int(row["timeout_seconds"]),
        expire_action=str(row["expire_action"]),
        auto_delete_seconds=int(row["auto_delete_seconds"]),
        last_active_at=row["last_active_at"],
    )


def _is_recent(value: str, days: int) -> bool:
    try:
        return datetime.fromisoformat(value) >= utc_now() - timedelta(days=days)
    except ValueError:
        return False
