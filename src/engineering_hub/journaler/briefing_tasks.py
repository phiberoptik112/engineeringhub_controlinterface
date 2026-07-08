"""Briefing task extraction and background work queue.

Extracts actionable tasks from morning briefings, discussion briefings, and
journal context using the resident MLX model.  Persists a JSON-backed work
queue that the daemon's background work loop processes throughout the day.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.journaler.delegator import AgentDelegator
    from engineering_hub.journaler.engine import ConversationEngine

logger = logging.getLogger(__name__)

TASK_EXTRACTION_PROMPT = """\
You are the Journaler task extractor.  Given the briefing content, journal \
context, recent chat history, and available agent skills below, extract \
concrete tasks that an AI agent could work on in the background.

Only extract tasks that:
- Are explicitly actionable (research, draft, audit, scan, review, summarize)
- Can be completed by one of the available agent skills
- Are NOT physical tasks (printing, ordering, meeting attendance)
- Have NOT already been completed according to the context

For each task, output a JSON object on its own line (JSONL format):
{{"description": "...", "suggested_agent": "...", "priority": "high|medium|low", \
"project_ref": "...", "source_persona": "..."}}

Priority rules:
- HIGH: user discussed this topic in recent chat, or it is flagged as overdue/stale
- MEDIUM: mentioned in briefing agenda or suggested paths, not yet discussed in chat
- LOW: background improvement, nice-to-have, or already partially addressed

Output ONLY the JSON lines, no commentary.  If no tasks are extractable, \
output nothing.

## Briefing Content
{briefing_markdown}

## Recent Chat Activity
{chat_excerpt}

## Available Agent Skills
{skills_list}
"""


@dataclass
class BriefingTask:
    """A single extractable task from a briefing or journal."""

    id: str
    description: str
    source: str  # "morning_briefing" | "discussion_briefing" | "journal" | "topic_scout"
    source_persona: str
    suggested_agent: str
    priority: str  # "high" | "medium" | "low"
    project_ref: str
    status: str  # "pending" | "in_progress" | "completed" | "skipped"
    created_at: str
    completed_at: str = ""
    output_path: str = ""
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> BriefingTask:
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in allowed})


# ---------------------------------------------------------------------------
# Chat history reader
# ---------------------------------------------------------------------------


def _read_recent_chat_context(
    state_dir: Path,
    *,
    lookback_days: int = 3,
    max_chars: int = 3000,
) -> str:
    """Read recent user turns from conversation.jsonl for priority signals.

    Returns a condensed excerpt of user messages, focusing on topics discussed,
    questions asked, and /agent commands already run.
    """
    log_file = state_dir / "conversation.jsonl"
    if not log_file.exists():
        return ""

    cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()
    lines: list[str] = []
    total = 0

    for raw_line in reversed(log_file.read_text(encoding="utf-8").splitlines()):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        ts = entry.get("timestamp", "")
        if ts < cutoff:
            break
        role = entry.get("role", "")
        content = entry.get("content", "")
        if role == "user" and content.strip():
            snippet = content[:400]
            candidate = f"[{ts[:16]}] {snippet}"
            if total + len(candidate) > max_chars:
                break
            lines.append(candidate)
            total += len(candidate)

    if not lines:
        return "(no recent chat activity)"
    lines.reverse()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task extractor
# ---------------------------------------------------------------------------


class BriefingTaskExtractor:
    """Extract structured tasks from briefing markdown using the MLX engine."""

    def __init__(
        self,
        engine: ConversationEngine,
        state_dir: Path,
        delegator: AgentDelegator | None = None,
        chat_lookback_days: int = 3,
        max_extraction_tokens: int = 1024,
    ) -> None:
        self._engine = engine
        self._state_dir = state_dir
        self._delegator = delegator
        self._chat_lookback_days = chat_lookback_days
        self._max_extraction_tokens = max_extraction_tokens

    def _skills_list(self) -> str:
        if self._delegator is None:
            return "(no agent skills available)"
        skills = self._delegator.list_skills()
        if not skills:
            return "(no agent skills loaded)"
        return "\n".join(
            f"- {s.name}: {s.description.splitlines()[0]}" for s in skills
        )

    def extract(
        self,
        briefing_markdown: str,
        source: str,
        completed_task_keys: set[str] | None = None,
    ) -> list[BriefingTask]:
        """Run the extraction prompt and parse results into BriefingTask objects."""
        from engineering_hub.journaler.task_resolution import is_task_already_completed

        chat_excerpt = _read_recent_chat_context(
            self._state_dir, lookback_days=self._chat_lookback_days
        )
        prompt = TASK_EXTRACTION_PROMPT.format(
            briefing_markdown=briefing_markdown[:8000],
            chat_excerpt=chat_excerpt,
            skills_list=self._skills_list(),
        )

        try:
            raw = self._engine._raw_complete(
                prompt, max_tokens=self._max_extraction_tokens
            )
        except Exception as exc:
            logger.warning("Task extraction failed: %s", exc)
            return []

        tasks = self._parse_extraction(raw, source)
        if not completed_task_keys:
            return tasks
        filtered = [
            t
            for t in tasks
            if not is_task_already_completed(t.description, completed_task_keys)
        ]
        if len(filtered) < len(tasks):
            logger.info(
                "Filtered %d already-completed tasks from %s extraction",
                len(tasks) - len(filtered),
                source,
            )
        return filtered

    def _parse_extraction(self, raw: str, source: str) -> list[BriefingTask]:
        tasks: list[BriefingTask] = []
        now = datetime.now().isoformat(timespec="seconds")
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not obj.get("description"):
                continue
            priority = obj.get("priority", "medium").lower()
            if priority not in ("high", "medium", "low"):
                priority = "medium"
            tasks.append(
                BriefingTask(
                    id=uuid.uuid4().hex[:12],
                    description=obj["description"],
                    source=source,
                    source_persona=obj.get("source_persona", ""),
                    suggested_agent=obj.get("suggested_agent", "research"),
                    priority=priority,
                    project_ref=obj.get("project_ref", ""),
                    status="pending",
                    created_at=now,
                )
            )
        return tasks


# ---------------------------------------------------------------------------
# Background work queue
# ---------------------------------------------------------------------------


_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class BackgroundWorkQueue:
    """File-backed daily task queue stored as JSON."""

    def __init__(self, queue_dir: Path) -> None:
        self._queue_dir = queue_dir
        self._queue_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, day: date | None = None) -> Path:
        day_str = (day or date.today()).isoformat()
        return self._queue_dir / f"{day_str}.json"

    def _load(self, day: date | None = None) -> list[BriefingTask]:
        path = self._path(day)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [BriefingTask.from_dict(t) for t in data]
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Failed to load queue %s: %s", path, exc)
            return []

    def _save(self, tasks: list[BriefingTask], day: date | None = None) -> None:
        path = self._path(day)
        path.write_text(
            json.dumps([t.to_dict() for t in tasks], indent=2),
            encoding="utf-8",
        )

    def add_tasks(self, tasks: list[BriefingTask]) -> int:
        """Add tasks to today's queue, skipping duplicates by description."""
        existing = self._load()
        existing_descs = {t.description.lower().strip() for t in existing}
        added = 0
        for task in tasks:
            if task.description.lower().strip() not in existing_descs:
                existing.append(task)
                existing_descs.add(task.description.lower().strip())
                added += 1
        if added:
            self._save(existing)
        return added

    def next_pending(self, *, auto_approve_only: bool = False) -> BriefingTask | None:
        """Return the highest-priority pending task, or None."""
        tasks = self._load()
        candidates = [t for t in tasks if t.status == "pending"]
        if not candidates:
            return None
        candidates.sort(key=lambda t: _PRIORITY_ORDER.get(t.priority, 1))
        return candidates[0]

    def mark_in_progress(self, task_id: str) -> None:
        tasks = self._load()
        for t in tasks:
            if t.id == task_id:
                t.status = "in_progress"
                break
        self._save(tasks)

    def mark_completed(self, task_id: str, output_path: str = "") -> None:
        tasks = self._load()
        for t in tasks:
            if t.id == task_id:
                t.status = "completed"
                t.completed_at = datetime.now().isoformat(timespec="seconds")
                t.output_path = output_path
                break
        self._save(tasks)

    def mark_skipped(self, task_id: str, reason: str = "") -> None:
        tasks = self._load()
        for t in tasks:
            if t.id == task_id:
                t.status = "skipped"
                t.skip_reason = reason
                break
        self._save(tasks)

    def today_tasks(self) -> list[BriefingTask]:
        return self._load()

    def today_status(self) -> dict[str, int]:
        """Return counts by status for today."""
        tasks = self._load()
        counts: dict[str, int] = {
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "skipped": 0,
        }
        for t in tasks:
            counts[t.status] = counts.get(t.status, 0) + 1
        return counts

    def completed_today_count(self) -> int:
        return sum(1 for t in self._load() if t.status == "completed")


