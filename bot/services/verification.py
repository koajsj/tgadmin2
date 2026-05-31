from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime

from bot.models import ChallengeStatus, VerificationChallenge
from bot.storage import Repository

WORD_POOL = (
    "amber",
    "birch",
    "cinder",
    "delta",
    "ember",
    "fjord",
    "grove",
    "harbor",
    "iris",
    "juniper",
    "kepler",
    "lilac",
    "maple",
    "nova",
    "onyx",
    "pine",
    "quartz",
    "river",
    "spruce",
    "thunder",
    "umbra",
    "velvet",
    "willow",
    "zenith",
)


@dataclass(slots=True)
class ChallengeSpec:
    display_text: str
    expected_response: str


class VerificationService:
    def __init__(self, repository: Repository):
        self._repository = repository

    def get_or_create_challenge(
        self,
        *,
        chat_id: int,
        user_id: int,
        display_name: str,
        join_message_id: int | None,
        user_chat_instance: str | None,
        timeout_seconds: int,
        now: datetime | None = None,
    ) -> tuple[VerificationChallenge, bool]:
        active = self._repository.get_active_challenge(chat_id, user_id, now=now)
        if active:
            return active, False

        spec = build_challenge(display_name)
        token = secrets.token_urlsafe(18)
        challenge = self._repository.create_challenge(
            chat_id=chat_id,
            user_id=user_id,
            user_chat_instance=user_chat_instance,
            join_message_id=join_message_id,
            start_token=token,
            challenge_text=spec.display_text,
            expected_response=spec.expected_response,
            timeout_seconds=timeout_seconds,
        )
        return challenge, True

    def validate_start_token(
        self,
        raw_start_param: str | None,
        user_id: int,
    ) -> tuple[VerificationChallenge | None, str | None]:
        if not raw_start_param or not raw_start_param.startswith("verify_"):
            return None, "请使用群里的验证链接进入私聊。"

        token = raw_start_param.removeprefix("verify_")
        challenge = self._repository.get_pending_challenge_by_token(token)
        if not challenge:
            return None, "验证任务不存在或已失效。"
        if challenge.user_id != user_id:
            return None, "这个验证链接不属于你。"
        if challenge.status != ChallengeStatus.PENDING:
            return None, "该验证任务已结束。"
        if challenge.expires_at_dt() <= datetime.now(challenge.expires_at_dt().tzinfo):
            return None, "该验证任务已过期，请回到群里重新获取。"
        return challenge, None

    def validate_response(self, challenge: VerificationChallenge, response_text: str) -> bool:
        return normalize_response(challenge.expected_response) == normalize_response(response_text)

    def record_attempt(self, challenge_id: int) -> int:
        return self._repository.increment_attempt_count(challenge_id)

    def mark_passed(self, challenge_id: int) -> VerificationChallenge:
        return self._repository.mark_passed(challenge_id)

    def mark_expired(self, challenge_id: int) -> VerificationChallenge:
        return self._repository.mark_expired(challenge_id)

    def mark_failed(self, challenge_id: int) -> VerificationChallenge:
        return self._repository.mark_failed(challenge_id)

    def set_prompt_message_id(self, challenge_id: int, message_id: int) -> None:
        self._repository.set_prompt_message_id(challenge_id, message_id)

    def set_user_chat_instance(self, challenge_id: int, user_chat_instance: str | None) -> None:
        self._repository.set_user_chat_instance(challenge_id, user_chat_instance)

    def get_latest_pending_for_user(self, user_id: int) -> VerificationChallenge | None:
        challenge = self._repository.get_pending_challenge_for_user(user_id)
        if challenge and challenge.expires_at_dt() <= datetime.now(challenge.expires_at_dt().tzinfo):
            return None
        return challenge

    def get_pending_for_group_user(self, chat_id: int, user_id: int) -> VerificationChallenge | None:
        return self._repository.find_pending_challenge(chat_id, user_id)


def build_challenge(display_name: str) -> ChallengeSpec:
    name_fragment = sanitize_name_fragment(display_name)
    first_word, second_word = pick_words(name_fragment)
    number = f"{secrets.randbelow(90) + 10:02d}"
    display_parts = [first_word, number, second_word]
    return ChallengeSpec(
        display_text=" ".join(display_parts),
        expected_response="-".join(part.upper() for part in reversed(display_parts)),
    )


def pick_words(name_fragment: str) -> tuple[str, str]:
    first_index = secrets.randbelow(len(WORD_POOL))
    second_index = secrets.randbelow(len(WORD_POOL))
    first_word = WORD_POOL[first_index]
    second_word = WORD_POOL[second_index]
    if name_fragment and name_fragment not in {first_word, second_word}:
        return first_word, name_fragment
    if first_word == second_word:
        second_word = WORD_POOL[(second_index + 1) % len(WORD_POOL)]
    return first_word, second_word


def sanitize_name_fragment(name: str) -> str:
    letters = [char.lower() for char in name if char.isalnum()]
    return "".join(letters[:8])


def normalize_response(value: str) -> str:
    compact = value.strip().replace("_", "-")
    compact = compact.replace(" - ", "-").replace("- ", "-").replace(" -", "-")
    return " ".join(part for part in compact.split() if part).upper()
