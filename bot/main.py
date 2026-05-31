from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import Settings
from bot.db import connect
from bot.handlers import build_admin_router, build_group_router, build_private_router
from bot.logging import configure_logging
from bot.services import AuditService, MembershipService, SchedulerService, VerificationService
from bot.storage import Repository

LOGGER = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    connection = connect(settings.db_path)
    repository = Repository(connection, settings.verify_timeout_seconds, settings.expire_action)
    audit_service = AuditService(repository)
    verification_service = VerificationService(repository)
    membership_service = MembershipService()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    LOGGER.info(
        "bot starting username=%s db_path=%s timeout=%s expire_action=%s",
        me.username,
        settings.db_path,
        settings.verify_timeout_seconds,
        settings.expire_action,
    )

    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_admin_router(repository, verification_service, membership_service, audit_service)
    )
    dispatcher.include_router(
        build_group_router(
            repository,
            verification_service,
            membership_service,
            audit_service,
            settings.group_message_auto_delete_seconds,
        )
    )
    dispatcher.include_router(
        build_private_router(repository, verification_service, membership_service, audit_service)
    )

    scheduler = SchedulerService(
        bot=bot,
        repository=repository,
        verification_service=verification_service,
        membership_service=membership_service,
        audit_service=audit_service,
        interval_seconds=settings.scheduler_interval_seconds,
    )
    scheduler.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await scheduler.stop()
        await bot.session.close()
        connection.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
