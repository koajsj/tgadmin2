from __future__ import annotations

import unittest
from pathlib import Path

from bot.config import Settings
from bot.models import GitSnapshot
from bot.services.operations import CommandOutput, UpdateService
from bot.services.system import SystemInspector


class FakeRepository:
    def build_git_snapshot(
        self,
        *,
        branch: str,
        current_revision: str,
        latest_revision: str | None,
        is_dirty: bool,
    ) -> GitSnapshot:
        return GitSnapshot(
            branch=branch,
            current_revision=current_revision,
            latest_revision=latest_revision,
            is_dirty=is_dirty,
        )


class SystemInspectorTests(unittest.TestCase):
    def test_git_snapshot_caches_remote_revision_for_short_ttl(self) -> None:
        inspector = SystemInspector(Path("."), FakeRepository(), "")
        calls: list[tuple[str, ...]] = []

        def fake_run_git(*args: str) -> str | None:
            calls.append(args)
            if args == ("rev-parse", "--abbrev-ref", "HEAD"):
                return "main"
            if args == ("rev-parse", "HEAD"):
                return "abc1234"
            if args == ("status", "--porcelain"):
                return ""
            if args == ("ls-remote", "origin", "refs/heads/main"):
                return "def5678\trefs/heads/main"
            return None

        inspector._run_git = fake_run_git  # type: ignore[method-assign]

        first = inspector.git_snapshot()
        second = inspector.git_snapshot()

        self.assertEqual(first.latest_revision, "def5678")
        self.assertEqual(second.latest_revision, "def5678")
        remote_calls = [call for call in calls if call[:2] == ("ls-remote", "origin")]
        self.assertEqual(len(remote_calls), 1)


class FakeInspector:
    def __init__(self) -> None:
        self.calls = 0

    def git_snapshot(self, *, fresh_remote: bool = False) -> GitSnapshot:
        self.calls += 1
        if self.calls == 1:
            return GitSnapshot(
                branch="main",
                current_revision="oldrev",
                latest_revision="newrev",
                is_dirty=False,
            )
        return GitSnapshot(
            branch="main",
            current_revision="newrev",
            latest_revision="newrev",
            is_dirty=False,
        )


class RecordingUpdateService(UpdateService):
    def __init__(self, repo_root: Path, settings: Settings, inspector: FakeInspector) -> None:
        super().__init__(repo_root, settings, inspector)  # type: ignore[arg-type]
        self.restore_calls = 0

    async def _run_git(self, *args: str) -> CommandOutput:
        if args[0] == "fetch":
            return CommandOutput(0, "", "")
        if args[0] == "pull":
            return CommandOutput(0, "", "")
        if args[0] == "diff":
            return CommandOutput(0, "requirements.txt", "")
        if args[0] == "reset":
            return CommandOutput(0, "", "")
        raise AssertionError(f"unexpected git command: {args}")

    async def _run_python(self, *args: str) -> CommandOutput:
        if "pip" in args:
            return CommandOutput(1, "", "pip failed")
        return CommandOutput(0, "", "")

    async def _restore_revision(self, revision: str) -> bool:
        self.restore_calls += 1
        return True


class UpdateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_update_skips_code_rollback_after_dependency_phase(self) -> None:
        inspector = FakeInspector()
        settings = Settings(
            bot_token="123:test",
            owner_id=1,
            db_path=Path("data/bot.sqlite3"),
        )
        service = RecordingUpdateService(Path("."), settings, inspector)

        result = await service.run_update(Path("data/bot.sqlite3"))

        self.assertFalse(result.success)
        self.assertEqual(service.restore_calls, 0)
        self.assertIn(
            "rollback skipped: dependency or database changes may already have been applied",
            result.steps,
        )
        self.assertIn("Manual recovery may be required", result.error or "")
