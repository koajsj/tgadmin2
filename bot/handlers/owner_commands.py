from __future__ import annotations

import asyncio
import html
import math
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.services.audit import AuditService
from bot.services.operations import UpdateService
from bot.services.security import OwnerSecurityService
from bot.services.system import SystemInspector
from bot.storage import Repository

STATUS_PAGE_COUNT = 4
GROUP_PAGE_SIZE = 5


def build_owner_router(
    repository: Repository,
    inspector: SystemInspector,
    update_service: UpdateService,
    security_service: OwnerSecurityService,
    audit_service: AuditService,
    settings: Settings,
) -> Router:
    router = Router(name="owner-commands")

    async def ensure_owner(message: Message) -> bool:
        if message.chat.type != ChatType.PRIVATE:
            return False
        user_id = message.from_user.id if message.from_user else None
        if not security_service.is_owner(user_id):
            await message.answer("仅 OWNER 可使用此命令。")
            return False
        return True

    async def render_dashboard() -> str:
        runtime = await asyncio.to_thread(inspector.runtime_snapshot)
        database = await asyncio.to_thread(inspector.database_snapshot)
        redis = await asyncio.to_thread(inspector.redis_snapshot)
        git = await asyncio.to_thread(inspector.git_snapshot)
        verification = await asyncio.to_thread(repository.get_verification_stats)
        total_groups = repository.count_groups()
        total_users = repository.count_users()
        active_users = repository.count_active_users()
        verified_users = repository.count_verified_users()
        failed_users = repository.count_failed_verification_users()
        total_messages = repository.count_total_messages()
        logs = repository.count_audit_logs()
        errors = repository.count_recent_errors()
        restarts = repository.count_recent_restarts()
        group_admins = repository.sum_group_admin_count()
        group_members = repository.sum_group_member_count()

        latest_label = _latest_label(git.current_revision, git.latest_revision)
        return "\n".join(
            [
                "<b>OWNER 仪表盘</b>",
                f"状态：<b>运行中</b>",
                f"运行时长：{inspector.format_duration(runtime.uptime_seconds)}",
                f"版本：{_short_hash(git.current_revision)}",
                f"最新：{latest_label}",
                f"CPU：{runtime.cpu_percent:.1f}%",
                f"内存：{inspector.format_bytes(runtime.memory_used)} / {inspector.format_bytes(runtime.memory_total)} ({runtime.memory_percent:.1f}%)",
                f"磁盘：{inspector.format_bytes(runtime.disk_used)} / {inspector.format_bytes(runtime.disk_total)} ({runtime.disk_percent:.1f}%)",
                f"数据库：{'正常' if database.integrity_ok else '异常'} | {inspector.format_bytes(database.size_bytes)} | 表 {database.table_count} 个",
                f"Redis：{_render_redis(redis)}",
                f"群数量：{total_groups} | 群成员总量：{group_members} | 管理员总量：{group_admins}",
                f"用户数量：{total_users} | 活跃用户：{active_users} | 验证成功用户：{verified_users} | 验证失败用户：{failed_users}",
                f"验证：总 {verification.total} | 今日 {verification.today} | 成功率 {verification.success_rate:.1f}% | 失败率 {verification.failure_rate:.1f}% | 超时率 {verification.timeout_rate:.1f}%",
                f"消息数量：{total_messages}",
                f"日志数量：{logs} | 近24h异常：{errors} | 近7天重启：{restarts}",
            ]
        )

    async def render_status_page(page: int) -> str:
        runtime = await asyncio.to_thread(inspector.runtime_snapshot)
        database = await asyncio.to_thread(repository.build_database_snapshot)
        redis = await asyncio.to_thread(inspector.redis_snapshot)
        git = await asyncio.to_thread(inspector.git_snapshot)
        verification = await asyncio.to_thread(repository.get_verification_stats)
        total_groups = repository.count_groups()
        total_users = repository.count_users()
        active_users = repository.count_active_users()
        verified_users = repository.count_verified_users()
        failed_users = repository.count_failed_verification_users()
        total_messages = repository.count_total_messages()
        logs = repository.count_audit_logs()
        errors = repository.count_recent_errors()
        restarts = repository.count_recent_restarts()
        recent_new = repository.count_recent_new_users()
        recent_banned = repository.count_recent_banned_users()

        pages = [
            "\n".join(
                [
                    "<b>状态 1/4 - 服务器</b>",
                    f"主机：{html.escape(runtime.hostname)}",
                    f"平台：{html.escape(runtime.platform)}",
                    f"运行时长：{inspector.format_duration(runtime.uptime_seconds)}",
                    f"CPU：{runtime.cpu_percent:.1f}%",
                    f"内存：{inspector.format_bytes(runtime.memory_used)} / {inspector.format_bytes(runtime.memory_total)} ({runtime.memory_percent:.1f}%)",
                    f"磁盘：{inspector.format_bytes(runtime.disk_used)} / {inspector.format_bytes(runtime.disk_total)} ({runtime.disk_percent:.1f}%)",
                    f"网络：↑ {inspector.format_bytes(runtime.net_sent)} | ↓ {inspector.format_bytes(runtime.net_recv)}",
                    f"负载：{_render_load(runtime)}",
                ]
            ),
            "\n".join(
                [
                    "<b>状态 2/4 - 机器人</b>",
                    f"当前版本：{_short_hash(git.current_revision)}",
                    f"最新版本：{_latest_label(git.current_revision, git.latest_revision)}",
                    f"仓库状态：{'有本地修改' if git.is_dirty else '干净'}",
                    f"运行时长：{inspector.format_duration(runtime.uptime_seconds)}",
                    f"消息数量：{total_messages}",
                    f"群数量：{total_groups}",
                    f"用户数量：{total_users}",
                    f"管理员总量：{repository.sum_group_admin_count()}",
                    f"OWNER 数量：1",
                ]
            ),
            "\n".join(
                [
                    "<b>状态 3/4 - 验证系统</b>",
                    f"总验证：{verification.total}",
                    f"今日验证：{verification.today}",
                    f"最近24小时：{verification.last_24h}",
                    f"最近7天：{verification.last_7d}",
                    f"成功：{verification.success} ({verification.success_rate:.1f}%)",
                    f"失败：{verification.failure} ({verification.failure_rate:.1f}%)",
                    f"超时：{verification.timeout} ({verification.timeout_rate:.1f}%)",
                    f"活跃用户：{active_users}",
                    f"近期新增用户：{recent_new}",
                ]
            ),
            "\n".join(
                [
                    "<b>状态 4/4 - 数据库与日志</b>",
                    f"数据库：{'正常' if database.integrity_ok else '异常'}",
                    f"数据库大小：{inspector.format_bytes(database.size_bytes)}",
                    f"表数量：{database.table_count}",
                    f"Redis：{_render_redis(redis)}",
                    f"总日志：{logs}",
                    f"近24小时异常：{errors}",
                    f"近7天重启：{restarts}",
                    f"近期封禁用户：{recent_banned}",
                    f"验证成功用户：{verified_users} | 验证失败用户：{failed_users}",
                ]
            ),
        ]
        return pages[max(0, min(page - 1, STATUS_PAGE_COUNT - 1))]

    async def render_groups_page(page: int) -> tuple[str, int]:
        total = repository.count_groups()
        total_pages = max(1, math.ceil(total / GROUP_PAGE_SIZE))
        page = max(1, min(page, total_pages))
        offset = (page - 1) * GROUP_PAGE_SIZE
        groups = repository.list_groups(limit=GROUP_PAGE_SIZE, offset=offset)
        if not groups:
            return ("<b>群监控中心</b>\n当前没有已追踪的群。", total_pages)
        lines = ["<b>群监控中心</b>"]
        for index, group in enumerate(groups, start=offset + 1):
            lines.extend(
                [
                    f"{index}. {html.escape(group.title or '未命名群')}",
                    f"ID：<code>{group.chat_id}</code>",
                    f"成员：{group.member_count} | 管理员：{group.admin_count}",
                    f"验证：{'开启' if group.verification_enabled else '关闭'} | 自动删：{group.auto_delete_seconds}s",
                    f"活跃：{_render_dt(group.last_active_at)} | 加入：{_render_dt(group.joined_at)} | 风险：{group.risk_level}",
                ]
            )
        lines.append(f"第 {page}/{total_pages} 页")
        return "\n".join(lines), total_pages

    def pager(prefix: str, page: int, total_pages: int) -> InlineKeyboardBuilder:
        builder = InlineKeyboardBuilder()
        if page > 1:
            builder.button(text="上一页", callback_data=f"{prefix}:{page - 1}")
        builder.button(text="刷新", callback_data=f"{prefix}:{page}")
        if page < total_pages:
            builder.button(text="下一页", callback_data=f"{prefix}:{page + 1}")
        builder.adjust(3)
        return builder

    async def send_status(message: Message, page: int = 1) -> None:
        text = await render_status_page(page)
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=pager("owner_status", page, STATUS_PAGE_COUNT).as_markup(),
        )

    async def send_groups(message: Message, page: int = 1) -> None:
        text, total_pages = await render_groups_page(page)
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=pager("owner_groups", page, total_pages).as_markup(),
        )

    async def send_panel(message: Message) -> None:
        text = await render_dashboard()
        builder = InlineKeyboardBuilder()
        builder.button(text="/status 状态页", callback_data="owner_status:1")
        builder.button(text="/groups 群列表", callback_data="owner_groups:1")
        builder.button(text="/update 更新机器人", callback_data="owner_update:confirm")
        builder.adjust(1)
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

    @router.message(Command("panel"))
    @router.message(Command("dashboard"))
    async def panel(message: Message) -> None:
        if not await ensure_owner(message):
            return
        await send_panel(message)

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        if not await ensure_owner(message):
            return
        page = _parse_page(message.text)
        await send_status(message, page)

    @router.message(Command("groups"))
    async def groups(message: Message) -> None:
        if not await ensure_owner(message):
            return
        page = _parse_page(message.text)
        await send_groups(message, page)

    @router.message(Command("group"))
    async def group_detail(message: Message, bot: Bot) -> None:
        if not await ensure_owner(message):
            return
        chat_id = _parse_chat_id(message.text or "")
        if chat_id is None:
            await message.answer("用法：/group 群ID")
            return
        try:
            chat = await bot.get_chat(chat_id)
            member_count = await bot.get_chat_member_count(chat_id)
            administrators = await bot.get_chat_administrators(chat_id)
            settings = repository.ensure_group_settings(chat_id)
            profile = repository.touch_group_profile(
                chat_id,
                title=chat.title or str(chat_id),
                member_count=member_count,
                admin_count=len(administrators),
                verification_enabled=settings.enabled,
                auto_delete_seconds=settings.auto_delete_seconds,
                last_active_at=datetime.now(timezone.utc).isoformat(),
            )
            text = "\n".join(
                [
                    "<b>群详情</b>",
                    f"名称：{html.escape(profile.title)}",
                    f"ID：<code>{profile.chat_id}</code>",
                    f"成员：{profile.member_count}",
                    f"管理员：{profile.admin_count}",
                    f"验证：{'开启' if profile.verification_enabled else '关闭'}",
                    f"自动删除：{profile.auto_delete_seconds}s",
                    f"最近活跃：{_render_dt(profile.last_active_at)}",
                    f"加入时间：{_render_dt(profile.joined_at)}",
                    f"风险等级：{profile.risk_level}",
                ]
            )
            await message.answer(text, parse_mode="HTML")
        except Exception as exc:
            await message.answer(f"无法读取该群：{html.escape(str(exc))}")

    @router.message(Command("update"))
    async def update(message: Message) -> None:
        if not await ensure_owner(message):
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) == 1:
            token = security_service.issue_confirmation(settings.owner_id, "update")
            await message.answer(
                f"危险操作确认：请在 5 分钟内再次发送 <code>/update {token}</code> 执行更新。",
                parse_mode="HTML",
            )
            audit_service.log("update_confirmation_requested", chat_id=None, user_id=settings.owner_id)
            return
        token = parts[1].strip().upper()
        if not security_service.verify_confirmation(settings.owner_id, "update", token):
            await message.answer("确认码无效或已过期，请重新发送 /update 获取新的确认码。")
            return

        progress = await message.answer("正在执行更新，请稍候。")
        audit_service.log("update_started", chat_id=None, user_id=settings.owner_id)
        result = await update_service.run_update(settings.db_path)
        audit_service.log(
            "update_completed" if result.success else "update_failed",
            chat_id=None,
            user_id=settings.owner_id,
            success=result.success,
            current_revision=result.current_revision,
            latest_revision=result.latest_revision,
            restarted_with=result.restarted_with,
            error=result.error,
        )
        summary = "\n".join(
            [
                "<b>更新结果</b>",
                f"结果：{'成功' if result.success else '失败'}",
                f"当前版本：{_short_hash(result.current_revision)}",
                f"最新版本：{_short_hash(result.latest_revision) if result.latest_revision else '未知'}",
                f"重启策略：{result.restarted_with}",
                f"步骤：",
                *[f"- {html.escape(step)}" for step in result.steps],
                f"输出：",
                f"<pre>{html.escape(_truncate(result.output or '无输出'))}</pre>",
                f"错误：{html.escape(result.error or '无')}",
            ]
        )
        try:
            await progress.edit_text(summary, parse_mode="HTML")
        except TelegramBadRequest:
            await message.answer(summary, parse_mode="HTML")

        if result.success and result.restarted_with != "manual":
            asyncio.create_task(
                _restart_later(audit_service, update_service, 2),
                name="owner-update-restart",
            )

    @router.callback_query(F.data.startswith("owner_status:"))
    async def callback_status(callback: CallbackQuery) -> None:
        if not callback.message or not callback.from_user or not security_service.is_owner(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        page = _parse_callback_page(callback.data or "", "owner_status")
        text = await render_status_page(page)
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=pager("owner_status", page, STATUS_PAGE_COUNT).as_markup(),
            )
        except TelegramBadRequest:
            pass
        await callback.answer()

    @router.callback_query(F.data.startswith("owner_groups:"))
    async def callback_groups(callback: CallbackQuery) -> None:
        if not callback.message or not callback.from_user or not security_service.is_owner(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        page = _parse_callback_page(callback.data or "", "owner_groups")
        text, total_pages = await render_groups_page(page)
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=pager("owner_groups", page, total_pages).as_markup(),
            )
        except TelegramBadRequest:
            pass
        await callback.answer()

    @router.callback_query(F.data == "owner_update:confirm")
    async def callback_update(callback: CallbackQuery) -> None:
        if not callback.message or not callback.from_user or not security_service.is_owner(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        token = security_service.issue_confirmation(settings.owner_id, "update")
        await callback.answer()
        await callback.message.answer(
            f"危险操作确认：请在 5 分钟内再次发送 <code>/update {token}</code> 执行更新。",
            parse_mode="HTML",
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        if not await ensure_owner(message):
            return
        text = "\n".join(
            [
                "/panel 仪表盘",
                "/dashboard 仪表盘",
                "/status 状态页",
                "/groups 群列表",
                "/group 群详情",
                "/update 更新机器人",
            ]
        )
        await message.answer(text)

    return router


async def _restart_later(
    audit_service: AuditService, update_service: UpdateService, delay_seconds: int
) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        method = await update_service.restart_runtime()
        audit_service.log("service_restarted", chat_id=None, user_id=None, method=method)
    except Exception as exc:
        audit_service.log("service_restart_failed", chat_id=None, user_id=None, error=str(exc))
        return


def _parse_page(text: str | None) -> int:
    if not text:
        return 1
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return 1
    try:
        return max(1, int(parts[1].strip()))
    except ValueError:
        return 1


def _parse_chat_id(text: str) -> int | None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[1].strip())
    except ValueError:
        return None


def _parse_callback_page(data: str, prefix: str) -> int:
    try:
        return max(1, int(data.removeprefix(f"{prefix}:")))
    except ValueError:
        return 1


def _short_hash(value: str | None) -> str:
    if not value:
        return "未知"
    return value[:7]


def _latest_label(current: str, latest: str | None) -> str:
    if not latest:
        return "未知"
    if current == latest:
        return f"{_short_hash(latest)}（已是最新）"
    return f"{_short_hash(latest)}（可更新）"


def _render_redis(snapshot) -> str:
    if not snapshot.configured:
        return "未配置"
    return "正常" if snapshot.reachable else f"异常：{snapshot.detail}"


def _render_load(runtime) -> str:
    if runtime.load_1m is None:
        return "不可用"
    return f"{runtime.load_1m:.2f} / {runtime.load_5m:.2f} / {runtime.load_15m:.2f}"


def _render_dt(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def _truncate(value: str, limit: int = 3000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n..."
