from __future__ import annotations

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from bot.command_parsers import parse_auto_delete_command, parse_timeout_command
from bot.services.audit import AuditService
from bot.services.membership import MembershipService
from bot.services.verification import VerificationService
from bot.storage import Repository
from bot.utils import schedule_delete


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

    async def ensure_manager(message: Message, bot: Bot) -> bool:
        if not message.from_user or message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await reply(message, bot, "此命令只能在群里使用。")
            return False
        if message.from_user.id == owner_id:
            return True
        if not await membership_service.is_admin(bot, message.chat.id, message.from_user.id):
            await reply(message, bot, "仅群管理员或 OWNER 可使用此命令。")
            return False
        return True

    @router.message(Command("status"))
    async def status(message: Message, bot: Bot) -> None:
        settings = repository.ensure_group_settings(message.chat.id)
        pending_count = repository.count_pending_challenges(message.chat.id)
        text = "\n".join(
            [
                "<b>群验证状态</b>",
                f"状态：{'开启' if settings.enabled else '关闭'}",
                f"超时：{settings.timeout_seconds} 秒",
                f"策略：{settings.expire_action}",
                f"失败上限：{max_failed_attempts}",
                f"自动删除：{settings.auto_delete_seconds} 秒",
                f"待验证人数：{pending_count}",
            ]
        )
        await reply(message, bot, text, parse_mode="HTML")

    @router.message(Command("enable"))
    async def enable(message: Message, bot: Bot) -> None:
        if not await ensure_manager(message, bot):
            return
        settings = repository.update_group_settings(message.chat.id, enabled=True)
        audit_service.log("group_enabled", chat_id=message.chat.id, user_id=message.from_user.id)
        await reply(
            message,
            bot,
            f"已开启入群验证，超时 {settings.timeout_seconds} 秒。",
        )

    @router.message(Command("disable"))
    async def disable(message: Message, bot: Bot) -> None:
        if not await ensure_manager(message, bot):
            return
        repository.update_group_settings(message.chat.id, enabled=False)
        audit_service.log("group_disabled", chat_id=message.chat.id, user_id=message.from_user.id)
        await reply(message, bot, "已关闭入群验证。")

    @router.message(Command("set_timeout"))
    async def set_timeout(message: Message, bot: Bot) -> None:
        if not await ensure_manager(message, bot):
            return
        seconds, error = parse_timeout_command(message.text or "")
        if error:
            await reply(message, bot, error)
            return
        repository.update_group_settings(message.chat.id, timeout_seconds=seconds)
        audit_service.log(
            "group_timeout_updated",
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            timeout_seconds=seconds,
        )
        await reply(message, bot, f"验证超时已更新为 {seconds} 秒。")

    @router.message(Command("set_autodelete"))
    async def set_autodelete(message: Message, bot: Bot) -> None:
        if not await ensure_manager(message, bot):
            return
        seconds, error = parse_auto_delete_command(message.text or "")
        if error:
            await reply(message, bot, error)
            return
        repository.update_group_settings(message.chat.id, auto_delete_seconds=seconds)
        audit_service.log(
            "group_auto_delete_updated",
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            auto_delete_seconds=seconds,
        )
        if seconds == 0:
            await reply(message, bot, "已关闭群内消息自动删除。")
        else:
            await reply(message, bot, f"群内消息将在 {seconds} 秒后自动删除。")

    @router.message(Command("resend"))
    async def resend(message: Message, bot: Bot) -> None:
        if not await ensure_manager(message, bot):
            return
        target_user_id, error = parse_resend_target(message)
        if error:
            await reply(message, bot, error)
            return
        assert target_user_id is not None
        challenge = verification_service.get_pending_for_group_user(message.chat.id, target_user_id)
        if not challenge:
            await reply(message, bot, "没有找到该用户的待验证任务。")
            return
        me = await bot.get_me()
        deep_link = f"https://t.me/{me.username}?start=verify_{challenge.start_token}"
        sent = await message.answer(
            f"<a href='tg://user?id={target_user_id}'>该用户</a> 请点击下面链接继续验证：{deep_link}",
            parse_mode="HTML",
        )
        settings = repository.ensure_group_settings(message.chat.id)
        schedule_delete(bot, sent, settings.auto_delete_seconds)
        verification_service.set_prompt_message_id(challenge.id, sent.message_id)
        audit_service.log(
            "challenge_resend",
            chat_id=message.chat.id,
            user_id=target_user_id,
            challenge_id=challenge.id,
            admin_user_id=message.from_user.id,
        )

    @router.message(Command("help"))
    async def help_command(message: Message, bot: Bot) -> None:
        help_text = "\n".join(
            [
                "/status 查看验证状态",
                "/enable 开启验证",
                "/disable 关闭验证",
                "/set_timeout 设置验证超时",
                "/set_autodelete 设置自动删消息",
                "/resend 重新发送验证链接",
                "/help 查看帮助",
            ]
        )
        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await reply(message, bot, help_text)
            return
        await message.answer(help_text)

    return router


def parse_resend_target(message: Message) -> tuple[int | None, str | None]:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, None
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        return None, "请回复目标用户消息后使用 /resend，或输入 /resend 用户ID。"
    try:
        return int(parts[1].strip()), None
    except ValueError:
        return None, "user_id 必须是整数。"
