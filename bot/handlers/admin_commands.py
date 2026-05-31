from __future__ import annotations

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from bot.services import AuditService, MembershipService, VerificationService
from bot.storage import Repository

TIMEOUT_MIN = 60
TIMEOUT_MAX = 86400


def build_admin_router(
    repository: Repository,
    verification_service: VerificationService,
    membership_service: MembershipService,
    audit_service: AuditService,
) -> Router:
    router = Router(name="admin-commands")

    async def ensure_admin(message: Message, bot: Bot) -> bool:
        if not message.from_user or message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await message.answer("该命令只能在群里使用。")
            return False
        if not await membership_service.is_admin(bot, message.chat.id, message.from_user.id):
            await message.answer("只有群管理员可以使用这个命令。")
            return False
        return True

    @router.message(Command("status"))
    async def status(message: Message, bot: Bot) -> None:
        if not await ensure_admin(message, bot):
            return
        settings = repository.ensure_group_settings(message.chat.id)
        pending_count = repository.count_pending_challenges(message.chat.id)
        text = (
            f"验证状态：{'启用' if settings.enabled else '禁用'}\n"
            f"超时时间：{settings.timeout_seconds} 秒\n"
            f"过期策略：{settings.expire_action}\n"
            f"待验证人数：{pending_count}"
        )
        await message.answer(text)

    @router.message(Command("enable"))
    async def enable(message: Message, bot: Bot) -> None:
        if not await ensure_admin(message, bot):
            return
        settings = repository.update_group_settings(message.chat.id, enabled=True)
        audit_service.log("group_enabled", chat_id=message.chat.id, user_id=message.from_user.id)
        await message.answer(f"已启用入群验证，超时时间 {settings.timeout_seconds} 秒。")

    @router.message(Command("disable"))
    async def disable(message: Message, bot: Bot) -> None:
        if not await ensure_admin(message, bot):
            return
        repository.update_group_settings(message.chat.id, enabled=False)
        audit_service.log("group_disabled", chat_id=message.chat.id, user_id=message.from_user.id)
        await message.answer("已禁用入群验证。")

    @router.message(Command("set_timeout"))
    async def set_timeout(message: Message, bot: Bot) -> None:
        if not await ensure_admin(message, bot):
            return
        seconds, error = parse_timeout_command(message.text or "")
        if error:
            await message.answer(error)
            return
        repository.update_group_settings(message.chat.id, timeout_seconds=seconds)
        audit_service.log(
            "group_timeout_updated",
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            timeout_seconds=seconds,
        )
        await message.answer(f"验证超时时间已更新为 {seconds} 秒。")

    @router.message(Command("resend"))
    async def resend(message: Message, bot: Bot) -> None:
        if not await ensure_admin(message, bot):
            return
        target_user_id, error = parse_resend_target(message)
        if error:
            await message.answer(error)
            return
        assert target_user_id is not None
        challenge = verification_service.get_pending_for_group_user(message.chat.id, target_user_id)
        if not challenge:
            await message.answer("未找到该用户的待验证任务。")
            return
        chat = await bot.get_me()
        deep_link = f"https://t.me/{chat.username}?start=verify_{challenge.start_token}"
        sent = await message.answer(
            f"<a href='tg://user?id={target_user_id}'>用户</a> 请私聊机器人完成验证：{deep_link}",
            parse_mode="HTML",
        )
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
        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            if not await ensure_admin(message, bot):
                return
        text = (
            "/status 查看当前配置\n"
            "/enable 启用入群验证\n"
            "/disable 禁用入群验证\n"
            "/set_timeout <秒> 设置超时时间\n"
            "/resend [user_id] 或回复用户消息后 /resend\n"
            "/help 查看帮助\n\n"
            "机器人需要管理员权限：限制成员、封禁成员、删除消息（可选）。"
        )
        await message.answer(text)

    return router


def parse_timeout_command(text: str) -> tuple[int | None, str | None]:
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None, f"用法：/set_timeout <seconds>，范围 {TIMEOUT_MIN}-{TIMEOUT_MAX}"
    try:
        seconds = int(parts[1].strip())
    except ValueError:
        return None, "超时时间必须是整数。"
    if not TIMEOUT_MIN <= seconds <= TIMEOUT_MAX:
        return None, f"超时时间必须在 {TIMEOUT_MIN} 到 {TIMEOUT_MAX} 秒之间。"
    return seconds, None


def parse_resend_target(message: Message) -> tuple[int | None, str | None]:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, None
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        return None, "请回复目标用户消息后使用 /resend，或传入 /resend <user_id>。"
    try:
        return int(parts[1].strip()), None
    except ValueError:
        return None, "user_id 必须是整数。"