# ---------------------------------------------------------------------------
# Work status file writer
# ---------------------------------------------------------------------------


def write_work_status(state_dir: Path, queue: BackgroundWorkQueue) -> Path:
    """Write or update the daily agent work status markdown file.

    Returns the path to the status file.
    """
    tasks = queue.today_tasks()
    today_str = date.today().isoformat()
    status_dir = state_dir / "agent_work_status"
    status_dir.mkdir(parents=True, exist_ok=True)
    path = status_dir / f"{today_str}.md"

    by_source: dict[str, list[BriefingTask]] = {}
    for t in tasks:
        by_source.setdefault(t.source, []).append(t)

    lines = [f"# Agent Work Status -- {today_str}\n"]

    source_labels = {
        "morning_briefing": "Morning Briefing",
        "discussion_briefing": "Discussion Briefing",
        "journal": "Journal Tasks",
        "topic_scout": "Topic Scout",
    }

    for source_key, label in source_labels.items():
        group = by_source.get(source_key, [])
        if not group:
            continue
        lines.append(f"\n## Source: {label}\n")
        for t in group:
            check = "x" if t.status == "completed" else " "
            agent_note = f"{t.suggested_agent} agent"
            if t.status == "completed" and t.completed_at:
                time_str = t.completed_at[11:16]
                agent_note += f", {time_str}"
            elif t.status == "skipped":
                agent_note += f", skipped"
                if t.skip_reason:
                    agent_note += f": {t.skip_reason}"
            elif t.status == "in_progress":
                agent_note += ", in progress"
            else:
                agent_note += ", pending"
            lines.append(f"- [{check}] **{t.description}** ({agent_note})")
            if t.output_path:
                lines.append(f"  Output: {t.output_path}")

    counts = queue.today_status()
    total = sum(counts.values())
    lines.append(f"\n## Summary\n")
    lines.append(
        f"Completed: {counts['completed']}/{total} | "
        f"Pending: {counts['pending']} | "
        f"Skipped: {counts['skipped']}"
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
