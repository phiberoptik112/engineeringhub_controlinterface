"""Append and roll back Journaler-owned pending-tasks.org entries."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from engineering_hub.journaler.task_planner_models import ProposedTask

PENDING_HEADING = "* Pending Agent Tasks"
COMPLETED_HEADING = "* Completed Agent Tasks"
SECTION_PENDING = "Pending Agent Tasks"

_SESSION_ID_RE = re.compile(r"^\s*:SESSION_ID:\s+(\S+)\s*$", re.MULTILINE)


def _org_inactive_ts(dt: datetime) -> str:
    """Format as [YYYY-MM-DD Day HH:MM] in local time."""
    local = dt.astimezone()
    return local.strftime("[%Y-%m-%d %a %H:%M]")


def _keywords_prop(keywords: list[str]) -> str:
    return " ".join(k.replace(" ", "-") for k in keywords if k.strip())


def _build_task_block(task: ProposedTask, session_ts: datetime) -> str:
    title = task.description.strip()
    if len(title) > 80:
        title = title[:77] + "..."
    agent = task.agent_type.strip().lstrip("@")

    lines: list[str] = [
        f"** @{agent}: {title}",
        ":PROPERTIES:",
        f":SESSION_ID: {task.session_id}",
        f":SESSION_TIMESTAMP: {_org_inactive_ts(session_ts)}",
        f":PROPOSED_AT: {_org_inactive_ts(task.proposed_at)}",
        f":KEYWORDS: {_keywords_prop(task.keywords)}",
        f":PROJECT_ID: {task.project_id if task.project_id is not None else ''}",
        ":STATUS: PENDING",
        ":END:",
    ]

    parts: list[str] = [f"- [ ] @{agent}: {task.description.strip()}"]
    if task.project_id is not None:
        parts[0] += f" [[django://project/{task.project_id}]]"
    for p in task.input_paths:
        link = p.strip()
        if link and not link.startswith("[["):
            link = f"[[{link}]]"
        elif link.startswith("[[") and link.endswith("]]"):
            pass
        else:
            link = f"[[{link}]]"
        parts[0] += f" {link}"
    if task.output_path:
        out = task.output_path.strip()
        if not out.startswith("[["):
            out = f"[[{out}]]"
        parts[0] += f" → {out}"
    lines.append(parts[0])
    lines.append("")
    return "\n".join(lines) + "\n"


def _default_file_header(created: datetime) -> str:
    return (
        "#+title: Journaler Pending Agent Tasks\n"
        f"#+created: {_org_inactive_ts(created)}\n"
        "#+author: Engineering Hub Journaler\n"
        "# This file is managed by the Journaler daemon.\n"
        "# Do not edit task entries manually; use /tasks commands in journaler chat.\n"
        "\n"
        f"{PENDING_HEADING}\n"
        "\n"
        f"{COMPLETED_HEADING}\n"
    )


def ensure_pending_tasks_file(path: Path) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_default_file_header(datetime.now()), encoding="utf-8")


class TaskCommitter:
    """Writes confirmed ProposedTask rows to pending-tasks.org."""

    def __init__(self, pending_tasks_file: Path) -> None:
        self.pending_tasks_file = pending_tasks_file.expanduser().resolve()

    def commit_tasks(
        self,
        tasks: list[ProposedTask],
        *,
        session_timestamp: datetime,
    ) -> tuple[bool, str]:
        if not tasks:
            return False, "No confirmed tasks to commit."
        ensure_pending_tasks_file(self.pending_tasks_file)
        text = self.pending_tasks_file.read_text(encoding="utf-8")
        insertion = "".join(
            _build_task_block(t, session_timestamp) for t in tasks
        )
        new_text = _insert_before_completed(text, insertion)
        self.pending_tasks_file.write_text(new_text, encoding="utf-8")
        written = "\n".join(
            _checkbox_line_only(t) for t in tasks
        )
        return True, written

    def rollback(
        self,
        session_id: str,
        mode: Literal["last", "nth", "all"] = "last",
        n: int | None = None,
    ) -> tuple[int, str]:
        """Remove task blocks authored by session_id. Returns (count_removed, message)."""
        path = self.pending_tasks_file
        if not path.exists():
            return 0, "No pending-tasks file yet."
        text = path.read_text(encoding="utf-8")
        blocks = _extract_pending_blocks(text)
        session_blocks = [(start, end, sid) for start, end, sid in blocks if sid == session_id]
        if not session_blocks:
            return 0, "No tasks from this session in the queue file."

        if mode == "all":
            to_remove = session_blocks
        elif mode == "last":
            to_remove = [session_blocks[-1]]
        else:
            if n is None or n < 1 or n > len(session_blocks):
                return 0, f"Invalid index (use 1–{len(session_blocks)} for this session)."
            to_remove = [session_blocks[n - 1]]

        removed = 0
        for start, end, _ in reversed(to_remove):
            text = text[:start] + text[end:]
            removed += 1
        path.write_text(text, encoding="utf-8")
        return removed, f"Removed {removed} task block(s) from {path}."


def _checkbox_line_only(task: ProposedTask) -> str:
    agent = task.agent_type.strip().lstrip("@")
    line = f"- [ ] @{agent}: {task.description.strip()}"
    if task.project_id is not None:
        line += f" [[django://project/{task.project_id}]]"
    for p in task.input_paths:
        link = p.strip()
        if link and not link.startswith("[["):
            link = f"[[{link}]]"
        line += f" {link}"
    if task.output_path:
        out = task.output_path.strip()
        if not out.startswith("[["):
            out = f"[[{out}]]"
        line += f" → {out}"
    return line


def _insert_before_completed(text: str, insertion: str) -> str:
    """Insert *insertion* before COMPLETED_HEADING, or append before EOF."""
    idx = text.find(COMPLETED_HEADING)
    if idx == -1:
        if PENDING_HEADING in text:
            return text.rstrip() + "\n\n" + insertion
        return text.rstrip() + "\n\n" + PENDING_HEADING + "\n\n" + insertion + "\n" + COMPLETED_HEADING + "\n"

    before = text[:idx].rstrip() + "\n\n"
    after = text[idx:]
    return before + insertion + after


def _extract_pending_blocks(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, session_id) for each level-2 task block under Pending section."""
    pending_start = text.find(PENDING_HEADING)
    if pending_start == -1:
        return []
    completed_start = text.find(COMPLETED_HEADING, pending_start)
    end_limit = completed_start if completed_start != -1 else len(text)
    region_start = pending_start
    slice_ = text[region_start:end_limit]
    heading_pat = re.compile(r"(?m)^\*\* .+$")
    matches = list(heading_pat.finditer(slice_))
    blocks: list[tuple[int, int, str]] = []
    for i, m in enumerate(matches):
        start = region_start + m.start()
        if i + 1 < len(matches):
            end = region_start + matches[i + 1].start()
        else:
            end = end_limit
        block = text[start:end]
        sm = _SESSION_ID_RE.search(block)
        if sm:
            blocks.append((start, end, sm.group(1).strip()))
    return blocks
