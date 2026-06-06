from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

from aiogram import Bot

from bot.services.audit import AuditService
from bot.services.membership import MembershipService
from bot.services.verification import VerificationService
from bot.storage import Repository

LOGGER = logging.getLogger(__name__)
EXPIRED_CHALLENGE_CONCURRENCY = 10


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
    ) -> None:
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
                await self._run_maintenance_if_due()
            except Exception:
                LOGGER.exception("scheduler cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    async def _scan_once(self) -> None:
        expired_items = self._repository.list_expired_pending_challenges()
        if not expired_items:
            return
        semaphore = asyncio.Semaphore(EXPIRED_CHALLENGE_CONCURRENCY)
        await asyncio.gather(
            *(self._handle_expired_challenge(semaphore, challenge) for challenge in expired_items)
        )

    async def _handle_expired_challenge(
        self,
        semaphore: asyncio.Semaphore,
        challenge,
    ) -> None:
        async with semaphore:
            settings = self._repository.ensure_group_settings(challenge.chat_id)
            if settings.expire_action == "kick":
                result = await self._membership_service.kick_member(
                    self._bot, challenge.chat_id, challenge.user_id
                )
                self._verification_service.mark_expired(challenge.id)
                if result.success:
                    self._audit_service.log(
                        "expired_kicked",
                        chat_id=challenge.chat_id,
                        user_id=challenge.user_id,
                        challenge_id=challenge.id,
                    )
                else:
                    self._audit_service.log(
                        "expire_action_failed",
                        chat_id=challenge.chat_id,
                        user_id=challenge.user_id,
                        challenge_id=challenge.id,
                        fallback="restricted",
                        error=result.detail,
                    )
                return
            self._verification_service.mark_expired(challenge.id)
            self._audit_service.log(
                "expired_restricted",
                chat_id=challenge.chat_id,
                user_id=challenge.user_id,
                challenge_id=challenge.id,
            )

    async def _run_maintenance_if_due(self) -> None:
        today = _today_key()
        last_run = self._repository.get_app_setting("db_maintenance_last_date")
        if last_run == today:
            return
        if _utc_hour() < 3:
            return
        db_ok, db_detail = self._repository.quick_check()
        repaired = False
        repaired_detail: str | None = None
        if not db_ok:
            repaired, repaired_detail = self._repository.repair_database()
        deleted_logs = self._repository.cleanup_audit_logs(
            older_than=_utc_now() - timedelta(days=30)
        )
        deleted_challenges = self._repository.cleanup_old_challenges(
            older_than=_utc_now() - timedelta(days=30)
        )
        self._repository.set_app_setting("db_maintenance_last_date", today)
        self._audit_service.log(
            "database_maintenance",
            chat_id=None,
            user_id=None,
            db_ok=db_ok,
            db_detail=db_detail,
            repaired=repaired,
            repaired_detail=repaired_detail,
            deleted_logs=deleted_logs,
            deleted_challenges=deleted_challenges,
        )


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _today_key() -> str:
    return _utc_now().date().isoformat()


def _utc_hour() -> int:
    return _utc_now().hour
