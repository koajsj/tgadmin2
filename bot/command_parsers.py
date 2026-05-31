from __future__ import annotations

TIMEOUT_MIN = 60
TIMEOUT_MAX = 86400
AUTO_DELETE_MIN = 0
AUTO_DELETE_MAX = 86400


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


def parse_auto_delete_command(text: str) -> tuple[int | None, str | None]:
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None, f"用法：/set_autodelete <seconds>，范围 {AUTO_DELETE_MIN}-{AUTO_DELETE_MAX}"
    try:
        seconds = int(parts[1].strip())
    except ValueError:
        return None, "自动删除时间必须是整数。"
    if not AUTO_DELETE_MIN <= seconds <= AUTO_DELETE_MAX:
        return None, f"自动删除时间必须在 {AUTO_DELETE_MIN} 到 {AUTO_DELETE_MAX} 秒之间。"
    return seconds, None
