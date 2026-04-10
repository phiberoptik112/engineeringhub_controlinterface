"""Data models for the Journaler daemon."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class OrgEntry:
    """A single heading/entry extracted from an org file."""

    level: int
    title: str
    state: str | None = None  # "TODO", "DONE", None
    tags: list[str] = field(default_factory=list)
    timestamp: datetime | None = None
    body: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    children: list[OrgEntry] = field(default_factory=list)


@dataclass
class OrgFileInfo:
    """Metadata and entries extracted from a single org file."""

    path: Path
    title: str = ""
    filetags: list[str] = field(default_factory=list)
    entries: list[OrgEntry] = field(default_factory=list)

    @property
    def pending_tasks(self) -> list[str]:
        """All unchecked task lines from the file."""
        return _collect_tasks(self.entries, checked=False)

    @property
    def completed_tasks(self) -> list[str]:
        """All checked task lines from the file."""
        return _collect_tasks(self.entries, checked=True)


@dataclass
class ScanState:
    """Persisted scan state (file mtimes) for incremental scanning."""

    last_scan: str = ""
    file_mtimes: dict[str, float] = field(default_factory=dict)

    def is_changed(self, path: Path) -> bool:
        key = str(path)
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            return False
        return self.file_mtimes.get(key) != current_mtime

    def record(self, path: Path) -> None:
        try:
            self.file_mtimes[str(path)] = path.stat().st_mtime
        except OSError:
            pass


@dataclass
class ProjectChange:
    """A file that changed since the last scan."""

    file: str
    changed: str
    summary: str


@dataclass
class AgentOutput:
    """Summary of a recent agent output from memory."""

    agent: str
    project_id: int | None
    date: str
    summary: str


@dataclass
class ContextSnapshot:
    """Compressed context snapshot built from scanning."""

    last_scan: str = ""
    today_date: str = ""
    today_entries: list[dict[str, str]] = field(default_factory=list)
    pending_tasks: list[str] = field(default_factory=list)
    completed_tasks: list[str] = field(default_factory=list)
    recent_project_changes: list[dict[str, str]] = field(default_factory=list)
    recent_agent_outputs: list[dict[str, str]] = field(default_factory=list)
    active_projects: list[dict[str, str]] = field(default_factory=list)
    has_significant_changes: bool = False
    change_summary: str = ""

    # Multi-day journal window: {date_str: [{time, heading, content, state, tags}]}
    journal_window: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    # Topics appearing on 2+ distinct days: [{topic, days_seen, count, last_seen}]
    recurring_topics: list[dict[str, str | int]] = field(default_factory=list)

    # Recently modified org-roam nodes (non-journal): [{title, tags, path_rel, modified, top_headings}]
    active_roam_nodes: list[dict[str, str]] = field(default_factory=list)

    # Pending tasks with no journal mention in the lookback window
    stale_tasks: list[str] = field(default_factory=list)

    # Per-task first-seen dates for stale detection: {task_fragment: date_str}
    task_first_seen: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dictionary."""
        return {
            "last_scan": self.last_scan,
            "today_date": self.today_date,
            "today_entries": self.today_entries,
            "pending_tasks": self.pending_tasks,
            "completed_tasks": self.completed_tasks,
            "recent_project_changes": self.recent_project_changes,
            "recent_agent_outputs": self.recent_agent_outputs,
            "active_projects": self.active_projects,
            "journal_window": self.journal_window,
            "recurring_topics": self.recurring_topics,
            "active_roam_nodes": self.active_roam_nodes,
            "stale_tasks": self.stale_tasks,
            "task_first_seen": self.task_first_seen,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ContextSnapshot:
        return cls(
            last_scan=data.get("last_scan", ""),
            today_date=data.get("today_date", ""),
            today_entries=data.get("today_entries", []),
            pending_tasks=data.get("pending_tasks", []),
            completed_tasks=data.get("completed_tasks", []),
            recent_project_changes=data.get("recent_project_changes", []),
            recent_agent_outputs=data.get("recent_agent_outputs", []),
            active_projects=data.get("active_projects", []),
            journal_window=data.get("journal_window", {}),
            recurring_topics=data.get("recurring_topics", []),
            active_roam_nodes=data.get("active_roam_nodes", []),
            stale_tasks=data.get("stale_tasks", []),
            task_first_seen=data.get("task_first_seen", {}),
        )


def _collect_tasks(entries: list[OrgEntry], *, checked: bool) -> list[str]:
    """Recursively collect task lines from OrgEntry trees."""
    target_state = "DONE" if checked else "TODO"
    results: list[str] = []
    for entry in entries:
        if entry.state == target_state:
            results.append(entry.title)
        results.extend(_collect_tasks(entry.children, checked=checked))
    return results
