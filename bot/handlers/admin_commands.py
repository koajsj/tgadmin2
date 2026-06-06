from __future__ import annotations

import math

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.command_parsers import parse_auto_delete_command, parse_timeout_command
from bot.services.audit import AuditService
from bot.services.membership import MembershipService
from bot.services.verification import VerificationService
from bot.storage import Repository
from bot.utils import schedule_delete

GROUP_PAGE_SIZE = 5


def build_admin_router(
    repository: Repository,
    verification_service: VerificationService,
    membership_service: MembershipService,
    audit_service: AuditService,
    owner_id: int,
    max_failed_attempts: int,
) -> Router:
    router = Router(name="admin-commands")

    async def reply(message: Message, bot: Bot, text: str, **kwargs: object) -> None:
        sent = await message.answer(text, **kwargs)
        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            settings = repository.ensure_group_settings(message.chat.id)
            schedule_delete(bot, sent, settings.auto_delete_seconds)

    async def ensure_operator(message: Message, bot: Bot) -> bool:
        if not message.from_user:
            return False
        if message.from_user.id == owner_id:
            return True
        if message.chat.type == ChatType.PRIVATE:
            await reply(message, bot, "Only OWNER can manage group verification from private chat.")
            return False
        if await membership_service.is_admin(bot, message.chat.id, message.from_user.id):
            return True
        await reply(message, bot, "Only group admins or OWNER can use this command.")
        return False

    def resolve_target_chat_id(message: Message) -> int | None:
        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return message.chat.id
        return None

    def parse_group_command_args(text: str) -> tuple[int | None, str]:
        raw = _command_args(text)
        if not raw:
            return None, ""
        parts = raw.split(maxsplit=1)
        try:
            chat_id = int(parts[0].strip())
        except ValueError:
            return None, raw
        rest = parts[1].strip() if len(parts) > 1 else ""
        return chat_id, rest

    def build_group_picker(action: str, value: str = "", page: int = 1) -> tuple[str, object]:
        total_groups = repository.count_groups()
        total_pages = max(1, math.ceil(total_groups / GROUP_PAGE_SIZE))
        page = max(1, min(page, total_pages))
        offset = (page - 1) * GROUP_PAGE_SIZE
        groups = repository.list_groups(limit=GROUP_PAGE_SIZE, offset=offset)
        lines = [
            "<b>Select a group</b>",
            "Choose the target group below.",
        ]
        if not groups:
            lines.append("No tracked groups available yet.")
        builder = InlineKeyboardBuilder()
        for group in groups:
            label = group.alias or group.title or str(group.chat_id)
            builder.button(
                text=label[:18],
                callback_data=f"grpact:{action}:{page}:{group.chat_id}:{value or '-'}",
            )
            lines.append(f"- {label} (<code>{group.chat_id}</code>)")
        if page > 1:
            builder.button(
                text="Prev",
                callback_data=f"grpnav:{action}:{page - 1}:{value or '-'}",
            )
        builder.button(text=f"{page}/{total_pages}", callback_data="noop")
        if page < total_pages:
            builder.button(
                text="Next",
                callback_data=f"grpnav:{action}:{page + 1}:{value or '-'}",
            )
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    async def ask_group_selection(message: Message, action: str, value: str = "", page: int = 1) -> None:
        text, markup = build_group_picker(action, value=value, page=page)
        await message.answer(text, parse_mode="HTML", reply_markup=markup)

    async def execute_action(
        message: Message,
        bot: Bot,
        action: str,
        chat_id: int,
        value: str = "",
    ) -> None:
        if action == "status":
            settings = repository.ensure_group_settings(chat_id)
            pending_count = repository.count_pending_challenges(chat_id)
            text = "\n".join(
                [
                    "<b>Verification Status</b>",
                    f"Chat ID: <code>{chat_id}</code>",
                    f"Enabled: {'on' if settings.enabled else 'off'}",
                    f"Timeout: {settings.timeout_seconds}s",
                    f"Expire action: {settings.expire_action}",
                    f"Max failed attempts: {max_failed_attempts}",
                    f"Auto delete: {settings.auto_delete_seconds}s",
                    f"Pending users: {pending_count}",
                ]
            )
            await reply(message, bot, text, parse_mode="HTML")
            return

        if action == "enable":
            settings = repository.update_group_settings(chat_id, enabled=True)
            audit_service.log(
                "group_enabled",
                chat_id=chat_id,
                user_id=message.from_user.id if message.from_user else None,
            )
            await reply(message, bot, f"Group verification enabled. Timeout: {settings.timeout_seconds}s.")
            return

        if action == "disable":
            repository.update_group_settings(chat_id, enabled=False)
            audit_service.log(
                "group_disabled",
                chat_id=chat_id,
                user_id=message.from_user.id if message.from_user else None,
            )
            await reply(message, bot, "Group verification disabled.")
            return

        if action == "timeout":
            seconds, error = parse_timeout_command(f"/set_timeout {value}".strip())
            if error:
                await reply(message, bot, error)
                return
            repository.update_group_settings(chat_id, timeout_seconds=seconds)
            audit_service.log(
                "group_timeout_updated",
                chat_id=chat_id,
                user_id=message.from_user.id if message.from_user else None,
                timeout_seconds=seconds,
            )
            await reply(message, bot, f"Verification timeout updated to {seconds}s.")
            return

        if action == "autodelete":
            seconds, error = parse_auto_delete_command(f"/set_autodelete {value}".strip())
            if error:
                await reply(message, bot, error)
                return
            repository.update_group_settings(chat_id, auto_delete_seconds=seconds)
            audit_service.log(
                "group_auto_delete_updated",
                chat_id=chat_id,
                user_id=message.from_user.id if message.from_user else None,
                auto_delete_seconds=seconds,
            )
            if seconds == 0:
                await reply(message, bot, "Auto-delete disabled for group messages.")
            else:
                await reply(message, bot, f"Group messages will auto-delete after {seconds}s.")
            return

        if action == "resend":
            if not value:
                await reply(message, bot, "Please provide the target user ID.")
                return
            try:
                target_user_id = int(value)
            except ValueError:
                await reply(message, bot, "user_id must be an integer.")
                return
            challenge = verification_service.get_pending_for_group_user(chat_id, target_user_id)
            if not challenge:
                await reply(message, bot, "No pending verification task found for that user.")
                return
            me = await bot.get_me()
            deep_link = f"https://t.me/{me.username}?start=verify_{challenge.start_token}"
            sent = await message.answer(
                (
                    f"<a href='tg://user?id={target_user_id}'>User</a> "
                    f"please continue verification here: {deep_link}"
                ),
                parse_mode="HTML",
            )
            settings = repository.ensure_group_settings(chat_id)
            schedule_delete(bot, sent, settings.auto_delete_seconds)
            verification_service.set_prompt_message_id(challenge.id, sent.message_id)
            audit_service.log(
                "challenge_resend",
                chat_id=chat_id,
                user_id=target_user_id,
                challenge_id=challenge.id,
                admin_user_id=message.from_user.id if message.from_user else None,
            )

    @router.message(Command("status"))
    async def status(message: Message, bot: Bot) -> None:
        if not await ensure_operator(message, bot):
            return
        chat_id, _rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            await ask_group_selection(message, "status")
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "Unable to determine target group.")
            return
        await execute_action(message, bot, "status", chat_id)

    @router.message(Command("enable"))
    async def enable(message: Message, bot: Bot) -> None:
        if not await ensure_operator(message, bot):
            return
        chat_id, _rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            await ask_group_selection(message, "enable")
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "Unable to determine target group.")
            return
        await execute_action(message, bot, "enable", chat_id)

    @router.message(Command("disable"))
    async def disable(message: Message, bot: Bot) -> None:
        if not await ensure_operator(message, bot):
            return
        chat_id, _rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            await ask_group_selection(message, "disable")
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "Unable to determine target group.")
            return
        await execute_action(message, bot, "disable", chat_id)

    @router.message(Command("set_timeout"))
    async def set_timeout(message: Message, bot: Bot) -> None:
        if not await ensure_operator(message, bot):
            return
        chat_id, rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            if not rest:
                await reply(message, bot, "Usage: /set_timeout <seconds>")
                return
            await ask_group_selection(message, "timeout", value=rest)
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "Unable to determine target group.")
            return
        if not rest:
            await reply(message, bot, "Usage: /set_timeout <seconds>")
            return
        await execute_action(message, bot, "timeout", chat_id, value=rest)

    @router.message(Command("set_autodelete"))
    async def set_autodelete(message: Message, bot: Bot) -> None:
        if not await ensure_operator(message, bot):
            return
        chat_id, rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            if not rest:
                await reply(message, bot, "Usage: /set_autodelete <seconds>")
                return
            await ask_group_selection(message, "autodelete", value=rest)
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "Unable to determine target group.")
            return
        if not rest:
            await reply(message, bot, "Usage: /set_autodelete <seconds>")
            return
        await execute_action(message, bot, "autodelete", chat_id, value=rest)

    @router.message(Command("resend"))
    async def resend(message: Message, bot: Bot) -> None:
        if not await ensure_operator(message, bot):
            return
        chat_id, rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            if not rest:
                await reply(message, bot, "Usage: /resend <user_id>")
                return
            await ask_group_selection(message, "resend", value=rest)
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "Unable to determine target group.")
            return
        if not rest:
            await reply(message, bot, "Usage: /resend <user_id>")
            return
        await execute_action(message, bot, "resend", chat_id, value=rest)

    @router.message(Command("help"))
    async def help_command(message: Message, bot: Bot) -> None:
        if not await ensure_operator(message, bot):
            return
        help_text = "\n".join(
            [
                "/status view verification status",
                "/enable enable join verification",
                "/disable disable join verification",
                "/set_timeout set verification timeout",
                "/set_autodelete set group auto-delete",
                "/resend resend a user's verification link",
                "/help show help",
                "",
                "Group admins can use these commands in their group.",
                "OWNER can also send them in private chat and then choose a group.",
            ]
        )
        await message.answer(help_text)

    @router.callback_query(F.data.startswith("grpnav:"))
    async def group_nav(callback: CallbackQuery) -> None:
        if not callback.message or not callback.from_user or callback.from_user.id != owner_id:
            await callback.answer("Permission denied.", show_alert=True)
            return
        try:
            _, action, page_text, value = (callback.data or "").split(":", 3)
            page = max(1, int(page_text))
        except ValueError:
            await callback.answer("Invalid parameters.", show_alert=True)
            return
        text, markup = build_group_picker(action, value if value != "-" else "", page)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except TelegramBadRequest:
            pass
        await callback.answer()

    @router.callback_query(F.data.startswith("grpact:"))
    async def group_action(callback: CallbackQuery) -> None:
        if not callback.message or not callback.from_user or callback.from_user.id != owner_id:
            await callback.answer("Permission denied.", show_alert=True)
            return
        try:
            _, action, _page_text, chat_text, value = (callback.data or "").split(":", 4)
            chat_id = int(chat_text)
        except ValueError:
            await callback.answer("Invalid parameters.", show_alert=True)
            return
        await callback.answer()
        await execute_action(
            callback.message,
            callback.message.bot,
            action,
            chat_id,
            value if value != "-" else "",
        )

    return router


def _command_args(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return ""
    return parts[1].strip()
