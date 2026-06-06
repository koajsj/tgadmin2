from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatMemberUpdated, Message
from aiogram.utils.deep_linking import create_start_link

from bot.services.audit import AuditService
from bot.services.membership import MembershipService
from bot.services.verification import VerificationService
from bot.storage import Repository
from bot.utils import schedule_delete

GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
ACTIVE_GROUP_STATUSES = {"member", "administrator", "creator"}
LOGGER = logging.getLogger(__name__)
GROUP_PROFILE_REFRESH_TTL_SECONDS = 60.0
GROUP_ACTIVITY_TOUCH_TTL_SECONDS = 30.0


def build_group_router(
    repository: Repository,
    verification_service: VerificationService,
    membership_service: MembershipService,
    audit_service: AuditService,
    owner_id: int,
) -> Router:
    router = Router(name="group-events")
    profile_refresh_cache: dict[int, float] = {}
    activity_touch_cache: dict[int, float] = {}

    async def refresh_group_profile(
        bot: Bot,
        chat_id: int,
        title: str | None,
        *,
        force: bool = False,
    ) -> None:
        if not _should_run(
            profile_refresh_cache,
            chat_id,
            GROUP_PROFILE_REFRESH_TTL_SECONDS,
            force=force,
        ):
            return
        await _refresh_group_profile(bot, repository, chat_id, title)

    def touch_group_activity(chat_id: int, title: str | None, last_active_at: str) -> None:
        if not _should_run(activity_touch_cache, chat_id, GROUP_ACTIVITY_TOUCH_TTL_SECONDS):
            return
        repository.touch_group_profile(
            chat_id,
            title=title or str(chat_id),
            last_active_at=last_active_at,
        )

    @router.my_chat_member()
    async def on_bot_membership_changed(event: ChatMemberUpdated, bot: Bot) -> None:
        if event.chat.type not in GROUP_CHAT_TYPES:
            return
        old_status = getattr(event.old_chat_member, "status", None)
        new_status = getattr(event.new_chat_member, "status", None)
        if _status_name(old_status) == _status_name(new_status):
            return
        if not _is_active_group_status(new_status):
            return
        settings = repository.ensure_group_settings(event.chat.id)
        repository.touch_group_profile(
            event.chat.id,
            title=event.chat.title or str(event.chat.id),
            verification_enabled=settings.enabled,
            auto_delete_seconds=settings.auto_delete_seconds,
            last_active_at=datetime.now(timezone.utc).isoformat(),
        )
        audit_service.log(
            "bot_group_auto_configured",
            chat_id=event.chat.id,
            user_id=event.new_chat_member.user.id,
            old_status=_status_name(old_status),
            new_status=_status_name(new_status),
        )
        await refresh_group_profile(bot, event.chat.id, event.chat.title, force=True)

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
        await refresh_group_profile(bot, event.chat.id, event.chat.title)

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
            count_message=True,
        )
        touch_group_activity(message.chat.id, message.chat.title, now)

    @router.message(lambda message: bool(message.new_chat_members))
    async def on_new_members(message: Message, bot: Bot) -> None:
        if message.chat.type not in GROUP_CHAT_TYPES:
            return

        settings = repository.ensure_group_settings(message.chat.id)
        await refresh_group_profile(bot, message.chat.id, message.chat.title, force=True)
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
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        LOGGER.warning("group member count refresh failed chat_id=%s error=%s", chat_id, exc)
    except Exception:
        LOGGER.exception("unexpected group member count refresh failure chat_id=%s", chat_id)
    try:
        administrators = await bot.get_chat_administrators(chat_id)
        admin_count = len(administrators)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        LOGGER.warning("group admin refresh failed chat_id=%s error=%s", chat_id, exc)
    except Exception:
        LOGGER.exception("unexpected group admin refresh failure chat_id=%s", chat_id)
    repository.touch_group_profile(
        chat_id,
        title=title or (current.title if current else str(chat_id)),
        member_count=member_count,
        admin_count=admin_count,
        verification_enabled=settings.enabled,
        auto_delete_seconds=settings.auto_delete_seconds,
        last_active_at=datetime.now(timezone.utc).isoformat(),
    )


def _status_name(status: object | None) -> str:
    value = getattr(status, "value", status)
    return str(value or "")


def _should_run(
    cache: dict[int, float],
    chat_id: int,
    ttl_seconds: float,
    *,
    force: bool = False,
) -> bool:
    now = time.monotonic()
    last_run = cache.get(chat_id)
    if not force and last_run is not None and (now - last_run) < ttl_seconds:
        return False
    cache[chat_id] = now
    return True


def _is_active_group_status(status: object | None) -> bool:
    return _status_name(status) in ACTIVE_GROUP_STATUSES
