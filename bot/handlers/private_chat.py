from __future__ import annotations

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.models import ChallengeStatus
from bot.services import AuditService, MembershipService, VerificationService
from bot.storage import Repository


def build_private_router(
    repository: Repository,
    verification_service: VerificationService,
    membership_service: MembershipService,
    audit_service: AuditService,
) -> Router:
    router = Router(name="private-chat")

    @router.message(CommandStart(deep_link=True))
    async def start_verification(message: Message, command: CommandStart, bot: Bot) -> None:
        if not message.from_user or message.chat.type != ChatType.PRIVATE:
            return
        challenge, error = verification_service.validate_start_token(command.args, message.from_user.id)
        if error:
            await message.answer(error)
            return
        assert challenge is not None
        verification_service.set_user_chat_instance(challenge.id, message.chat.id.__str__())
        prompt = (
            "请手动编辑并回复下面这段验证文字。\n"
            f"原文：`{challenge.challenge_text}`\n"
            "规则：把三段内容倒序，并用半角 `-` 连接，全部改为大写。\n"
            "例如：`river 42 maple` -> `MAPLE-42-RIVER`\n"
            "请直接回复你的结果，不要附加其他内容。"
        )
        await message.answer(prompt, parse_mode="Markdown")
        audit_service.log(
            "challenge_opened_private",
            chat_id=challenge.chat_id,
            user_id=challenge.user_id,
            challenge_id=challenge.id,
        )

    @router.message(lambda message: message.chat.type == ChatType.PRIVATE and bool(message.text))
    async def handle_private_response(message: Message, bot: Bot) -> None:
        if not message.from_user or not message.text:
            return
        challenge = verification_service.get_latest_pending_for_user(message.from_user.id)
        if not challenge:
            await message.answer("你当前没有待完成的验证任务。请回到群里重新获取验证链接。")
            return
        if challenge.status != ChallengeStatus.PENDING:
            await message.answer("该验证任务已结束。")
            return
        if verification_service.validate_response(challenge, message.text):
            current = repository.get_challenge_by_id(challenge.id)
            if current.status != ChallengeStatus.PENDING:
                await message.answer("该验证任务已完成，请返回群组查看状态。")
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
                await message.answer("验证已通过，但机器人暂时无法解除限制。请联系群管理员处理。")
                return
            verification_service.mark_passed(challenge.id)
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
            await bot.send_message(challenge.chat_id, f"<a href='tg://user?id={challenge.user_id}'>用户</a> 验证通过，已恢复发言。", parse_mode="HTML")
            return

        attempts = verification_service.record_attempt(challenge.id)
        audit_service.log(
            "challenge_failed_attempt",
            chat_id=challenge.chat_id,
            user_id=challenge.user_id,
            challenge_id=challenge.id,
            attempt_count=attempts,
        )
        await message.answer("验证内容不正确，请按规则手动编辑后重新发送。")

    return router
