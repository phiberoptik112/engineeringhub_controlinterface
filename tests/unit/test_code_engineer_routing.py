"""Phase 1+2 tests for the code-engineer agent (PiExecutor).

Covers:
- CodeProjectRegistry resolve/validation errors
- parse_pi_jsonl for real Pi event shapes + legacy fallbacks
- PiExecutor cmd/env/worktree/diff helpers (mocked pi subprocess)
- build_pi_executor None when no repos configured
- AgentDelegator.delegate("code-engineer", ...) routing + formatting
- Orchestrator._execute_task routing to _execute_code_task + memory capture
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from engineering_hub.code.pi_executor import (
    PiExecutor,
    build_pi_executor,
    parse_pi_jsonl,
)
from engineering_hub.code.project_registry import (
    CodeProjectError,
    CodeProjectRegistry,
)
from engineering_hub.config.settings import Settings
from engineering_hub.core.constants import AgentType
from engineering_hub.core.models import ParsedTask, TaskResult, TaskStatus
from engineering_hub.orchestration.orchestrator import Orchestrator


def _make_git_repo(tmp_path: Path, name: str = "myrepo") -> Path:
    """Create a real git repo with an initial commit (required for worktrees)."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def _code_task(project_id: str | None, description: str = "do a thing") -> ParsedTask:
    return ParsedTask(
        agent=AgentType.CODE_ENGINEER.value,
        status=TaskStatus.PENDING,
        project_id=project_id,
        description=description,
        start_line=0,
        end_line=0,
        raw_block=f"@code-engineer: {description}",
    )


def _settings_for(repo: Path, **kwargs) -> Settings:
    defaults = {
        "code_projects": {"myrepo": {"path": str(repo)}},
        "pi_bin": "pi",
        "pi_mode": "json",
        "pi_provider": "anthropic",
        "pi_model": "claude-sonnet-4-5",
        "pi_share_hub_api_key": True,
        "pi_offline": True,
        "anthropic_api_key": SecretStr("test-key"),
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def _fake_pi_stdout(*, message: str = "done", session_id: str = "sess-1") -> str:
    events = [
        {"type": "session", "version": 3, "id": session_id, "cwd": "/tmp"},
        {"type": "agent_start"},
        {"type": "turn_start"},
        {
            "type": "tool_execution_start",
            "toolCallId": "1",
            "toolName": "edit",
            "args": {},
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "1",
            "toolName": "edit",
            "result": "ok",
            "isError": False,
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": message}],
            },
        },
        {
            "type": "turn_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": message}],
            },
            "toolResults": [],
        },
        {
            "type": "agent_end",
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": message}],
                }
            ],
        },
    ]
    return "\n".join(json.dumps(e) for e in events)


# ---------------------------------------------------------------------------
# CodeProjectRegistry
# ---------------------------------------------------------------------------


