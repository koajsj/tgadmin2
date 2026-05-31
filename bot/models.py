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
