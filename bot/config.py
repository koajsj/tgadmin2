from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    bot_token: str
    db_path: Path
    verify_timeout_seconds: int = 600
    expire_action: str = "kick"
    log_level: str = "INFO"
    group_message_auto_delete_seconds: int = 0
    scheduler_interval_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise ValueError("BOT_TOKEN is required")

        db_path = Path(os.getenv("DB_PATH", "./data/bot.sqlite3")).expanduser()
        verify_timeout_seconds = _read_int("VERIFY_TIMEOUT_SECONDS", 600, 60, 86400)
        group_message_auto_delete_seconds = _read_int(
            "GROUP_MESSAGE_AUTO_DELETE_SECONDS", 0, 0, 86400
        )
        scheduler_interval_seconds = _read_int("SCHEDULER_INTERVAL_SECONDS", 30, 5, 3600)
        expire_action = os.getenv("EXPIRE_ACTION", "kick").strip().lower() or "kick"
        if expire_action not in {"kick", "restrict"}:
            raise ValueError("EXPIRE_ACTION must be 'kick' or 'restrict'")
        return cls(
            bot_token=bot_token,
            db_path=db_path,
            verify_timeout_seconds=verify_timeout_seconds,
            expire_action=expire_action,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            group_message_auto_delete_seconds=group_message_auto_delete_seconds,
            scheduler_interval_seconds=scheduler_interval_seconds,
        )


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def load_dotenv(dotenv_path: str = ".env") -> None:
    path = Path(dotenv_path)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