class TestCodeProjectRegistry:
    def test_resolve_valid_repo(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        registry = CodeProjectRegistry(
            {"myrepo": {"path": str(repo), "default_branch": "trunk"}}
        )
        project = registry.resolve("myrepo")
        assert project.name == "myrepo"
        assert project.path == repo
        assert project.default_branch == "trunk"
        assert registry.names() == ["myrepo"]
        assert len(registry) == 1

    def test_resolve_unknown_raises_with_names(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        registry = CodeProjectRegistry({"myrepo": {"path": str(repo)}})
        with pytest.raises(CodeProjectError) as exc:
            registry.resolve("nope")
        assert "myrepo" in str(exc.value)

    def test_resolve_missing_path_raises(self, tmp_path: Path) -> None:
        registry = CodeProjectRegistry(
            {"ghost": {"path": str(tmp_path / "does-not-exist")}}
        )
        with pytest.raises(CodeProjectError):
            registry.resolve("ghost")

    def test_resolve_non_git_dir_raises(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        registry = CodeProjectRegistry({"plain": {"path": str(plain)}})
        with pytest.raises(CodeProjectError):
            registry.resolve("plain")

    def test_malformed_entry_without_path_is_skipped(self) -> None:
        registry = CodeProjectRegistry({"broken": {"default_branch": "main"}})
        assert len(registry) == 0
        assert registry.names() == []


# ---------------------------------------------------------------------------
# parse_pi_jsonl
# ---------------------------------------------------------------------------


class TestParsePiJsonl:
    def test_real_pi_event_shapes(self) -> None:
        parsed = parse_pi_jsonl(_fake_pi_stdout(message="patched the client"))
        assert parsed.session_id == "sess-1"
        assert parsed.final_message == "patched the client"
        assert any(t.name == "edit" for t in parsed.tools)
        assert parsed.error is None
        assert parsed.raw_events >= 5

    def test_legacy_tolerant_parse(self) -> None:
        stdout = "\n".join(
            [
                '{"type": "session_start", "sessionId": "abc123"}',
                "not json — skip me",
                '{"type": "tool_use", "name": "edit"}',
                '{"type": "assistant", "text": "done editing"}',
            ]
        )
        parsed = parse_pi_jsonl(stdout)
        assert parsed.session_id == "abc123"
        assert parsed.raw_events == 3
        assert [t.name for t in parsed.tools] == ["edit"]
        assert parsed.final_message == "done editing"

    def test_empty_stdout(self) -> None:
        parsed = parse_pi_jsonl("")
        assert parsed.raw_events == 0
        assert parsed.session_id is None
        assert parsed.final_message == ""

    def test_error_event(self) -> None:
        stdout = json.dumps({"type": "error", "error": "rate limited"})
        parsed = parse_pi_jsonl(stdout)
        assert parsed.error == "rate limited"


# ---------------------------------------------------------------------------
# PiExecutor helpers + mocked run
# ---------------------------------------------------------------------------


class TestPiExecutorHelpers:
    def test_build_pi_cmd_implement(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))
        project = executor._registry.resolve("myrepo")
        briefing = tmp_path / "ehub-briefing.md"
        briefing.write_text("hi", encoding="utf-8")

        cmd = executor._build_pi_cmd(project, "add retry", briefing, "implement")
        assert cmd[0] == "pi"
        assert "--mode" in cmd and "json" in cmd
        assert "--no-session" in cmd
        assert "--provider" in cmd and "anthropic" in cmd
        assert "--model" in cmd and "claude-sonnet-4-5" in cmd
        assert "--api-key" in cmd and "test-key" in cmd
        assert f"@{briefing.resolve()}" in cmd
        assert "add retry" in cmd
        assert "--tools" not in cmd  # full toolset when default_tools empty

    def test_build_pi_cmd_review_scopes_tools(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))
        project = executor._registry.resolve("myrepo")
        briefing = tmp_path / "ehub-briefing.md"
        briefing.write_text("hi", encoding="utf-8")

        cmd = executor._build_pi_cmd(project, "review auth", briefing, "review")
        assert "--tools" in cmd
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == "read,grep,find,ls"

    def test_build_env_offline_and_key(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))
        env = executor._build_env()
        assert env.get("PI_OFFLINE") == "1"
        assert env.get("PI_SKIP_VERSION_CHECK") == "1"
        assert env.get("ANTHROPIC_API_KEY") == "test-key"

    def test_make_worktree_creates_branch(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))
        project = executor._registry.resolve("myrepo")

        branch, worktree = executor._make_worktree(project, "add-retry")
        assert branch.startswith("pi/add-retry-")
        assert worktree.is_dir()
        assert (worktree / "README.md").is_file()
        path = executor._write_briefing(worktree.parent, "BRIEF")
        assert path.read_text(encoding="utf-8") == "BRIEF"
        assert path.parent == worktree.parent
        assert not (worktree / path.name).exists()

    def test_capture_diff_after_edit(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))
        project = executor._registry.resolve("myrepo")
        _, worktree = executor._make_worktree(project, "edit-file")
        (worktree / "new.py").write_text("x = 1\n", encoding="utf-8")
        diff = executor._capture_diff(worktree)
        assert "new.py" in diff
        assert "diff --git" in diff


