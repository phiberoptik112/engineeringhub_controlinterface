"""PiExecutor: run the external Pi coding agent against a local git checkout.

Phase 1 (this file) wires the full plumbing with a deterministic **stub**
``execute_task`` — it resolves the target repo through the registry (exercising
the real validation/error path) and writes a placeholder diff artifact, but does
not spawn ``pi``. Phase 2 will replace the stub body with a real
``pi --mode json`` subprocess run inside a git worktree and capture ``git diff``.

The ``PiRunResult`` / ``PiToolInvocation`` dataclasses and ``parse_pi_jsonl``
are provided now so the executor's result-shaping contract is stable across
phases; ``parse_pi_jsonl`` currently does a minimal, tolerant parse and will be
extended to full Pi event coverage in Phase 2.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_hub.code.project_registry import (
    CodeProject,
    CodeProjectError,
    CodeProjectRegistry,
)
from engineering_hub.core.models import ParsedTask, TaskResult

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)


class PiExecutorError(Exception):
    """Raised when the Pi run itself fails (subprocess / parsing errors)."""


@dataclass
class PiToolInvocation:
    """A single tool call Pi made during a run."""

    name: str
    ok: bool = True
    detail: str = ""


@dataclass
class PiRunResult:
    """Parsed outcome of one ``pi --mode json`` invocation."""

    session_id: str | None = None
    final_message: str = ""
    tools: list[PiToolInvocation] = field(default_factory=list)
    error: str | None = None
    raw_events: int = 0


# Keys Pi may use for a session identifier / assistant text across event shapes.
_SESSION_KEYS = ("sessionId", "session_id", "session")
_TEXT_KEYS = ("text", "content", "message", "delta")


def parse_pi_jsonl(stdout: str) -> PiRunResult:
    """Parse Pi's JSONL event stream into a :class:`PiRunResult`.

    Phase 1: minimal, tolerant parse — counts events, extracts a session id,
    collects tool-call names, and takes the last text-bearing event as the final
    message. Malformed lines are skipped. Phase 2 will map Pi's concrete event
    schema (assistant/tool_use/tool_result/result) precisely.
    """
    result = PiRunResult()
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        result.raw_events += 1

        if result.session_id is None:
            for key in _SESSION_KEYS:
                val = event.get(key)
                if isinstance(val, str) and val:
                    result.session_id = val
                    break

        event_type = str(event.get("type", "")).lower()
        if "tool" in event_type:
            name = event.get("name") or event.get("tool") or event_type
            result.tools.append(PiToolInvocation(name=str(name)))

        if "error" in event_type or event.get("error"):
            err = event.get("error") or event.get("message")
            if err:
                result.error = str(err)

        for key in _TEXT_KEYS:
            val = event.get(key)
            if isinstance(val, str) and val.strip():
                result.final_message = val.strip()
                break

    return result


def build_pi_executor(settings: "Settings") -> "PiExecutor | None":
    """Construct a :class:`PiExecutor` from settings, or ``None`` if no repos.

    Returns ``None`` when ``code_projects`` is empty so callers can treat the
    code-engineer capability as unavailable without special-casing.
    """
    registry = CodeProjectRegistry(settings.code_projects)
    if len(registry) == 0:
        return None
    return PiExecutor(settings, registry)


class PiExecutor:
    """Execute code-engineer tasks by delegating to the Pi coding agent."""

    def __init__(self, settings: "Settings", registry: CodeProjectRegistry) -> None:
        self._settings = settings
        self._registry = registry
        self._output_dir = Path(settings.output_dir)
        self._semaphore = threading.Semaphore(
            max(1, int(getattr(settings, "pi_max_concurrent", 1)))
        )

    def execute_task(
        self,
        task: ParsedTask,
        briefing: str,
        *,
        mode: str = "implement",
        output_dir: Path | None = None,
    ) -> TaskResult:
        """Run the code-engineer task for ``task`` against its registered repo.

        Phase 1 stub: resolve + validate the repo, then write a placeholder diff
        artifact and return a success ``TaskResult`` describing what Pi *would*
        do. On an unknown/invalid repo, returns a failed ``TaskResult``.
        """
        out_dir = Path(output_dir) if output_dir is not None else self._output_dir

        try:
            project = self._registry.resolve(task.project_id)
        except CodeProjectError as exc:
            logger.warning("code-engineer task rejected: %s", exc)
            return TaskResult(task=task, success=False, error_message=str(exc))

        code_dir = out_dir / "code"
        try:
            code_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Could not create output directory {code_dir}: {exc}",
            )

        diff_path = code_dir / f"project-{project.name}-{self._slug(task.description)}.diff"
        try:
            diff_path.write_text(
                self._placeholder_diff(project, task, mode, briefing),
                encoding="utf-8",
            )
        except OSError as exc:
            return TaskResult(
                task=task,
                success=False,
                error_message=f"Could not write diff artifact {diff_path}: {exc}",
            )

        summary = (
            f"[STUB] code-engineer would run Pi ({mode}) in {project.path} "
            f"for: {task.description}"
        )
        logger.info(
            "code-engineer STUB: project=%s mode=%s -> %s",
            project.name,
            mode,
            diff_path,
        )
        return TaskResult(
            task=task,
            success=True,
            output_path=str(diff_path),
            agent_response=summary,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _slug(text: str) -> str:
        slug = "".join(
            c if c.isalnum() or c == "-" else "-" for c in text[:40].lower()
        ).strip("-")
        return "-".join(filter(None, slug.split("-"))) or "task"

    @staticmethod
    def _placeholder_diff(
        project: CodeProject,
        task: ParsedTask,
        mode: str,
        briefing: str,
    ) -> str:
        return (
            "# code-engineer Phase 1 stub — no Pi run performed.\n"
            f"# project: {project.name}\n"
            f"# repo: {project.path}\n"
            f"# default_branch: {project.default_branch}\n"
            f"# mode: {mode}\n"
            f"# task: {task.description}\n"
            "#\n"
            "# Phase 2 will replace this with a real `git diff` from a Pi run\n"
            "# inside an isolated worktree.\n"
            "#\n"
            "# --- briefing snapshot (truncated) ---\n"
            + "\n".join(
                f"# {ln}" for ln in (briefing or "(none)").splitlines()[:40]
            )
            + "\n"
        )
