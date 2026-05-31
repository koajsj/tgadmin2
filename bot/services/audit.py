from __future__ import annotations

from typing import Any

from bot.storage import Repository


class AuditService:
    def __init__(self, repository: Repository):
        self._repository = repository

    def log(self, action: str, *, chat_id: int | None, user_id: int | None, **details: Any) -> None:
        self._repository.append_audit_log(action, chat_id=chat_id, user_id=user_id, details=details)
