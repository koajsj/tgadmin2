from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass(slots=True)
class ConfirmationToken:
    action: str
    token: str
    expires_at: float


class OwnerSecurityService:
    def __init__(self, owner_id: int, ttl_seconds: int = 300) -> None:
        self._owner_id = owner_id
        self._ttl_seconds = ttl_seconds
        self._pending: dict[tuple[int, str], ConfirmationToken] = {}

    def is_owner(self, user_id: int | None) -> bool:
        return user_id == self._owner_id

    def issue_confirmation(self, user_id: int, action: str) -> str:
        token = secrets.token_hex(3).upper()
        self._pending[(user_id, action)] = ConfirmationToken(
            action=action,
            token=token,
            expires_at=time.time() + self._ttl_seconds,
        )
        return token

    def verify_confirmation(self, user_id: int, action: str, token: str) -> bool:
        key = (user_id, action)
        current = self._pending.get(key)
        if not current:
            return False
        if current.expires_at < time.time():
            self._pending.pop(key, None)
            return False
        if current.token != token.strip().upper():
            return False
        self._pending.pop(key, None)
        return True

    def clear(self, user_id: int, action: str) -> None:
        self._pending.pop((user_id, action), None)
