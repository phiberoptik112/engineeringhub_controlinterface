"""Slash-command handlers for /tasks and /queue (Journaler task planner)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from engineering_hub.journaler.engine import ConversationEngine
from engineering_hub.journaler.task_committer import TaskCommitter
from engineering_hub.journaler.task_planner_models import ProposedTask

_EDIT_RE = re.compile(
    r"^/tasks\s+edit\s+(\d+)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def handle_tasks_slash_command(raw: str, engine: ConversationEngine, pending_file: Path) -> str:
    """Handle `/tasks …` and `/queue …` lines. *raw* is the full user line."""
    line = raw.strip()
    low = line.lower()

    if low.startswith("/queue"):
        rest = line[6:].strip()
        if not rest:
            return "Usage: `/queue <description>` — proposes a task for the overnight queue."
        now = datetime.now(timezone.utc)
        task = ProposedTask(
            agent_type="research",
            description=rest,
            session_id=engine.session_id,
            session_timestamp=engine.session_opened_at,
            proposed_at=now,
            keywords=[],
            project_id=None,
            input_paths=[],
            output_path=None,
            context_flags=[],
            status="proposed",
            confidence=0.5,
            clarification_needed=True,
        )
        engine.task_planner.add_proposal(task)
        return (
            "Proposed for queue (default agent: research). Review with `/tasks`, "
            "then `/tasks confirm` and `/tasks commit`.\n"
            f"  1. [proposed] @research — {rest[:120]}{'…' if len(rest) > 120 else ''}"
        )

    if not low.startswith("/tasks"):
        return ""

    rest = line[6:].strip()
    planner = engine.task_planner
    committer = TaskCommitter(pending_file)

    if not rest:
        return _format_task_list(planner)

    parts = rest.split(maxsplit=2)
    sub = parts[0].lower()

    if sub == "confirm":
        if len(parts) >= 2 and parts[1].isdigit():
            n = int(parts[1])
            c = planner.confirm(n)
            if not c:
                return f"No active proposal #{n} to confirm."
            return f"Confirmed proposal #{n}. Use `/tasks commit` to write to pending-tasks.org."
        count = planner.confirm(None)
        if not count:
            return "Nothing to confirm (no proposals, or all rejected)."
        return f"Confirmed {count} proposal(s). Use `/tasks commit` to write to pending-tasks.org."

    if sub == "reject" and len(parts) >= 2 and parts[1].isdigit():
        if planner.reject(int(parts[1])):
            return f"Rejected proposal #{parts[1]}."
        return f"No proposal #{parts[1]}."

    if sub == "clear":
        dropped = planner.clear_unconfirmed()
        return f"Discarded {dropped} unconfirmed proposal(s). Confirmed tasks kept."

    m = _EDIT_RE.match(line)
    if m:
        n = int(m.group(1))
        new_desc = m.group(2).strip()
        if planner.edit_description(n, new_desc):
            return f"Updated proposal #{n}."
        return f"Cannot edit proposal #{n}."

    if sub == "commit":
        confirmed = planner.confirmed_tasks()
        if not confirmed:
            return "No confirmed tasks to commit. Use `/tasks confirm` first."
        ok, written = committer.commit_tasks(
            confirmed, session_timestamp=engine.session_opened_at
        )
        if not ok:
            return written
        committed_indices = {t.list_index for t in confirmed}
        planner.remove_by_list_indices(committed_indices)
        lines = ["Committed to pending-tasks.org:", f"Path: `{committer.pending_tasks_file}`", ""]
        for i, row in enumerate(written.split("\n"), start=1):
            lines.append(f"{i}. {row}")
        lines.append("\nThe Orchestrator will pick these up on its next scan. `/tasks rollback` to undo.")
        return "\n".join(lines)

    if sub == "rollback":
        nth: int | None = None
        if len(parts) >= 2:
            if parts[1] == "--all":
                removed, msg = committer.rollback(engine.session_id, mode="all")
            elif parts[1].isdigit():
                nth = int(parts[1])
                removed, msg = committer.rollback(
                    engine.session_id, mode="nth", n=nth
                )
            else:
                return "Usage: `/tasks rollback [N|--all]`"
        else:
            removed, msg = committer.rollback(engine.session_id, mode="last")
        if removed:
            return f"✓ {msg}"
        return msg

    return (
        "Unknown `/tasks` subcommand. Try `/tasks`, `/tasks confirm`, `/tasks commit`, "
        "`/tasks reject N`, `/tasks edit N <text>`, `/tasks clear`, `/tasks rollback`."
    )


def _format_task_list(planner) -> str:
    tasks = planner.list_all()
    if not tasks:
        return "Session task queue is empty. Use `/queue <description>` to propose work."
    lines = [f"Session task queue ({len(tasks)} task(s)):", ""]
    for t in tasks:
        proj = f"project {t.project_id}" if t.project_id is not None else "none"
        kw = ", ".join(t.keywords) if t.keywords else "—"
        lines.append(
            f"{t.list_index}. [{t.status}] @{t.agent_type.lstrip('@')} — {t.description[:100]}"
            f"{'…' if len(t.description) > 100 else ''}"
        )
        lines.append(f"   Project: {proj} | Keywords: {kw}")
    lines.append("")
    lines.append("`/tasks confirm` · `/tasks commit` · `/queue …`")
    return "\n".join(lines)
