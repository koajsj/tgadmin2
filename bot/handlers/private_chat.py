from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from bot.models import ChallengeStatus
from bot.services.audit import AuditService
from bot.services.membership import MembershipService
from bot.services.verification import VerificationService
from bot.storage import Repository
from bot.utils import schedule_delete


def build_private_router(
    repository: Repository,
    verification_service: VerificationService,
    membership_service: MembershipService,
    audit_service: AuditService,
    max_failed_attempts: int,
) -> Router:
    router = Router(name="private-chat")

    @router.message(CommandStart(deep_link=True))
    async def start_verification(message: Message, command: CommandObject) -> None:
        if not message.from_user or message.chat.type != ChatType.PRIVATE:
            return
        challenge, error = verification_service.validate_start_token(command.args, message.from_user.id)
        if error:
            await message.answer(error)
            return
        assert challenge is not None
        verification_service.set_user_chat_instance(challenge.id, str(message.chat.id))
        repository.set_active_private_challenge(message.from_user.id, challenge.id)
        repository.record_user_seen(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            seen_at=datetime.now(timezone.utc).isoformat(),
        )
        prompt = (
            "请手动编辑并回复下面这段验证文字。\n"
            f"原文：`{challenge.challenge_text}`\n"
            "规则：把三段内容倒序，并用半角 `-` 连接，全部改成大写。\n"
            "示例：`river 42 maple` -> `MAPLE-42-RIVER`\n"
            "请直接回复结果，不要附加其他内容。"
        )
        await message.answer(prompt, parse_mode="Markdown")
        audit_service.log(
            "challenge_opened_private",
            chat_id=challenge.chat_id,
            user_id=challenge.user_id,
            challenge_id=challenge.id,
        )

    @router.message(CommandStart())
    async def start_without_link(message: Message) -> None:
        if message.chat.type != ChatType.PRIVATE:
            return
        await message.answer("请先从群里的验证链接进入私聊，再发送 /start。")

    @router.message(lambda message: message.chat.type == ChatType.PRIVATE and bool(message.text) and not message.text.startswith("/"))
    async def handle_private_response(message: Message, bot: Bot) -> None:
        if not message.from_user or not message.text:
            return
        repository.record_user_seen(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            seen_at=datetime.now(timezone.utc).isoformat(),
        )
        challenge = repository.get_active_private_challenge_for_user(message.from_user.id)
        if not challenge and repository.count_pending_challenges_for_user(message.from_user.id) == 1:
            challenge = verification_service.get_latest_pending_for_user(message.from_user.id)
            if challenge:
                repository.set_active_private_challenge(message.from_user.id, challenge.id)
        if not challenge:
            await message.answer("当前没有待完成的验证任务，请返回群里重新获取链接。")
            return
        if challenge.status != ChallengeStatus.PENDING:
            await message.answer("这条验证任务已经结束。")
            return
        if verification_service.validate_response(challenge, message.text):
            current = repository.get_challenge_by_id(challenge.id)
            if current.status != ChallengeStatus.PENDING:
                await message.answer("这条验证任务已经完成，请返回群里查看状态。")
                return
            result = await membership_service.unrestrict_member(bot, challenge.chat_id, challenge.user_id)
            if not result.success:
                audit_service.log(
                    "member_unrestrict_failed",
                    chat_id=challenge.chat_id,
                    user_id=challenge.user_id,
                    challenge_id=challenge.id,
                    error=result.detail,
                )
                await message.answer("验证通过，但机器人暂时无法解除限制，请联系群管理员处理。")
                return
            verification_service.mark_passed(challenge.id)
            repository.record_verification_result(
                user_id=challenge.user_id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
                success=True,
                seen_at=datetime.now(timezone.utc).isoformat(),
            )
            audit_service.log(
                "challenge_passed",
                chat_id=challenge.chat_id,
                user_id=challenge.user_id,
                challenge_id=challenge.id,
            )
            audit_service.log(
                "member_unrestricted",
                chat_id=challenge.chat_id,
                user_id=challenge.user_id,
                challenge_id=challenge.id,
            )
            await message.answer("验证通过，已自动解除群内限制。")
            repository.clear_active_private_challenge(message.from_user.id)
            group_notice = await bot.send_message(
                challenge.chat_id,
                f"<a href='tg://user?id={challenge.user_id}'>该用户</a> 验证通过，已恢复发言。",
                parse_mode="HTML",
            )
            group_settings = repository.ensure_group_settings(challenge.chat_id)
            schedule_delete(bot, group_notice, group_settings.auto_delete_seconds)
            return

        attempts = verification_service.record_attempt(challenge.id)
        repository.record_verification_result(
            user_id=challenge.user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            success=False,
            seen_at=datetime.now(timezone.utc).isoformat(),
        )
        audit_service.log(
            "challenge_failed_attempt",
            chat_id=challenge.chat_id,
            user_id=challenge.user_id,
            challenge_id=challenge.id,
            attempt_count=attempts,
        )
        if attempts >= max_failed_attempts:
            verification_service.mark_failed(challenge.id)
            repository.clear_active_private_challenge(message.from_user.id)
            result = await membership_service.kick_member(bot, challenge.chat_id, challenge.user_id)
            audit_service.log(
                "challenge_failed_locked",
                chat_id=challenge.chat_id,
                user_id=challenge.user_id,
                challenge_id=challenge.id,
                attempt_count=attempts,
                kick_success=result.success,
                error=result.detail,
            )
            if result.success:
                await message.answer("验证失败次数过多，已将你移出群组。")
            else:
                await message.answer("验证失败次数过多，但机器人踢人失败，请联系群管理员。")
            return

        remaining_attempts = max_failed_attempts - attempts
        await message.answer(f"验证内容不正确，请重新按规则编辑后发送。剩余尝试次数：{remaining_attempts}。")

    return router
