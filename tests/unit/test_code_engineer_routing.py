"""Phase 1 routing tests for the code-engineer agent (stub PiExecutor).

Covers:
- CodeProjectRegistry resolve/validation errors
- PiExecutor stub execute_task (success artifact + unknown-project failure)
- build_pi_executor None when no repos configured
- AgentDelegator.delegate("code-engineer", ...) routing + formatting
- Orchestrator._execute_task routing to _execute_code_task + memory capture
- parse_pi_jsonl minimal tolerant parse
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
    repo = tmp_path / name
    repo.mkdir()
    (repo / ".git").mkdir()
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
# PiExecutor stub + build_pi_executor
# ---------------------------------------------------------------------------


class TestPiExecutorStub:
    def test_execute_task_success_writes_diff(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = Settings(code_projects={"myrepo": {"path": str(repo)}})
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))

        task = _code_task("myrepo", "add a retry helper")
        result = executor.execute_task(task, briefing="BRIEFING", output_dir=tmp_path)

        assert result.success is True
        assert result.output_path is not None
        diff_path = Path(result.output_path)
        assert diff_path.exists()
        assert diff_path.parent == tmp_path / "code"
        assert "[STUB]" in (result.agent_response or "")
        assert str(repo) in (result.agent_response or "")

    def test_execute_task_unknown_project_fails(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = Settings(code_projects={"myrepo": {"path": str(repo)}})
        executor = PiExecutor(settings, CodeProjectRegistry(settings.code_projects))

        result = executor.execute_task(
            _code_task("unknown"), briefing="", output_dir=tmp_path
        )
        assert result.success is False
        assert "Unknown code project" in (result.error_message or "")

    def test_build_pi_executor_none_when_no_repos(self) -> None:
        assert build_pi_executor(Settings(code_projects={})) is None

    def test_build_pi_executor_builds_when_repos(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path)
        settings = Settings(code_projects={"myrepo": {"path": str(repo)}})
        assert isinstance(build_pi_executor(settings), PiExecutor)


# ---------------------------------------------------------------------------
# AgentDelegator routing
# ---------------------------------------------------------------------------


class _StubPiExecutor:
    def __init__(self, result: TaskResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def execute_task(self, task, briefing, *, mode="implement", output_dir=None):
        self.calls.append(
            {"task": task, "briefing": briefing, "mode": mode}
        )
        return self.result


def _make_delegator(pi_executor):
    from engineering_hub.journaler.delegator import AgentDelegator

    # mlx_backend is only touched at worker-execute time, never for code-engineer.
    return AgentDelegator(mlx_backend=object(), pi_executor=pi_executor)


class TestDelegatorRouting:
    def test_delegate_code_engineer_success(self, tmp_path: Path) -> None:
        task = _code_task("myrepo")
        stub_result = TaskResult(
            task=task,
            success=True,
            output_path=str(tmp_path / "code" / "d.diff"),
            agent_response="[STUB] ran",
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
        assert "[STUB] ran" in out
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
# Orchestrator routing (surgical, bypassing heavy __init__)
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
        orch = object.__new__(Orchestrator)  # bypass __init__/_init_components

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
        # _capture_task_result writes two memory entries on success
        assert len(memory.captures) == 2

    def test_execute_code_task_no_executor_fails(self) -> None:
        orch = object.__new__(Orchestrator)
        orch._pi_executor = None  # type: ignore[attr-defined]

        out = orch._execute_code_task(_code_task("myrepo"))
        assert out.success is False
        assert "no code projects" in (out.error_message or "").lower()


# ---------------------------------------------------------------------------
# parse_pi_jsonl minimal
# ---------------------------------------------------------------------------


class TestParsePiJsonl:
    def test_tolerant_parse(self) -> None:
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
