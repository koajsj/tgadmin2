from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Settings
from bot.models import ConfigGroupSummary, UpdateResult
from bot.services.audit import AuditService
from bot.services.operations import UpdateService
from bot.services.security import OwnerSecurityService
from bot.services.system import SystemInspector
from bot.storage import Repository

CONFIG_PAGE_SIZE = 6
GROUP_PAGE_SIZE = 6
STATUS_CACHE_TTL_SECONDS = 8.0


@dataclass(slots=True)
class PendingInput:
    kind: str
    chat_id: int | None = None
    page: int = 1


def build_owner_router(
    repository: Repository,
    inspector: SystemInspector,
    update_service: UpdateService,
    owner_security_service: OwnerSecurityService,
    audit_service: AuditService,
    settings: Settings,
) -> Router:
    router = Router(name="owner-commands")
    pending_inputs: dict[int, PendingInput] = {}
    status_cache: dict[str, tuple[float, str]] = {}

    def is_owner(user_id: int | None) -> bool:
        return owner_security_service.is_owner(user_id)

    def owner_filter() -> tuple[object, ...]:
        return (F.chat.type == ChatType.PRIVATE, F.from_user.id == settings.owner_id)

    def callback_owner_filter() -> tuple[object, ...]:
        return (F.from_user.id == settings.owner_id,)

    def clear_pending_input(user_id: int) -> None:
        pending_inputs.pop(user_id, None)

    def set_pending_input(user_id: int, state: PendingInput) -> None:
        pending_inputs[user_id] = state

    def get_pending_input(user_id: int) -> PendingInput | None:
        return pending_inputs.get(user_id)

    def build_markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def button(text: str, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=data)

    def safe_label(value: str, limit: int = 24) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 1]}…"

    def format_timestamp(value: str | None) -> str:
        if not value:
            return "从未"
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")

    def format_expire_action(value: str) -> str:
        return "到期踢出" if value == "kick" else "到期禁言"

    def clamp(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(maximum, value))

    def primary_group_name(chat_id: int, title: str) -> str:
        alias = repository.get_group_alias(chat_id)
        if alias:
            return alias
        if title:
            return title
        return f"群 {chat_id}"

    def secondary_group_name(chat_id: int, title: str) -> str | None:
        alias = repository.get_group_alias(chat_id)
        if alias and title and alias != title:
            return title
        return None

    def format_config_name(group: ConfigGroupSummary) -> str:
        return primary_group_name(group.chat_id, group.title)

    def format_group_status(group: ConfigGroupSummary) -> str:
        return "已接入" if group.tracked else "仅预配置"

    def render_panel_text() -> str:
        tracked_groups = repository.count_groups()
        configurable_groups = repository.count_configurable_groups()
        users = repository.count_users()
        active_users = repository.count_active_users()
        stats = repository.get_verification_stats()
        pending_only = max(0, configurable_groups - tracked_groups)
        return "\n".join(
            [
                "<b>Owner 控制台</b>",
                "",
                f"已配置群：<b>{configurable_groups}</b>",
                f"已接入群：<b>{tracked_groups}</b>",
                f"仅预配置：<b>{pending_only}</b>",
                f"用户数：<b>{users}</b>",
                f"7 天活跃用户：<b>{active_users}</b>",
                f"今日通过验证：<b>{stats.today}</b>",
                f"最近 24 小时挑战：<b>{stats.last_24h}</b>",
                "",
                "现在可以先在私聊录入群 ID 预设参数，机器人之后进群会直接按这套配置运行。",
            ]
        )

    def render_panel_markup() -> InlineKeyboardMarkup:
        return build_markup(
            [
                [button("配置群", "own:cfg:1"), button("已接入群", "own:grp:1")],
                [button("系统状态", "own:stat"), button("更新代码", "own:upd")],
                [button("帮助", "own:help"), button("刷新面板", "own:home")],
            ]
        )

    def render_help_text() -> str:
        return "\n".join(
            [
                "<b>私聊控制说明</b>",
                "",
                "/panel 打开主面板",
                "/config 查看或配置群参数",
                "/config &lt;chat_id&gt; [备注] 直接录入群配置",
                "/group &lt;chat_id&gt; 打开指定群配置页",
                "/groups 查看已接入的群",
                "/status 查看运行状态",
                "/update 拉取最新代码",
                "/cancel 取消当前输入模式",
                "",
                "预配置流程：",
                "1. 点击“配置群”",
                "2. 点击“手动录入群 ID”",
                "3. 发送 <code>-1001234567890 业务群</code>",
                "4. 在详情页直接调整验证开关、超时、到期动作、自动删消息",
                "",
                "如果机器人还没进群，配置会先落库，之后进群自动生效。",
            ]
        )

    def render_help_markup() -> InlineKeyboardMarkup:
        return build_markup([[button("返回面板", "own:home"), button("配置群", "own:cfg:1")]])

    def build_config_list(page: int) -> tuple[str, InlineKeyboardMarkup]:
        total = repository.count_configurable_groups()
        total_pages = max(1, math.ceil(total / CONFIG_PAGE_SIZE))
        page = clamp(page, 1, total_pages)
        offset = (page - 1) * CONFIG_PAGE_SIZE
        groups = repository.list_configurable_groups(limit=CONFIG_PAGE_SIZE, offset=offset)

        lines = [
            "<b>群配置列表</b>",
            "",
            "这里会显示两类群：",
            "1. 机器人已接入并留下资料的群",
            "2. 你在私聊里手动录入、但机器人尚未进群的预配置群",
            "",
        ]
        if not groups:
            lines.append("当前还没有任何群配置。")
        else:
            for item in groups:
                label = escape(format_config_name(item))
                lines.append(
                    f"• {label} | {format_group_status(item)} | "
                    f"{'开启' if item.enabled else '关闭'} | 超时 {item.timeout_seconds}s"
                )

        lines.extend(
            [
                "",
                f"第 {page}/{total_pages} 页",
                "可直接点群进入详情，也可以手动发送群 ID 创建预配置。",
            ]
        )

        rows: list[list[InlineKeyboardButton]] = []
        for item in groups:
            rows.append(
                [
                    button(
                        safe_label(format_config_name(item)),
                        f"own:co:{item.chat_id}:{page}",
                    )
                ]
            )
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(button("上一页", f"own:cfg:{page - 1}"))
        if page < total_pages:
            nav.append(button("下一页", f"own:cfg:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([button("手动录入群 ID", "own:cfgm"), button("返回面板", "own:home")])
        return "\n".join(lines), build_markup(rows)

    def build_tracked_groups_list(page: int) -> tuple[str, InlineKeyboardMarkup]:
        total = repository.count_groups()
        total_pages = max(1, math.ceil(total / GROUP_PAGE_SIZE))
        page = clamp(page, 1, total_pages)
        offset = (page - 1) * GROUP_PAGE_SIZE
        groups = repository.list_groups(limit=GROUP_PAGE_SIZE, offset=offset)

        lines = ["<b>已接入群列表</b>", ""]
        if not groups:
            lines.append("当前还没有已接入群。")
        else:
            for item in groups:
                label = escape(primary_group_name(item.chat_id, item.title))
                lines.append(
                    f"• {label} | 成员 {item.member_count} | 管理员 {item.admin_count} | "
                    f"{'开启' if item.verification_enabled else '关闭'}"
                )
        lines.extend(["", f"第 {page}/{total_pages} 页"])

        rows: list[list[InlineKeyboardButton]] = []
        for item in groups:
            rows.append(
                [
                    button(
                        safe_label(primary_group_name(item.chat_id, item.title)),
                        f"own:co:{item.chat_id}:{page}",
                    )
                ]
            )
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(button("上一页", f"own:grp:{page - 1}"))
        if page < total_pages:
            nav.append(button("下一页", f"own:grp:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([button("配置群", "own:cfg:1"), button("返回面板", "own:home")])
        return "\n".join(lines), build_markup(rows)

    def build_config_detail(chat_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
        repository.ensure_group_settings(chat_id)
        config = repository.get_configurable_group(chat_id)
        settings_record = repository.get_group_settings(chat_id)
        if config is None or settings_record is None:
            raise ValueError("group config not found")

        tracked_summary = repository.get_group_summary(chat_id)
        alias = repository.get_group_alias(chat_id)
        display_name = primary_group_name(chat_id, config.title)
        original_title = secondary_group_name(chat_id, config.title)
        pending_count = repository.count_pending_challenges(chat_id)

        lines = [
            "<b>群配置详情</b>",
            "",
            f"名称：<b>{escape(display_name)}</b>",
            f"群 ID：<code>{chat_id}</code>",
            f"接入状态：<b>{format_group_status(config)}</b>",
        ]
        if original_title:
            lines.append(f"原始群名：{escape(original_title)}")
        if alias:
            lines.append(f"备注：{escape(alias)}")
        lines.extend(
            [
                f"验证开关：<b>{'开启' if settings_record.enabled else '关闭'}</b>",
                f"超时：<b>{settings_record.timeout_seconds}</b> 秒",
                f"到期动作：<b>{format_expire_action(settings_record.expire_action)}</b>",
                f"自动删消息：<b>{settings_record.auto_delete_seconds}</b> 秒",
                f"待验证人数：<b>{pending_count}</b>",
            ]
        )
        if tracked_summary is not None:
            lines.extend(
                [
                    f"成员数：<b>{tracked_summary.member_count}</b>",
                    f"管理员数：<b>{tracked_summary.admin_count}</b>",
                    f"最近活跃：<b>{format_timestamp(tracked_summary.last_active_at)}</b>",
                    f"风险等级：<b>{escape(tracked_summary.risk_level)}</b>",
                ]
            )
        else:
            lines.extend(
                [
                    "成员数：<b>未采集</b>",
                    "管理员数：<b>未采集</b>",
                    "最近活跃：<b>未采集</b>",
                    "说明：机器人未在该群中，当前修改的是预配置参数。",
                ]
            )

        markup = build_markup(
            [
                [
                    button(
                        "关闭验证" if settings_record.enabled else "开启验证",
                        f"own:ct:{chat_id}:{page}",
                    ),
                    button(
                        "切换到禁言" if settings_record.expire_action == "kick" else "切换到踢出",
                        f"own:ca:{chat_id}:{page}",
                    ),
                ],
                [button("超时 -1m", f"own:tt:{chat_id}:-60:{page}"), button("超时 +1m", f"own:tt:{chat_id}:60:{page}")],
                [button("超时 -5m", f"own:tt:{chat_id}:-300:{page}"), button("超时 +5m", f"own:tt:{chat_id}:300:{page}")],
                [button("删消息 关", f"own:az:{chat_id}:{page}"), button("删消息 -30s", f"own:ad:{chat_id}:-30:{page}"), button("删消息 +30s", f"own:ad:{chat_id}:30:{page}")],
                [button("删消息 -5m", f"own:ad:{chat_id}:-300:{page}"), button("删消息 +5m", f"own:ad:{chat_id}:300:{page}")],
                [button("修改备注", f"own:ae:{chat_id}:{page}"), button("清空备注", f"own:ac:{chat_id}:{page}")],
                [button("刷新", f"own:cr:{chat_id}:{page}"), button("返回列表", f"own:cfg:{page}")],
            ]
        )
        return "\n".join(lines), markup

    def render_status_text(*, fresh: bool) -> str:
        cached = status_cache.get("status")
        now = time.monotonic()
        if not fresh and cached and (now - cached[0]) < STATUS_CACHE_TTL_SECONDS:
            return cached[1]

        runtime = inspector.runtime_snapshot()
        database = inspector.database_snapshot()
        redis = inspector.redis_snapshot()
        git = inspector.git_snapshot(fresh_remote=fresh)
        lines = [
            "<b>系统状态</b>",
            "",
            f"主机：<b>{escape(runtime.hostname)}</b>",
            f"平台：<b>{escape(runtime.platform)}</b>",
            f"运行时长：<b>{escape(inspector.format_duration(runtime.uptime_seconds))}</b>",
            f"CPU：<b>{runtime.cpu_percent:.1f}%</b>",
            (
                "内存：<b>"
                f"{escape(inspector.format_bytes(runtime.memory_used))}"
                f" / {escape(inspector.format_bytes(runtime.memory_total))}"
                f" ({runtime.memory_percent:.1f}%)"
                "</b>"
            ),
            (
                "磁盘：<b>"
                f"{escape(inspector.format_bytes(runtime.disk_used))}"
                f" / {escape(inspector.format_bytes(runtime.disk_total))}"
                f" ({runtime.disk_percent:.1f}%)"
                "</b>"
            ),
            f"网络：<b>↑ {escape(inspector.format_bytes(runtime.net_sent))} / ↓ {escape(inspector.format_bytes(runtime.net_recv))}</b>",
            (
                "负载：<b>"
                f"{runtime.load_1m if runtime.load_1m is not None else '-'} / "
                f"{runtime.load_5m if runtime.load_5m is not None else '-'} / "
                f"{runtime.load_15m if runtime.load_15m is not None else '-'}"
                "</b>"
            ),
            "",
            f"数据库：<b>{escape(database.path)}</b>",
            f"数据库大小：<b>{escape(inspector.format_bytes(database.size_bytes))}</b>",
            f"表数量：<b>{database.table_count}</b>",
            f"完整性：<b>{'正常' if database.integrity_ok else '异常'}</b>",
            f"Redis：<b>{'已连接' if redis.reachable else redis.detail}</b>",
            "",
            f"分支：<b>{escape(git.branch)}</b>",
            f"当前提交：<code>{escape(git.current_revision[:12])}</code>",
            f"远端提交：<code>{escape((git.latest_revision or 'unknown')[:12])}</code>",
            f"工作区状态：<b>{'有未提交修改' if git.is_dirty else '干净'}</b>",
        ]
        text = "\n".join(lines)
        status_cache["status"] = (now, text)
        return text

    def render_status_markup() -> InlineKeyboardMarkup:
        return build_markup(
            [
                [button("刷新状态", "own:statf"), button("更新代码", "own:upd")],
                [button("配置群", "own:cfg:1"), button("返回面板", "own:home")],
            ]
        )

    def render_update_prompt(token: str) -> tuple[str, InlineKeyboardMarkup]:
        text = "\n".join(
            [
                "<b>确认更新</b>",
                "",
                "将执行：",
                "1. 拉取远端最新代码",
                "2. 按需安装依赖",
                "3. 运行数据库迁移",
                "4. 给出重启方式",
                "",
                f"本次确认码：<code>{token}</code>",
                "5 分钟内有效。点击按钮可直接确认，也可以手动发送 /update 确认码。",
            ]
        )
        markup = build_markup(
            [
                [button("确认更新", f"own:upc:{token}"), button("取消", "own:home")],
            ]
        )
        return text, markup

    def render_update_result(result: UpdateResult) -> tuple[str, InlineKeyboardMarkup]:
        status_text = "成功" if result.success else "失败"
        lines = [
            f"<b>更新{status_text}</b>",
            "",
            f"当前提交：<code>{escape(result.current_revision[:12])}</code>",
            f"远端提交：<code>{escape((result.latest_revision or 'unknown')[:12])}</code>",
            f"重启方式：<b>{escape(result.restarted_with)}</b>",
            "",
            "执行步骤：",
        ]
        lines.extend(f"• {escape(step)}" for step in result.steps)
        if result.error:
            lines.extend(["", f"错误：<b>{escape(result.error)}</b>"])
        if result.output:
            lines.extend(["", "<b>命令输出摘要</b>", f"<pre>{escape(result.output[-2500:])}</pre>"])
        markup = build_markup(
            [
                [button("查看状态", "own:statf"), button("返回面板", "own:home")],
                [button("再次更新", "own:upd"), button("配置群", "own:cfg:1")],
            ]
        )
        return "\n".join(lines), markup

    async def show_message(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        await message.answer(text, reply_markup=markup)

    async def replace_message(target: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await target.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            await target.answer(text, reply_markup=markup)

    async def show_panel(message: Message) -> None:
        clear_pending_input(message.from_user.id)
        await show_message(message, render_panel_text(), render_panel_markup())

    async def show_config_list(message: Message, page: int = 1) -> None:
        clear_pending_input(message.from_user.id)
        text, markup = build_config_list(page)
        await show_message(message, text, markup)

    async def show_config_detail(message: Message, chat_id: int, page: int = 1) -> None:
        clear_pending_input(message.from_user.id)
        text, markup = build_config_detail(chat_id, page)
        await show_message(message, text, markup)

    async def show_tracked_groups(message: Message, page: int = 1) -> None:
        clear_pending_input(message.from_user.id)
        text, markup = build_tracked_groups_list(page)
        await show_message(message, text, markup)

    @router.message(*owner_filter(), Command("start"))
    @router.message(*owner_filter(), Command("panel"))
    @router.message(*owner_filter(), Command("owner"))
    @router.message(*owner_filter(), Command("dashboard"))
    async def panel_command(message: Message) -> None:
        await show_panel(message)

    @router.message(*owner_filter(), Command("help"))
    async def help_command(message: Message) -> None:
        clear_pending_input(message.from_user.id)
        await show_message(message, render_help_text(), render_help_markup())

    @router.message(*owner_filter(), Command("cancel"))
    async def cancel_command(message: Message) -> None:
        clear_pending_input(message.from_user.id)
        await show_message(message, "已取消当前输入模式。", build_markup([[button("返回面板", "own:home")]]))

    @router.message(*owner_filter(), Command("status"))
    async def status_command(message: Message) -> None:
        clear_pending_input(message.from_user.id)
        await show_message(message, render_status_text(fresh=False), render_status_markup())

    @router.message(*owner_filter(), Command("groups"))
    async def groups_command(message: Message) -> None:
        await show_tracked_groups(message, 1)

    @router.message(*owner_filter(), Command("group"))
    async def group_command(message: Message) -> None:
        args = _command_args(message.text or "")
        chat_id = _parse_chat_id(args)
        if chat_id is None:
            await show_message(
                message,
                "用法：/group <chat_id>",
                build_markup([[button("配置群", "own:cfg:1"), button("返回面板", "own:home")]]),
            )
            return
        await show_config_detail(message, chat_id, 1)

    @router.message(*owner_filter(), Command("config"))
    @router.message(*owner_filter(), Command("settings"))
    async def config_command(message: Message) -> None:
        args = _command_args(message.text or "")
        if not args:
            await show_config_list(message, 1)
            return
        chat_id, alias = _parse_config_input(args)
        if chat_id is None:
            await show_message(
                message,
                "用法：/config <chat_id> [备注]",
                build_markup([[button("手动录入群 ID", "own:cfgm"), button("返回面板", "own:home")]]),
            )
            return
        repository.ensure_group_settings(chat_id)
        if alias:
            repository.set_group_alias(chat_id, alias)
        audit_service.log(
            "owner_config_prepared",
            chat_id=chat_id,
            user_id=message.from_user.id,
            alias=alias or None,
        )
        await show_config_detail(message, chat_id, 1)

    @router.message(*owner_filter(), Command("update"))
    async def update_command(message: Message) -> None:
        args = _command_args(message.text or "")
        if args:
            if not owner_security_service.verify_confirmation(message.from_user.id, "update", args):
                await show_message(
                    message,
                    "确认码无效或已过期，请重新发送 /update 获取新的确认。",
                    build_markup([[button("重新获取确认", "own:upd"), button("返回面板", "own:home")]]),
                )
                return
            await show_message(message, "正在执行更新，请稍候……")
            result = await update_service.run_update(settings.db_path)
            text, markup = render_update_result(result)
            await show_message(message, text, markup)
            return

        token = owner_security_service.issue_confirmation(message.from_user.id, "update")
        text, markup = render_update_prompt(token)
        await show_message(message, text, markup)

    @router.message(*owner_filter(), F.text)
    async def private_input_handler(message: Message) -> None:
        if not is_owner(message.from_user.id):
            return
        state = get_pending_input(message.from_user.id)
        if state is None:
            return

        text = (message.text or "").strip()
        if not text:
            return

        if state.kind == "group":
            chat_id, alias = _parse_config_input(text)
            if chat_id is None:
                await show_message(
                    message,
                    "请输入正确的群 ID。可以附带备注，例如：<code>-1001234567890 业务群</code>",
                    build_markup([[button("取消", "own:home")]]),
                )
                return
            repository.ensure_group_settings(chat_id)
            if alias:
                repository.set_group_alias(chat_id, alias)
            clear_pending_input(message.from_user.id)
            audit_service.log(
                "owner_config_prepared",
                chat_id=chat_id,
                user_id=message.from_user.id,
                alias=alias or None,
            )
            await show_config_detail(message, chat_id, state.page)
            return

        if state.kind == "alias" and state.chat_id is not None:
            alias = text.strip()
            if alias in {"-", "清空"}:
                alias = ""
            repository.set_group_alias(state.chat_id, alias or None)
            clear_pending_input(message.from_user.id)
            audit_service.log(
                "owner_alias_updated",
                chat_id=state.chat_id,
                user_id=message.from_user.id,
                alias=alias or None,
            )
            await show_config_detail(message, state.chat_id, state.page)

    @router.callback_query(*callback_owner_filter(), F.data == "own:home")
    async def panel_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        clear_pending_input(callback.from_user.id)
        await replace_message(callback.message, render_panel_text(), render_panel_markup())
        await callback.answer()

    @router.callback_query(*callback_owner_filter(), F.data == "own:help")
    async def help_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        clear_pending_input(callback.from_user.id)
        await replace_message(callback.message, render_help_text(), render_help_markup())
        await callback.answer()

    @router.callback_query(*callback_owner_filter(), F.data == "own:stat")
    @router.callback_query(*callback_owner_filter(), F.data == "own:statf")
    async def status_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        clear_pending_input(callback.from_user.id)
        fresh = callback.data == "own:statf"
        await replace_message(
            callback.message,
            render_status_text(fresh=fresh),
            render_status_markup(),
        )
        await callback.answer("已刷新" if fresh else "")

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:cfg:"))
    async def config_list_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            page = int((callback.data or "").rsplit(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer("参数错误", show_alert=True)
            return
        clear_pending_input(callback.from_user.id)
        text, markup = build_config_list(page)
        await replace_message(callback.message, text, markup)
        await callback.answer()

    @router.callback_query(*callback_owner_filter(), F.data == "own:cfgm")
    async def config_manual_input_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        set_pending_input(callback.from_user.id, PendingInput(kind="group", page=1))
        await replace_message(
            callback.message,
            "\n".join(
                [
                    "<b>手动录入群 ID</b>",
                    "",
                    "请直接发送群 ID，支持顺带备注。",
                    "示例：<code>-1001234567890 业务群</code>",
                    "发送 /cancel 可取消。",
                ]
            ),
            build_markup([[button("返回列表", "own:cfg:1"), button("返回面板", "own:home")]]),
        )
        await callback.answer()

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:grp:"))
    async def tracked_groups_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            page = int((callback.data or "").rsplit(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer("参数错误", show_alert=True)
            return
        clear_pending_input(callback.from_user.id)
        text, markup = build_tracked_groups_list(page)
        await replace_message(callback.message, text, markup)
        await callback.answer()

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:co:"))
    async def config_open_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        clear_pending_input(callback.from_user.id)
        text, markup = build_config_detail(chat_id, page)
        await replace_message(callback.message, text, markup)
        await callback.answer()

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:ct:"))
    async def config_toggle_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        current = repository.ensure_group_settings(chat_id)
        repository.update_group_settings(chat_id, enabled=not current.enabled)
        audit_service.log(
            "owner_group_toggle",
            chat_id=chat_id,
            user_id=callback.from_user.id,
            enabled=not current.enabled,
        )
        text, markup = build_config_detail(chat_id, page)
        await replace_message(callback.message, text, markup)
        await callback.answer("已更新")

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:ca:"))
    async def config_action_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        current = repository.ensure_group_settings(chat_id)
        next_action = "restrict" if current.expire_action == "kick" else "kick"
        repository.update_group_settings(chat_id, expire_action=next_action)
        audit_service.log(
            "owner_group_expire_action_updated",
            chat_id=chat_id,
            user_id=callback.from_user.id,
            expire_action=next_action,
        )
        text, markup = build_config_detail(chat_id, page)
        await replace_message(callback.message, text, markup)
        await callback.answer("已更新")

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:tt:"))
    async def config_timeout_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, delta_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            delta = int(delta_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        current = repository.ensure_group_settings(chat_id)
        next_timeout = clamp(current.timeout_seconds + delta, 60, 86400)
        repository.update_group_settings(chat_id, timeout_seconds=next_timeout)
        audit_service.log(
            "owner_group_timeout_updated",
            chat_id=chat_id,
            user_id=callback.from_user.id,
            timeout_seconds=next_timeout,
        )
        text, markup = build_config_detail(chat_id, page)
        await replace_message(callback.message, text, markup)
        await callback.answer(f"超时 {next_timeout}s")

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:ad:"))
    async def config_auto_delete_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, delta_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            delta = int(delta_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        current = repository.ensure_group_settings(chat_id)
        next_auto_delete = clamp(current.auto_delete_seconds + delta, 0, 86400)
        repository.update_group_settings(chat_id, auto_delete_seconds=next_auto_delete)
        audit_service.log(
            "owner_group_auto_delete_updated",
            chat_id=chat_id,
            user_id=callback.from_user.id,
            auto_delete_seconds=next_auto_delete,
        )
        text, markup = build_config_detail(chat_id, page)
        await replace_message(callback.message, text, markup)
        await callback.answer(f"自动删 {next_auto_delete}s")

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:az:"))
    async def config_auto_delete_zero_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        repository.update_group_settings(chat_id, auto_delete_seconds=0)
        audit_service.log(
            "owner_group_auto_delete_updated",
            chat_id=chat_id,
            user_id=callback.from_user.id,
            auto_delete_seconds=0,
        )
        text, markup = build_config_detail(chat_id, page)
        await replace_message(callback.message, text, markup)
        await callback.answer("已关闭自动删消息")

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:ae:"))
    async def config_alias_edit_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        set_pending_input(callback.from_user.id, PendingInput(kind="alias", chat_id=chat_id, page=page))
        current_alias = repository.get_group_alias(chat_id) or "未设置"
        await replace_message(
            callback.message,
            "\n".join(
                [
                    "<b>修改群备注</b>",
                    "",
                    f"群 ID：<code>{chat_id}</code>",
                    f"当前备注：<b>{escape(current_alias)}</b>",
                    "请直接发送新备注。",
                    "发送 <code>-</code> 或“清空”可删除备注，发送 /cancel 可取消。",
                ]
            ),
            build_markup([[button("返回详情", f"own:cr:{chat_id}:{page}"), button("返回面板", "own:home")]]),
        )
        await callback.answer()

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:ac:"))
    async def config_alias_clear_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        repository.set_group_alias(chat_id, None)
        audit_service.log(
            "owner_alias_updated",
            chat_id=chat_id,
            user_id=callback.from_user.id,
            alias=None,
        )
        text, markup = build_config_detail(chat_id, page)
        await replace_message(callback.message, text, markup)
        await callback.answer("备注已清空")

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:cr:"))
    async def config_refresh_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            _, _, chat_text, page_text = (callback.data or "").split(":")
            chat_id = int(chat_text)
            page = int(page_text)
        except ValueError:
            await callback.answer("参数错误", show_alert=True)
            return
        clear_pending_input(callback.from_user.id)
        text, markup = build_config_detail(chat_id, page)
        await replace_message(callback.message, text, markup)
        await callback.answer("已刷新")

    @router.callback_query(*callback_owner_filter(), F.data == "own:upd")
    async def update_prompt_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        clear_pending_input(callback.from_user.id)
        token = owner_security_service.issue_confirmation(callback.from_user.id, "update")
        text, markup = render_update_prompt(token)
        await replace_message(callback.message, text, markup)
        await callback.answer()

    @router.callback_query(*callback_owner_filter(), F.data.startswith("own:upc:"))
    async def update_confirm_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        token = (callback.data or "").rsplit(":", 1)[-1]
        if not owner_security_service.verify_confirmation(callback.from_user.id, "update", token):
            await callback.answer("确认码无效或已过期", show_alert=True)
            return
        await replace_message(callback.message, "正在执行更新，请稍候……")
        await callback.answer()
        result = await update_service.run_update(settings.db_path)
        text, markup = render_update_result(result)
        await replace_message(callback.message, text, markup)

    return router


def _command_args(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return ""
    return parts[1].strip()


def _parse_chat_id(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value != 0 else None


def _parse_config_input(raw: str) -> tuple[int | None, str]:
    text = raw.strip()
    if not text:
        return None, ""
    parts = text.split(maxsplit=1)
    chat_id = _parse_chat_id(parts[0])
    alias = parts[1].strip() if len(parts) > 1 else ""
    return chat_id, alias
