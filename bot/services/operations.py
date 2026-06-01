from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from bot.config import Settings
from bot.models import UpdateResult
from bot.services.system import SystemInspector


class CommandOutput:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class UpdateService:
    def __init__(self, repo_root: Path, settings: Settings, inspector: SystemInspector) -> None:
        self._repo_root = repo_root
        self._settings = settings
        self._inspector = inspector

    async def run_update(self, db_path: Path) -> UpdateResult:
        steps: list[str] = []
        output_chunks: list[str] = []
        current_snapshot = self._inspector.git_snapshot()
        if current_snapshot.is_dirty:
            return UpdateResult(
                success=False,
                current_revision=current_snapshot.current_revision,
                latest_revision=current_snapshot.latest_revision,
                steps=["update blocked: working tree has local changes"],
                output="",
                restarted_with="none",
                error="working tree has local changes",
            )

        try:
            steps.append("fetching latest code")
            fetch = await self._run_git("fetch", "origin", "--prune")
            output_chunks.append(self._format_output("git fetch", fetch))
            self._raise_for_failure(fetch, "git fetch failed")

            steps.append("pulling fast-forward changes")
            pull = await self._run_git("pull", "--ff-only")
            output_chunks.append(self._format_output("git pull", pull))
            self._raise_for_failure(pull, "git pull failed")

            steps.append("installing dependencies")
            pip = await self._run_python(
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(self._repo_root / "requirements.txt"),
            )
            output_chunks.append(self._format_output("pip install", pip))
            self._raise_for_failure(pip, "dependency installation failed")

            steps.append("running database migration")
            migrate = await self._run_python(
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; from bot.db import connect; "
                    f"connect(Path({str(db_path)!r}))"
                ),
            )
            output_chunks.append(self._format_output("database migration", migrate))
            self._raise_for_failure(migrate, "database migration failed")

            restart_method = self._detect_restart_method()
            steps.append(f"restart plan: {restart_method}")

            latest_snapshot = self._inspector.git_snapshot()
            return UpdateResult(
                success=True,
                current_revision=latest_snapshot.current_revision,
                latest_revision=latest_snapshot.latest_revision,
                steps=steps,
                output="\n".join(output_chunks).strip(),
                restarted_with=restart_method,
            )
        except Exception as exc:
            await self._restore_revision(current_snapshot.current_revision)
            return UpdateResult(
                success=False,
                current_revision=current_snapshot.current_revision,
                latest_revision=current_snapshot.latest_revision,
                steps=steps,
                output="\n".join(output_chunks).strip(),
                restarted_with="none",
                error=str(exc),
            )

    async def restart_runtime(self) -> str:
        restart_method = self._detect_restart_method()
        if restart_method.startswith("systemd:"):
            result = await self._run_command(
                "systemctl", "restart", self._settings.systemd_service_name
            )
            self._raise_for_failure(result, "systemd restart failed")
            return restart_method
        if restart_method == "docker-compose":
            compose_cmd = self._docker_compose_command()
            if compose_cmd:
                result = await self._run_command(
                    *compose_cmd, "up", "-d", "--build", "--force-recreate"
                )
                self._raise_for_failure(result, "docker compose restart failed")
            return restart_method
        if restart_method.startswith("pm2:"):
            pm2 = shutil.which("pm2")
            if pm2 and self._settings.pm2_process_name:
                result = await self._run_command(pm2, "restart", self._settings.pm2_process_name)
                self._raise_for_failure(result, "pm2 restart failed")
            return restart_method
        return restart_method

    async def _run_git(self, *args: str) -> CommandOutput:
        return await self._run_command(self._git_binary(), *args, cwd=self._repo_root)

    async def _run_python(self, *args: str) -> CommandOutput:
        return await self._run_command(*args, cwd=self._repo_root)

    async def _run_command(self, *args: str, cwd: Path | None = None) -> CommandOutput:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd or self._repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        return CommandOutput(
            returncode=proc.returncode,
            stdout=stdout_b.decode("utf-8", errors="replace").strip(),
            stderr=stderr_b.decode("utf-8", errors="replace").strip(),
        )

    def _raise_for_failure(self, result: CommandOutput, message: str) -> None:
        if result.returncode != 0:
            raise RuntimeError(f"{message}: {result.stderr or result.stdout or result.returncode}")

    def _format_output(self, label: str, result: CommandOutput) -> str:
        parts = [f"[{label}] rc={result.returncode}"]
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(result.stderr)
        return "\n".join(parts)

    def _git_binary(self) -> str:
        return shutil.which("git") or "git"

    async def _restore_revision(self, revision: str) -> None:
        try:
            await self._run_git("reset", "--hard", revision)
        except Exception:
            return

    def _detect_restart_method(self) -> str:
        if self._is_systemd() and self._settings.systemd_service_name:
            return f"systemd:{self._settings.systemd_service_name}"
        if self._has_docker_compose():
            return "docker-compose"
        if shutil.which("pm2") and self._settings.pm2_process_name:
            return f"pm2:{self._settings.pm2_process_name}"
        return "manual"

    def _is_systemd(self) -> bool:
        return Path("/run/systemd/system").exists() and shutil.which("systemctl") is not None

    def _has_docker_compose(self) -> bool:
        return (
            shutil.which("docker") is not None
            and any((self._repo_root / name).exists() for name in ("docker-compose.yml", "compose.yml", "compose.yaml"))
        )

    def _docker_compose_command(self) -> list[str] | None:
        docker = shutil.which("docker")
        if not docker:
            return None
        return [docker, "compose"]
