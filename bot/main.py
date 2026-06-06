from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

from bot.config import Settings
from bot.db import connect
from bot.handlers import build_admin_router, build_group_router, build_owner_router, build_private_router
from bot.logging import configure_logging
from bot.services.audit import AuditService
from bot.services.membership import MembershipService
from bot.services.operations import UpdateService
from bot.services.scheduler import SchedulerService
from bot.services.security import OwnerSecurityService
from bot.services.system import SystemInspector
from bot.services.verification import VerificationService
from bot.storage import Repository

LOGGER = logging.getLogger(__name__)


async def configure_bot_commands(bot: Bot, owner_id: int) -> None:
    await bot.set_my_commands(
        [BotCommand(command="start", description="Start verification")],
        scope=BotCommandScopeAllPrivateChats(),
    )
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Open owner panel"),
            BotCommand(command="panel", description="Open owner panel"),
            BotCommand(command="config", description="Configure group verification"),
            BotCommand(command="groups", description="List tracked groups"),
            BotCommand(command="group", description="Open group config"),
            BotCommand(command="status", description="Show system status"),
            BotCommand(command="update", description="Pull and update code"),
            BotCommand(command="cancel", description="Cancel current input"),
            BotCommand(command="help", description="Show command help"),
        ],
        scope=BotCommandScopeChat(chat_id=owner_id),
    )
    await bot.set_my_commands(
        [
            BotCommand(command="status", description="Show verification status"),
            BotCommand(command="enable", description="Enable join verification"),
            BotCommand(command="disable", description="Disable join verification"),
            BotCommand(command="set_timeout", description="Set verification timeout"),
            BotCommand(command="set_autodelete", description="Set auto-delete seconds"),
            BotCommand(command="resend", description="Resend verification link"),
            BotCommand(command="help", description="Show command help"),
        ],
        scope=BotCommandScopeAllChatAdministrators(),
    )


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    repo_root = Path(__file__).resolve().parents[1]
    connection = connect(settings.db_path)
    repository = Repository(
        connection,
        settings.db_path,
        settings.verify_timeout_seconds,
        settings.expire_action,
        settings.group_message_auto_delete_seconds,
        initialize_schema=False,
    )
    audit_service = AuditService(repository)
    verification_service = VerificationService(repository)
    membership_service = MembershipService()
    owner_security_service = OwnerSecurityService(settings.owner_id)
    inspector = SystemInspector(repo_root, repository, settings.redis_url)
    update_service = UpdateService(repo_root, settings, inspector)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await configure_bot_commands(bot, settings.owner_id)
    me = await bot.get_me()
    LOGGER.info(
        "bot starting username=%s db_path=%s timeout=%s expire_action=%s owner_id=%s",
        me.username,
        settings.db_path,
        settings.verify_timeout_seconds,
        settings.expire_action,
        settings.owner_id,
    )

    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_owner_router(
            repository,
            inspector,
            update_service,
            owner_security_service,
            audit_service,
            settings,
        )
    )
    dispatcher.include_router(
        build_admin_router(
            repository,
            verification_service,
            membership_service,
            audit_service,
            settings.owner_id,
            settings.max_failed_attempts,
        )
    )
    dispatcher.include_router(
        build_group_router(
            repository,
            verification_service,
            membership_service,
            audit_service,
            settings.owner_id,
        )
    )
    dispatcher.include_router(
        build_private_router(
            repository,
            verification_service,
            membership_service,
            audit_service,
            settings.max_failed_attempts,
            settings.owner_id,
        )
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
