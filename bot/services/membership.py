from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatPermissions

LOGGER = logging.getLogger(__name__)

MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

UNRESTRICTED_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)


@dataclass(slots=True)
class ActionResult:
    success: bool
    detail: str | None = None


class MembershipService:
    def __init__(self, permission_cache_ttl_seconds: float = 300.0) -> None:
        self._permission_cache_ttl_seconds = permission_cache_ttl_seconds
        self._permission_cache: dict[int, tuple[float, ChatPermissions]] = {}

    async def is_admin(self, bot: Bot, chat_id: int, user_id: int) -> bool:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except TelegramBadRequest:
            return False
        return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}

    async def restrict_member(self, bot: Bot, chat_id: int, user_id: int) -> ActionResult:
        try:
            await bot.restrict_chat_member(chat_id, user_id, permissions=MUTED_PERMISSIONS)
            return ActionResult(True)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            LOGGER.warning("restrict_member failed chat_id=%s user_id=%s error=%s", chat_id, user_id, exc)
            return ActionResult(False, str(exc))

    async def unrestrict_member(self, bot: Bot, chat_id: int, user_id: int) -> ActionResult:
        try:
            permissions = await self._resolve_default_permissions(bot, chat_id)
            await bot.restrict_chat_member(chat_id, user_id, permissions=permissions)
            return ActionResult(True)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            LOGGER.warning("unrestrict_member failed chat_id=%s user_id=%s error=%s", chat_id, user_id, exc)
            return ActionResult(False, str(exc))

    async def kick_member(self, bot: Bot, chat_id: int, user_id: int) -> ActionResult:
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
            return ActionResult(True)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            LOGGER.warning("kick_member failed chat_id=%s user_id=%s error=%s", chat_id, user_id, exc)
            return ActionResult(False, str(exc))

    async def _resolve_default_permissions(self, bot: Bot, chat_id: int) -> ChatPermissions:
        cached = self._permission_cache.get(chat_id)
        now = time.monotonic()
        if cached and (now - cached[0]) < self._permission_cache_ttl_seconds:
            return cached[1]
        try:
            chat = await bot.get_chat(chat_id)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            LOGGER.warning("get_chat permissions failed chat_id=%s error=%s", chat_id, exc)
            return UNRESTRICTED_PERMISSIONS
        permissions = chat.permissions or UNRESTRICTED_PERMISSIONS
        self._permission_cache[chat_id] = (now, permissions)
        return permissions
