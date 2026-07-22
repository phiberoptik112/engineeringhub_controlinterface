"""PiExecutor: run the external Pi coding agent against a local git checkout.

Resolves a registered repo, creates an isolated git worktree + review branch,
writes a hub briefing beside the worktree for Pi to consume via ``@path``,
runs ``pi --mode json``, parses the JSONL event stream, captures ``git diff``,
and returns a :class:`TaskResult` whose ``output_path`` is the diff artifact.

The worktree/branch are left in place for human review — never merged to the
repo's default branch.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engineering_hub.code.project_registry import (
    CodeProject,
    CodeProjectError,
    CodeProjectRegistry,
)
from engineering_hub.core.models import ParsedTask, TaskResult

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)

_REVIEW_TOOLS = "read,grep,find,ls"
_BRIEFING_FILENAME = "ehub-briefing.md"


class PiExecutorError(Exception):
    """Raised when the Pi subprocess or git worktree setup fails."""


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


def _assistant_text(message: Any) -> str:
    """Extract plain text from a Pi assistant message object."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "".join(parts).strip()
    for key in ("text", "message"):
        val = message.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def parse_pi_jsonl(stdout: str) -> PiRunResult:
    """Parse Pi's JSONL event stream into a :class:`PiRunResult`.

    Handles the documented Pi event shapes:

    - ``session`` header (``id``)
    - ``message_end`` / ``turn_end`` assistant text
    - ``tool_execution_start`` / ``tool_execution_end``
    - ``turn_end.toolResults``
    - ``agent_end`` (fallback final message from last assistant)
    - error-bearing events

    Malformed lines are skipped. Unknown event types are counted but ignored.
    """
    result = PiRunResult()
    assistant_texts: list[str] = []

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
        event_type = str(event.get("type", "")).lower()

        if event_type == "session" and result.session_id is None:
            sid = event.get("id") or event.get("sessionId") or event.get("session_id")
            if isinstance(sid, str) and sid:
                result.session_id = sid

        if result.session_id is None:
            for key in ("sessionId", "session_id", "session"):
                val = event.get(key)
                if isinstance(val, str) and val:
                    result.session_id = val
                    break

        if event_type == "message_end":
            text = _assistant_text(event.get("message"))
            if text:
                assistant_texts.append(text)

        elif event_type == "turn_end":
            text = _assistant_text(event.get("message"))
            if text:
                assistant_texts.append(text)
            for tr in event.get("toolResults") or []:
                if not isinstance(tr, dict):
                    continue
                name = (
                    tr.get("toolName")
                    or tr.get("name")
                    or tr.get("tool")
                    or "tool"
                )
                is_error = bool(tr.get("isError") or tr.get("error"))
                detail = ""
                if isinstance(tr.get("result"), str):
                    detail = tr["result"][:200]
                result.tools.append(
                    PiToolInvocation(name=str(name), ok=not is_error, detail=detail)
                )

        elif event_type == "tool_execution_start":
            name = event.get("toolName") or event.get("name") or "tool"
            result.tools.append(PiToolInvocation(name=str(name), ok=True))

        elif event_type == "tool_execution_end":
            name = event.get("toolName") or event.get("name") or "tool"
            is_error = bool(event.get("isError"))
            updated = False
            for inv in reversed(result.tools):
                if inv.name == str(name) and inv.ok and not inv.detail:
                    inv.ok = not is_error
                    if isinstance(event.get("result"), str):
                        inv.detail = event["result"][:200]
                    updated = True
                    break
            if not updated:
                detail = ""
                if isinstance(event.get("result"), str):
                    detail = event["result"][:200]
                result.tools.append(
                    PiToolInvocation(name=str(name), ok=not is_error, detail=detail)
                )

        elif event_type == "agent_end":
            messages = event.get("messages") or []
            if isinstance(messages, list):
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        text = _assistant_text(msg)
                        if text:
                            assistant_texts.append(text)
                            break

        elif "error" in event_type or event.get("error"):
            err = event.get("error") or event.get("errorMessage") or event.get("message")
            if err:
                result.error = str(err)

        elif "tool" in event_type and event_type not in {
            "tool_execution_start",
            "tool_execution_end",
            "tool_execution_update",
        }:
            name = event.get("name") or event.get("tool") or event_type
            result.tools.append(PiToolInvocation(name=str(name)))
        else:
            for key in ("text", "content", "message", "delta"):
                val = event.get(key)
                if (
                    isinstance(val, str)
                    and val.strip()
                    and event_type in {"assistant", "message", "text"}
                ):
                    assistant_texts.append(val.strip())
                    break

    if assistant_texts:
        result.final_message = assistant_texts[-1]
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
        self._timeout = int(settings.pi_task_timeout)
        self._max_concurrent = max(1, int(settings.pi_max_concurrent))
        self._semaphore = threading.Semaphore(self._max_concurrent)

    def execute_task(
        self,
        task: ParsedTask,
        briefing: str,
        *,
        mode: str = "implement",
        output_dir: Path | None = None,
    ) -> TaskResult:
        """Resolve repo → worktree → Pi run → diff artifact → :class:`TaskResult`.

        On success: ``output_path`` is the diff file, ``agent_response`` is a
        human-readable summary. On failure: ``error_message`` is set. The
        worktree/branch are left in place for inspection; never merged.
        """
        out_dir = Path(output_dir) if output_dir is not None else self._output_dir
        mode = (mode or "implement").lower().strip()
        if mode not in {"implement", "review"}:
            mode = "implement"

        try:
            project = self._registry.resolve(task.project_id)
        except CodeProjectError as exc:
            logger.warning("code-engineer task rejected: %s", exc)
            return TaskResult(task=task, success=False, error_message=str(exc))

        acquired = self._semaphore.acquire(timeout=self._timeout)
        if not acquired:
            return TaskResult(
                task=task,
                success=False,
                error_message=(
                    f"Timed out waiting for a Pi slot "
                    f"(max_concurrent={self._max_concurrent})"
                ),
            )

        try:
            slug = self._slug(task.description)
            branch, worktree = self._make_worktree(project, slug)
            # Briefing lives beside the worktree so it does not pollute the diff.
            briefing_path = self._write_briefing(worktree.parent, briefing)
            cmd = self._build_pi_cmd(project, task.description, briefing_path, mode)
            env = self._build_env()

            logger.info(
                "code-engineer: project=%s mode=%s branch=%s cwd=%s",
                project.name,
                mode,
                branch,
                worktree,
            )
            logger.debug("Pi command: %s", " ".join(cmd))

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(worktree),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
            except FileNotFoundError:
                return TaskResult(
                    task=task,
                    success=False,
                    error_message=(
                        f"Pi CLI not found ({self._settings.pi_bin!r}). "
                        "Install with: npm install -g --ignore-scripts "
                        "@earendil-works/pi-coding-agent"
                    ),
                )
            except subprocess.TimeoutExpired:
                return TaskResult(
                    task=task,
                    success=False,
                    error_message=f"Pi timed out after {self._timeout} seconds",
                )

            run = parse_pi_jsonl(proc.stdout or "")
            if proc.returncode != 0 and not run.error:
                stderr = (proc.stderr or "").strip()
                run.error = stderr[:500] or f"pi exited with code {proc.returncode}"

            diff = self._capture_diff(worktree)
            artifact = self._write_artifact(
                task, project, diff, run, out_dir, branch=branch, mode=mode
            )
            summary = self._summarize(run, diff, branch, artifact, mode=mode)

            success = proc.returncode == 0 and run.error is None
            if success and mode == "implement" and not diff.strip():
                summary += (
                    "\n\nWarning: implement mode produced an empty diff "
                    "(Pi reported success but made no file changes)."
                )

            if not success:
                return TaskResult(
                    task=task,
                    success=False,
                    output_path=str(artifact) if artifact.exists() else None,
                    error_message=run.error or summary,
                    agent_response=summary,
                )

            return TaskResult(
                task=task,
                success=True,
                output_path=str(artifact),
                agent_response=summary,
            )
        except PiExecutorError as exc:
            logger.error("code-engineer setup failed: %s", exc)
            return TaskResult(task=task, success=False, error_message=str(exc))
        except Exception as exc:
            logger.exception("code-engineer unexpected failure")
            return TaskResult(
                task=task,
                success=False,
                error_message=f"code-engineer failed: {exc}",
            )
        finally:
            self._semaphore.release()

    def status(self) -> dict[str, Any]:
        """Return Pi binary availability and registered repo names."""
        bin_parts = shlex.split(self._settings.pi_bin)
        bin_path = bin_parts[0] if bin_parts else "pi"
        return {
            "pi_bin": self._settings.pi_bin,
            "pi_available": shutil.which(bin_path) is not None
            or Path(bin_path).exists(),
            "max_concurrent": self._max_concurrent,
            "timeout": self._timeout,
            "projects": self._registry.names(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _slug(text: str) -> str:
        slug = "".join(
            c if c.isalnum() or c == "-" else "-" for c in text[:40].lower()
        ).strip("-")
        return "-".join(filter(None, slug.split("-"))) or "task"

    def _make_worktree(self, project: CodeProject, slug: str) -> tuple[str, Path]:
        """Create an isolated worktree on a fresh ``pi/<slug>-<ts>`` branch."""
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        branch = f"pi/{slug}-{ts}"
        base = project.path.parent / ".ehub-pi" / project.name
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PiExecutorError(
                f"Could not create worktree base {base}: {exc}"
            ) from exc

        worktree = base / f"{slug}-{ts}"
        if worktree.exists():
            raise PiExecutorError(f"Worktree path already exists: {worktree}")

        result = subprocess.run(
            [
                "git",
                "-C",
                str(project.path),
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise PiExecutorError(
                f"git worktree add failed for '{project.name}' "
                f"(branch={branch}): {err}"
            )
        return branch, worktree

    @staticmethod
    def _write_briefing(dest_dir: Path, briefing: str) -> Path:
        path = dest_dir / _BRIEFING_FILENAME
        try:
            path.write_text(briefing or "(no briefing provided)\n", encoding="utf-8")
        except OSError as exc:
            raise PiExecutorError(
                f"Could not write briefing to {path}: {exc}"
            ) from exc
        return path

    def _policy_prompt(self) -> str:
        """Load the code-engineer policy prompt if present."""
        prompts_dir = self._settings.prompts_dir
        path = prompts_dir / "code-engineer.txt"
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                logger.warning("Could not read policy prompt at %s", path)
        return (
            "Work only in this checkout. Never merge to the default branch. "
            "Leave changes on the current review branch for human review."
        )

    def _resolve_tools(self, project: CodeProject, mode: str) -> str:
        if mode == "review":
            return _REVIEW_TOOLS
        if project.tools is not None and str(project.tools).strip() != "":
            return str(project.tools)
        return (self._settings.pi_default_tools or "").strip()

    def _build_pi_cmd(
        self,
        project: CodeProject,
        description: str,
        briefing_path: Path,
        mode: str,
    ) -> list[str]:
        cmd = shlex.split(self._settings.pi_bin)
        if not cmd:
            cmd = ["pi"]

        pi_mode = (self._settings.pi_mode or "json").strip() or "json"
        cmd.extend(["--mode", pi_mode, "--no-session"])

        provider = project.provider or self._settings.pi_provider
        model = project.model or self._settings.pi_model
        if provider:
            cmd.extend(["--provider", provider])
        if model:
            cmd.extend(["--model", model])

        if self._settings.pi_share_hub_api_key:
            key = self._settings.anthropic_api_key.get_secret_value()
            if key:
                cmd.extend(["--api-key", key])

        tools = self._resolve_tools(project, mode)
        if tools:
            cmd.extend(["--tools", tools])

        policy = self._policy_prompt()
        if policy:
            cmd.extend(["--append-system-prompt", policy])

        # Absolute @path so Pi can load a briefing written beside the worktree.
        cmd.append(f"@{briefing_path.resolve()}")
        cmd.append(description)
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._settings.pi_offline:
            env["PI_OFFLINE"] = "1"
            env["PI_SKIP_VERSION_CHECK"] = "1"
        if self._settings.pi_share_hub_api_key:
            key = self._settings.anthropic_api_key.get_secret_value()
            if key:
                env.setdefault("ANTHROPIC_API_KEY", key)
        return env

    @staticmethod
    def _capture_diff(worktree: Path) -> str:
        """Stage all changes and return a unified staged diff (may be empty)."""
        add = subprocess.run(
            ["git", "-C", str(worktree), "add", "-A"],
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            logger.warning(
                "git add -A failed in %s: %s",
                worktree,
                (add.stderr or "").strip(),
            )

        diff = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--staged"],
            capture_output=True,
            text=True,
        )
        if diff.returncode != 0:
            logger.warning(
                "git diff --staged failed in %s: %s",
                worktree,
                (diff.stderr or "").strip(),
            )
            return ""
        return diff.stdout or ""

    def _write_artifact(
        self,
        task: ParsedTask,
        project: CodeProject,
        diff: str,
        run: PiRunResult,
        output_dir: Path,
        *,
        branch: str,
        mode: str,
    ) -> Path:
        code_dir = output_dir / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slug(task.description)
        path = code_dir / f"project-{project.name}-{slug}.diff"

        header = (
            f"# code-engineer artifact\n"
            f"# project: {project.name}\n"
            f"# repo: {project.path}\n"
            f"# branch: {branch}\n"
            f"# mode: {mode}\n"
            f"# session: {run.session_id or 'n/a'}\n"
            f"# task: {task.description}\n"
            f"#\n"
        )
        if run.final_message:
            header += "# --- pi final message ---\n"
            for ln in run.final_message.splitlines()[:80]:
                header += f"# {ln}\n"
            header += "#\n"

        body = diff if diff.strip() else "# (empty diff)\n"
        path.write_text(header + body, encoding="utf-8")
        return path

    @staticmethod
    def _summarize(
        run: PiRunResult,
        diff: str,
        branch: str,
        artifact: Path,
        *,
        mode: str,
    ) -> str:
        tool_names = [t.name for t in run.tools]
        unique_tools = list(dict.fromkeys(tool_names))
        changed_files = 0
        for line in diff.splitlines():
            if line.startswith("diff --git "):
                changed_files += 1

        lines = [
            f"code-engineer ({mode}) finished on branch `{branch}`.",
            f"Diff artifact: `{artifact}`",
            f"Files touched: {changed_files}",
        ]
        if unique_tools:
            lines.append(f"Tools used: {', '.join(unique_tools)}")
        if run.session_id:
            lines.append(f"Pi session: {run.session_id}")
        if run.final_message:
            lines.append("")
            lines.append(run.final_message)
        elif run.error:
            lines.append("")
            lines.append(f"Error: {run.error}")
        return "\n".join(lines)
