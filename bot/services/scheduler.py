from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot

from bot.storage import Repository
from bot.services.audit import AuditService
from bot.services.membership import MembershipService
from bot.services.verification import VerificationService

LOGGER = logging.getLogger(__name__)


class SchedulerService:
    def __init__(
        self,
        *,
        bot: Bot,
        repository: Repository,
        verification_service: VerificationService,
        membership_service: MembershipService,
        audit_service: AuditService,
        interval_seconds: int,
    ):
        self._bot = bot
        self._repository = repository
        self._verification_service = verification_service
        self._membership_service = membership_service
        self._audit_service = audit_service
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="verification-expiry-scanner")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._scan_once()
            except Exception:
                LOGGER.exception("expiry scan failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    async def _scan_once(self) -> None:
        expired_items = self._repository.list_expired_pending_challenges()
        for challenge in expired_items:
            settings = self._repository.ensure_group_settings(challenge.chat_id)
            if settings.expire_action == "kick":
                result = await self._membership_service.kick_member(
                    self._bot, challenge.chat_id, challenge.user_id
                )
                if result.success:
                    self._verification_service.mark_expired(challenge.id)
                    self._audit_service.log(
                        "expired_kicked",
                        chat_id=challenge.chat_id,
                        user_id=challenge.user_id,
                        challenge_id=challenge.id,
                    )
                else:
                    self._verification_service.mark_expired(challenge.id)
                    self._audit_service.log(
                        "expire_action_failed",
                        chat_id=challenge.chat_id,
                        user_id=challenge.user_id,
                        challenge_id=challenge.id,
                        fallback="restricted",
                        error=result.detail,
                    )
            else:
                self._verification_service.mark_expired(challenge.id)
                self._audit_service.log(
                    "expired_restricted",
                    chat_id=challenge.chat_id,
                    user_id=challenge.user_id,
                    challenge_id=challenge.id,
                )
