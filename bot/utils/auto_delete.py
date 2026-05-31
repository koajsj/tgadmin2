from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

LOGGER = logging.getLogger(__name__)

GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


def schedule_delete(bot: Bot, message: Message, delay_seconds: int) -> None:
    if delay_seconds <= 0:
        return
    if message.chat.type not in GROUP_CHAT_TYPES:
        return
    asyncio.create_task(
        _delete_later(bot, message.chat.id, message.message_id, delay_seconds),
        name=f"delete-message-{message.chat.id}-{message.message_id}",
    )


async def _delete_later(bot: Bot, chat_id: int, message_id: int, delay_seconds: int) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        LOGGER.debug(
            "delayed delete skipped chat_id=%s message_id=%s error=%s",
            chat_id,
            message_id,
            exc,
        )
