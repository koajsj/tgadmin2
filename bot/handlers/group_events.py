from __future__ import annotations

import asyncio

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.types import Message
from aiogram.utils.deep_linking import create_start_link

from bot.services import AuditService, MembershipService, VerificationService
from bot.storage import Repository


def build_group_router(
    repository: Repository,
    verification_service: VerificationService,
    membership_service: MembershipService,
    audit_service: AuditService,
    auto_delete_seconds: int,
) -> Router:
    router = Router(name="group-events")

    @router.message(lambda message: bool(message.new_chat_members))
    async def on_new_members(message: Message, bot: Bot) -> None:
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return

        settings = repository.ensure_group_settings(message.chat.id)
        if not settings.enabled:
            return

        for member in message.new_chat_members or []:
            if member.is_bot:
                continue
            if await membership_service.is_admin(bot, message.chat.id, member.id):
                audit_service.log(
                    "member_joined_admin_skipped",
                    chat_id=message.chat.id,
                    user_id=member.id,
                    message_id=message.message_id,
                )
                continue

            audit_service.log(
                "member_joined",
                chat_id=message.chat.id,
                user_id=member.id,
                message_id=message.message_id,
            )
            restriction = await membership_service.restrict_member(bot, message.chat.id, member.id)
            if not restriction.success:
                audit_service.log(
                    "member_restrict_failed",
                    chat_id=message.chat.id,
                    user_id=member.id,
                    error=restriction.detail,
                )
                continue

            audit_service.log(
                "member_restricted",
                chat_id=message.chat.id,
                user_id=member.id,
            )
            challenge, created = verification_service.get_or_create_challenge(
                chat_id=message.chat.id,
                user_id=member.id,
                display_name=member.full_name,
                join_message_id=message.message_id,
                user_chat_instance=None,
                timeout_seconds=settings.timeout_seconds,
            )
            deep_link = await create_start_link(bot, f"verify_{challenge.start_token}")
            prompt = (
                f"{member.mention_html()} 已被临时禁言。\n"
                f"请先私聊机器人完成验证后自动放行：{deep_link}\n"
                f"验证有效期：{settings.timeout_seconds} 秒。"
            )
            sent = await message.answer(prompt, parse_mode="HTML")
            if auto_delete_seconds > 0:
                asyncio.create_task(
                    _delete_later(bot, message.chat.id, sent.message_id, auto_delete_seconds)
                )
            verification_service.set_prompt_message_id(challenge.id, sent.message_id)
            audit_service.log(
                "challenge_created" if created else "challenge_reused",
                chat_id=message.chat.id,
                user_id=member.id,
                challenge_id=challenge.id,
            )

    return router


async def _delete_later(bot: Bot, chat_id: int, message_id: int, delay_seconds: int) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        return
