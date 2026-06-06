from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ChallengeStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(slots=True)
class GroupSettingsRecord:
    chat_id: int
    enabled: bool
    timeout_seconds: int
    expire_action: str
    auto_delete_seconds: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class VerificationChallenge:
    id: int
    chat_id: int
    user_id: int
    user_chat_instance: str | None
    join_message_id: int | None
    prompt_message_id: int | None
    status: str
    start_token: str
    challenge_text: str
    expected_response: str
    attempt_count: int
    expires_at: str
    passed_at: str | None
    invalidated_at: str | None
    created_at: str
    updated_at: str

    def expires_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.expires_at)

    def passed_at_dt(self) -> datetime | None:
        return datetime.fromisoformat(self.passed_at) if self.passed_at else None

    def is_pending(self) -> bool:
        return self.status == ChallengeStatus.PENDING


@dataclass(slots=True)
class AuditLogRecord:
    id: int
    chat_id: int | None
    user_id: int | None
    action: str
    details_json: str
    created_at: str


@dataclass(slots=True)
class GroupProfileRecord:
    chat_id: int
    title: str
    member_count: int
    admin_count: int
    verification_enabled: bool
    auto_delete_seconds: int
    joined_at: str | None
    last_active_at: str | None
    risk_level: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class UserProfileRecord:
    user_id: int
    username: str | None
    full_name: str
    first_seen_at: str
    last_seen_at: str
    joined_at: str | None
    banned_at: str | None
    is_banned: bool
    total_messages: int
    verification_successes: int
    verification_failures: int
    last_verification_at: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class VerificationStats:
    total: int
    today: int
    last_24h: int
    last_7d: int
    success: int
    failure: int
    timeout: int
    success_rate: float
    failure_rate: float
    timeout_rate: float


@dataclass(slots=True)
class OwnerDashboardSummary:
    tracked_groups: int
    configurable_groups: int
    users: int
    active_users: int
    verification_stats: VerificationStats


@dataclass(slots=True)
class RuntimeSnapshot:
    hostname: str
    platform: str
    uptime_seconds: int
    cpu_percent: float
    memory_total: int
    memory_used: int
    memory_percent: float
    disk_total: int
    disk_used: int
    disk_percent: float
    net_sent: int
    net_recv: int
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None


@dataclass(slots=True)
class DatabaseSnapshot:
    path: str
    size_bytes: int
    table_count: int
    integrity_ok: bool
    connection_ok: bool
    error: str | None = None


@dataclass(slots=True)
class RedisSnapshot:
    configured: bool
    reachable: bool
    detail: str


@dataclass(slots=True)
class GitSnapshot:
    branch: str
    current_revision: str
    latest_revision: str | None
    is_dirty: bool


@dataclass(slots=True)
class GroupSummary:
    chat_id: int
    title: str
    alias: str | None
    member_count: int
    admin_count: int
    verification_enabled: bool
    auto_delete_seconds: int
    joined_at: str | None
    last_active_at: str | None
    risk_level: str


@dataclass(slots=True)
class ConfigGroupSummary:
    chat_id: int
    title: str
    alias: str | None
    tracked: bool
    enabled: bool
    timeout_seconds: int
    expire_action: str
    auto_delete_seconds: int
    last_active_at: str | None


@dataclass(slots=True)
class UserSummary:
    user_id: int
    username: str | None
    full_name: str
    total_messages: int
    verification_successes: int
    verification_failures: int
    last_seen_at: str
    joined_at: str | None
    banned_at: str | None
    is_banned: bool
    active: bool


@dataclass(slots=True)
class UpdateResult:
    success: bool
    current_revision: str
    latest_revision: str | None
    steps: list[str]
    output: str
    restarted_with: str
    error: str | None = None