class TestPiExecutorRun:
    def test_execute_task_unknown_project_fails(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))

        result = executor.execute_task(
            _code_task("unknown"), briefing="", output_dir=tmp_path
        )
        assert result.success is False
        assert "Unknown code project" in (result.error_message or "")

    def test_execute_task_success_with_mocked_pi(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))
        real_run = subprocess.run

        def selective_run(cmd, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] != "git" and (
                cmd[0] == "pi" or "--mode" in cmd
            ):
                cwd = Path(kwargs.get("cwd") or ".")
                (cwd / "feature.py").write_text(
                    "def f():\n    return 1\n", encoding="utf-8"
                )
                return MagicMock(
                    returncode=0,
                    stdout=_fake_pi_stdout(message="Added feature.py"),
                    stderr="",
                )
            return real_run(cmd, **kwargs)

        with patch(
            "engineering_hub.code.pi_executor.subprocess.run",
            side_effect=selective_run,
        ):
            result = executor.execute_task(
                _code_task("myrepo", "add feature"),
                briefing="BRIEFING",
                output_dir=tmp_path,
            )

        assert result.success is True
        assert result.output_path is not None
        diff_path = Path(result.output_path)
        assert diff_path.exists()
        assert "feature.py" in diff_path.read_text(encoding="utf-8")
        assert "Added feature.py" in (result.agent_response or "")
        assert "pi/" in (result.agent_response or "")

    def test_execute_task_pi_failure(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))
        real_run = subprocess.run

        def selective_run(cmd, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] != "git" and (
                cmd[0] == "pi" or "--mode" in cmd
            ):
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="provider auth failed",
                )
            return real_run(cmd, **kwargs)

        with patch(
            "engineering_hub.code.pi_executor.subprocess.run",
            side_effect=selective_run,
        ):
            result = executor.execute_task(
                _code_task("myrepo"), briefing="", output_dir=tmp_path
            )

        assert result.success is False
        assert "provider auth failed" in (result.error_message or "")

    def test_build_pi_executor_none_when_no_repos(self) -> None:
        assert build_pi_executor(Settings(code_projects={})) is None

    def test_build_pi_executor_builds_when_repos(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = _settings_for(repo)
        assert isinstance(build_pi_executor(settings), PiExecutor)


# ---------------------------------------------------------------------------
# AgentDelegator routing
# ---------------------------------------------------------------------------


class _StubPiExecutor:
    def __init__(self, result: TaskResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def execute_task(self, task, briefing, *, mode="implement", output_dir=None):
        self.calls.append({"task": task, "briefing": briefing, "mode": mode})
        return self.result


def _make_delegator(pi_executor):
    from engineering_hub.journaler.delegator import AgentDelegator

    return AgentDelegator(mlx_backend=object(), pi_executor=pi_executor)


class TestDelegatorRouting:
    def test_delegate_code_engineer_success(self, tmp_path: Path) -> None:
        task = _code_task("myrepo")
        stub_result = TaskResult(
            task=task,
            success=True,
            output_path=str(tmp_path / "code" / "d.diff"),
            agent_response="patched",
        )
        stub = _StubPiExecutor(stub_result)
        delegator = _make_delegator(stub)

        out = delegator.delegate(
            "code-engineer",
            "add retry logic",
            project_id="myrepo",
            journaler_context="BRIEF",
        )

        assert "completed" in out
        assert "d.diff" in out
        assert "patched" in out
        assert stub.calls and stub.calls[0]["briefing"] == "BRIEF"
        assert stub.calls[0]["task"].project_id == "myrepo"

    def test_delegate_alias_resolves(self, tmp_path: Path) -> None:
        task = _code_task("myrepo")
        stub = _StubPiExecutor(
            TaskResult(task=task, success=True, agent_response="ok")
        )
        delegator = _make_delegator(stub)
        out = delegator.delegate("coder", "x", project_id="myrepo")
        assert "completed" in out

    def test_delegate_failure_formats_error(self) -> None:
        task = _code_task("myrepo")
        stub = _StubPiExecutor(
            TaskResult(task=task, success=False, error_message="boom")
        )
        delegator = _make_delegator(stub)
        out = delegator.delegate("code-engineer", "x", project_id="myrepo")
        assert "failed" in out.lower()
        assert "boom" in out

    def test_delegate_without_executor_reports_unavailable(self) -> None:
        delegator = _make_delegator(None)
        out = delegator.delegate("code-engineer", "x", project_id="myrepo")
        assert "no code projects" in out.lower()


# ---------------------------------------------------------------------------
# Orchestrator routing
# ---------------------------------------------------------------------------


class _FakeContextManager:
    def format_for_agent(self, task) -> str:
        return "ORCH_BRIEFING"


class _FakeMemoryService:
    def __init__(self) -> None:
        self.captures: list[dict] = []

    def capture(self, **kwargs) -> None:
        self.captures.append(kwargs)


class TestOrchestratorRouting:
    def test_execute_task_routes_code_engineer_and_captures(self) -> None:
        orch = object.__new__(Orchestrator)

        task = _code_task("myrepo", "refactor module")
        result = TaskResult(
            task=task,
            success=True,
            output_path="/tmp/x.diff",
            agent_response="stub response",
        )
        stub = _StubPiExecutor(result)
        memory = _FakeMemoryService()

        orch._pi_executor = stub  # type: ignore[attr-defined]
        orch.context_manager = _FakeContextManager()  # type: ignore[attr-defined]
        orch.memory_service = memory  # type: ignore[attr-defined]

        out = orch._execute_task(task)

        assert out.success is True
        assert stub.calls and stub.calls[0]["briefing"] == "ORCH_BRIEFING"
        assert len(memory.captures) == 2

    def test_execute_code_task_no_executor_fails(self) -> None:
        orch = object.__new__(Orchestrator)
        orch._pi_executor = None  # type: ignore[attr-defined]

        out = orch._execute_code_task(_code_task("myrepo"))
        assert out.success is False
        assert "no code projects" in (out.error_message or "").lower()
