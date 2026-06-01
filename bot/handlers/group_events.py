from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.types import ChatMemberUpdated, Message
from aiogram.utils.deep_linking import create_start_link

from bot.services.audit import AuditService
from bot.services.membership import MembershipService
from bot.services.verification import VerificationService
from bot.storage import Repository
from bot.utils import schedule_delete

GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


def build_group_router(
    repository: Repository,
    verification_service: VerificationService,
    membership_service: MembershipService,
    audit_service: AuditService,
    owner_id: int,
) -> Router:
    router = Router(name="group-events")

    @router.chat_member()
    async def on_chat_member_update(event: ChatMemberUpdated, bot: Bot) -> None:
        if event.chat.type not in GROUP_CHAT_TYPES:
            return
        old_status = getattr(event.old_chat_member, "status", None)
        new_status = getattr(event.new_chat_member, "status", None)
        if old_status == new_status:
            return
        audit_service.log(
            "chat_member_changed",
            chat_id=event.chat.id,
            user_id=event.new_chat_member.user.id,
            changed_by=event.from_user.id if event.from_user else None,
            old_status=str(old_status),
            new_status=str(new_status),
        )
        await _refresh_group_profile(bot, repository, event.chat.id, event.chat.title)

    @router.message(
        lambda message: message.chat.type in GROUP_CHAT_TYPES
        and bool(message.text or message.caption)
        and not (message.text or "").startswith("/")
    )
    async def on_group_activity(message: Message, bot: Bot) -> None:
        if not message.from_user or message.from_user.is_bot:
            return
        now = datetime.now(timezone.utc).isoformat()
        repository.record_user_seen(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            seen_at=now,
        )
        repository.touch_group_profile(
            message.chat.id,
            title=message.chat.title or str(message.chat.id),
            last_active_at=now,
        )

    @router.message(lambda message: bool(message.new_chat_members))
    async def on_new_members(message: Message, bot: Bot) -> None:
        if message.chat.type not in GROUP_CHAT_TYPES:
            return

        settings = repository.ensure_group_settings(message.chat.id)
        await _refresh_group_profile(bot, repository, message.chat.id, message.chat.title)
        if not settings.enabled:
            return

        for member in message.new_chat_members or []:
            if member.is_bot or member.id == owner_id:
                continue
            if await membership_service.is_admin(bot, message.chat.id, member.id):
                audit_service.log(
                    "member_joined_admin_skipped",
                    chat_id=message.chat.id,
                    user_id=member.id,
                    message_id=message.message_id,
                )
                continue

            now = datetime.now(timezone.utc).isoformat()
            repository.record_user_seen(member.id, member.username, member.full_name, seen_at=now)
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
            schedule_delete(bot, sent, settings.auto_delete_seconds)
            verification_service.set_prompt_message_id(challenge.id, sent.message_id)
            audit_service.log(
                "challenge_created" if created else "challenge_reused",
                chat_id=message.chat.id,
                user_id=member.id,
                challenge_id=challenge.id,
            )

    return router


async def _refresh_group_profile(bot: Bot, repository: Repository, chat_id: int, title: str | None) -> None:
    settings = repository.ensure_group_settings(chat_id)
    current = repository.get_group_profile(chat_id)
    member_count = current.member_count if current else 0
    admin_count = current.admin_count if current else 0
    try:
        member_count = await bot.get_chat_member_count(chat_id)
    except Exception:
        pass
    try:
        administrators = await bot.get_chat_administrators(chat_id)
        admin_count = len(administrators)
    except Exception:
        pass
    repository.touch_group_profile(
        chat_id,
        title=title or (current.title if current else str(chat_id)),
        member_count=member_count,
        admin_count=admin_count,
        verification_enabled=settings.enabled,
        auto_delete_seconds=settings.auto_delete_seconds,
        last_active_at=datetime.now(timezone.utc).isoformat(),
    )
