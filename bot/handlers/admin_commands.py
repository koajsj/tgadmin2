from __future__ import annotations

import math

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
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

    async def ensure_owner(message: Message, bot: Bot) -> bool:
        if not message.from_user or message.from_user.id != owner_id:
            await reply(message, bot, "这个机器人只给 OWNER 使用。")
            return False
        return True

    def resolve_target_chat_id(message: Message) -> int | None:
        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return message.chat.id
        if not message.from_user or message.from_user.id != owner_id:
            return None
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
            "<b>选择要操作的群</b>",
            "点下面的按钮选群。",
        ]
        if not groups:
            lines.append("当前还没有任何已记录的群。")
        builder = InlineKeyboardBuilder()
        for group in groups:
            label = group.title or str(group.chat_id)
            builder.button(
                text=label[:18],
                callback_data=f"grpact:{action}:{page}:{group.chat_id}:{value or '-'}",
            )
            lines.append(f"• {label} (<code>{group.chat_id}</code>)")
        if page > 1:
            builder.button(text="上一页", callback_data=f"grpnav:{action}:{page - 1}:{value or '-'}")
        builder.button(text=f"第 {page}/{total_pages} 页", callback_data="noop")
        if page < total_pages:
            builder.button(text="下一页", callback_data=f"grpnav:{action}:{page + 1}:{value or '-'}")
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
                    "<b>群验证状态</b>",
                    f"群ID：<code>{chat_id}</code>",
                    f"状态：{'开启' if settings.enabled else '关闭'}",
                    f"超时：{settings.timeout_seconds} 秒",
                    f"策略：{settings.expire_action}",
                    f"失败上限：{max_failed_attempts}",
                    f"自动删除：{settings.auto_delete_seconds} 秒",
                    f"待验证人数：{pending_count}",
                ]
            )
            await reply(message, bot, text, parse_mode="HTML")
            return

        if action == "enable":
            settings = repository.update_group_settings(chat_id, enabled=True)
            audit_service.log("group_enabled", chat_id=chat_id, user_id=message.from_user.id if message.from_user else None)
            await reply(message, bot, f"已开启入群验证，超时 {settings.timeout_seconds} 秒。")
            return

        if action == "disable":
            repository.update_group_settings(chat_id, enabled=False)
            audit_service.log("group_disabled", chat_id=chat_id, user_id=message.from_user.id if message.from_user else None)
            await reply(message, bot, "已关闭入群验证。")
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
            await reply(message, bot, f"验证超时已更新为 {seconds} 秒。")
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
                await reply(message, bot, "已关闭群内消息自动删除。")
            else:
                await reply(message, bot, f"群内消息将在 {seconds} 秒后自动删除。")
            return

        if action == "resend":
            if not value:
                await reply(message, bot, "请先指定要重新发送链接的用户ID。")
                return
            try:
                target_user_id = int(value)
            except ValueError:
                await reply(message, bot, "user_id 必须是整数。")
                return
            challenge = verification_service.get_pending_for_group_user(chat_id, target_user_id)
            if not challenge:
                await reply(message, bot, "没有找到该用户的待验证任务。")
                return
            me = await bot.get_me()
            deep_link = f"https://t.me/{me.username}?start=verify_{challenge.start_token}"
            sent = await message.answer(
                f"<a href='tg://user?id={target_user_id}'>该用户</a> 请点击下面链接继续验证：{deep_link}",
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
            return

    @router.message(Command("status"))
    async def status(message: Message, bot: Bot) -> None:
        if not await ensure_owner(message, bot):
            return
        chat_id, rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            await ask_group_selection(message, "status")
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "无法识别要操作的群。")
            return
        await execute_action(message, bot, "status", chat_id)

    @router.message(Command("enable"))
    async def enable(message: Message, bot: Bot) -> None:
        if not await ensure_owner(message, bot):
            return
        chat_id, _ = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            await ask_group_selection(message, "enable")
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "无法识别要操作的群。")
            return
        await execute_action(message, bot, "enable", chat_id)

    @router.message(Command("disable"))
    async def disable(message: Message, bot: Bot) -> None:
        if not await ensure_owner(message, bot):
            return
        chat_id, _ = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            await ask_group_selection(message, "disable")
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "无法识别要操作的群。")
            return
        await execute_action(message, bot, "disable", chat_id)

    @router.message(Command("set_timeout"))
    async def set_timeout(message: Message, bot: Bot) -> None:
        if not await ensure_owner(message, bot):
            return
        chat_id, rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            if not rest:
                await reply(message, bot, "用法：/set_timeout 秒数")
                return
            await ask_group_selection(message, "timeout", value=rest)
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "无法识别要操作的群。")
            return
        if not rest:
            await reply(message, bot, "用法：/set_timeout 秒数")
            return
        await execute_action(message, bot, "timeout", chat_id, value=rest)

    @router.message(Command("set_autodelete"))
    async def set_autodelete(message: Message, bot: Bot) -> None:
        if not await ensure_owner(message, bot):
            return
        chat_id, rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            if not rest:
                await reply(message, bot, "用法：/set_autodelete 秒数")
                return
            await ask_group_selection(message, "autodelete", value=rest)
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "无法识别要操作的群。")
            return
        if not rest:
            await reply(message, bot, "用法：/set_autodelete 秒数")
            return
        await execute_action(message, bot, "autodelete", chat_id, value=rest)

    @router.message(Command("resend"))
    async def resend(message: Message, bot: Bot) -> None:
        if not await ensure_owner(message, bot):
            return
        chat_id, rest = parse_group_command_args(message.text or "")
        if message.chat.type == ChatType.PRIVATE and chat_id is None:
            if not rest:
                await reply(message, bot, "用法：/resend 用户ID")
                return
            await ask_group_selection(message, "resend", value=rest)
            return
        if chat_id is None:
            chat_id = resolve_target_chat_id(message)
        if chat_id is None:
            await reply(message, bot, "无法识别要操作的群。")
            return
        if not rest:
            await reply(message, bot, "用法：/resend 用户ID")
            return
        await execute_action(message, bot, "resend", chat_id, value=rest)

    @router.message(Command("help"))
    async def help_command(message: Message, bot: Bot) -> None:
        if not await ensure_owner(message, bot):
            return
        help_text = "\n".join(
            [
                "/status 查看验证状态",
                "/enable 开启验证",
                "/disable 关闭验证",
                "/set_timeout 设置验证超时",
                "/set_autodelete 设置自动删消息",
                "/resend 重新发送验证链接",
                "/help 查看帮助",
                "",
                "私聊里不用写群ID，直接发命令后选群就行。",
            ]
        )
        await message.answer(help_text)

    @router.callback_query(F.data.startswith("grpnav:"))
    async def group_nav(callback: CallbackQuery) -> None:
        if not callback.message or not callback.from_user or callback.from_user.id != owner_id:
            await callback.answer("无权限", show_alert=True)
            return
        try:
            _, action, page_text, value = (callback.data or "").split(":", 3)
            page = max(1, int(page_text))
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        text, markup = build_group_picker(action, value if value != "-" else "", page)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        await callback.answer()

    @router.callback_query(F.data.startswith("grpact:"))
    async def group_action(callback: CallbackQuery) -> None:
        if not callback.message or not callback.from_user or callback.from_user.id != owner_id:
            await callback.answer("无权限", show_alert=True)
            return
        try:
            _, action, _page_text, chat_text, value = (callback.data or "").split(":", 4)
            chat_id = int(chat_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        await callback.answer()
        await execute_action(callback.message, callback.message.bot, action, chat_id, value if value != "-" else "")

    return router


def parse_resend_target(message: Message) -> tuple[int | None, str | None]:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, None
    parts = _command_args(message.text or "").split(maxsplit=1)
    if len(parts) != 1:
        return None, "请回复目标用户消息后使用 /resend，或者直接写 /resend 用户ID。"
    try:
        return int(parts[0].strip()), None
    except ValueError:
        return None, "user_id 必须是整数。"


def _strip_command_name(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return ""
    return parts[1].strip()


def _command_args(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return ""
    return parts[1].strip()
