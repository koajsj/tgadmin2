from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from bot.models import DatabaseSnapshot, GitSnapshot, RedisSnapshot, RuntimeSnapshot
from bot.storage import Repository


class SystemInspector:
    def __init__(self, repo_root: Path, repository: Repository, redis_url: str) -> None:
        self._repo_root = repo_root
        self._repository = repository
        self._redis_url = redis_url

    def runtime_snapshot(self) -> RuntimeSnapshot:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(self._repo_root))
        net = psutil.net_io_counters()
        load_1m: float | None = None
        load_5m: float | None = None
        load_15m: float | None = None
        if hasattr(os, "getloadavg"):
            try:
                load_1m, load_5m, load_15m = os.getloadavg()
            except OSError:
                load_1m = load_5m = load_15m = None
        return self._repository.build_runtime_snapshot(
            hostname=socket.gethostname(),
            platform=platform.platform(),
            uptime_seconds=max(0, int(time.time() - psutil.boot_time())),
            cpu_percent=psutil.cpu_percent(interval=0.1),
            memory_total=int(memory.total),
            memory_used=int(memory.used),
            memory_percent=float(memory.percent),
            disk_total=int(disk.total),
            disk_used=int(disk.used),
            disk_percent=float(disk.percent),
            net_sent=int(net.bytes_sent),
            net_recv=int(net.bytes_recv),
            load_1m=load_1m,
            load_5m=load_5m,
            load_15m=load_15m,
        )

    def database_snapshot(self) -> DatabaseSnapshot:
        return self._repository.build_database_snapshot()

    def redis_snapshot(self) -> RedisSnapshot:
        if not self._redis_url:
            return RedisSnapshot(configured=False, reachable=False, detail="not configured")
        try:
            import redis
        except ImportError:
            return RedisSnapshot(configured=True, reachable=False, detail="redis package not installed")
        try:
            client = redis.Redis.from_url(self._redis_url, socket_connect_timeout=3, socket_timeout=3)
            try:
                reachable = bool(client.ping())
                detail = "ok" if reachable else "ping failed"
            finally:
                client.close()
            return RedisSnapshot(configured=True, reachable=reachable, detail=detail)
        except Exception as exc:  # pragma: no cover - defensive runtime probe
            return RedisSnapshot(configured=True, reachable=False, detail=str(exc))

    def git_snapshot(self) -> GitSnapshot:
        branch = self._run_git("rev-parse", "--abbrev-ref", "HEAD") or "unknown"
        current_revision = self._run_git("rev-parse", "HEAD") or "unknown"
        is_dirty = bool(self._run_git("status", "--porcelain"))
        latest_revision = self._remote_revision(branch)
        return self._repository.build_git_snapshot(
            branch=branch,
            current_revision=current_revision,
            latest_revision=latest_revision,
            is_dirty=is_dirty,
        )

    def _remote_revision(self, branch: str) -> str | None:
        remote = self._run_git("ls-remote", "origin", f"refs/heads/{branch}")
        if not remote:
            return None
        return remote.split()[0]

    def _run_git(self, *args: str) -> str | None:
        git = shutil.which("git")
        if not git:
            return None
        try:
            completed = subprocess.run(
                [git, *args],
                cwd=self._repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return None
        return completed.stdout.strip()

    @staticmethod
    def format_bytes(value: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    @staticmethod
    def format_duration(seconds: int) -> str:
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        parts: list[str] = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if secs or not parts:
            parts.append(f"{secs}s")
        return " ".join(parts)
