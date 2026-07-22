"""Data models for the Journaler daemon."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal


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


FileChangeStatus = Literal["unchanged", "mtime_only", "content_changed"]


@dataclass
class ScanState:
    """Persisted scan state (canonical paths, mtimes, content hashes)."""

    last_scan: str = ""
    file_mtimes: dict[str, float] = field(default_factory=dict)
    file_hashes: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def path_key(path: Path) -> str:
        return str(path.expanduser().resolve())

    @staticmethod
    def compute_hash(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def inspect(self, path: Path) -> tuple[FileChangeStatus, str | None]:
        """Classify a file without updating state.

        Fast path: matching mtime + stored hash → unchanged (no disk read).
        Slow path: mtime differs or hash missing → read bytes and compare hash.
        """
        key = self.path_key(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return "unchanged", None

        stored_mtime = self.file_mtimes.get(key)
        stored_hash = self.file_hashes.get(key)

        if stored_mtime == mtime and stored_hash is not None:
            return "unchanged", stored_hash

        current_hash = self.compute_hash(path)
        if current_hash is None:
            return "unchanged", None

        if stored_hash is None:
            if stored_mtime == mtime:
                return "unchanged", current_hash
            return "content_changed", current_hash

        if stored_hash != current_hash:
            return "content_changed", current_hash

        return "mtime_only", current_hash

    def record(self, path: Path, file_hash: str | None = None) -> None:
        key = self.path_key(path)
        try:
            self.file_mtimes[key] = path.stat().st_mtime
        except OSError:
            pass
        if file_hash is None:
            file_hash = self.compute_hash(path)
        if file_hash:
            self.file_hashes[key] = file_hash

    def normalize_legacy_keys(self) -> None:
        """Re-key persisted state through path_key() after upgrades."""
        if not self.file_mtimes and not self.file_hashes:
            return

        new_mtimes: dict[str, float] = {}
        for key, mtime in self.file_mtimes.items():
            try:
                new_mtimes[self.path_key(Path(key))] = mtime
            except OSError:
                new_mtimes[key] = mtime
        self.file_mtimes = new_mtimes

        new_hashes: dict[str, str] = {}
        for key, digest in self.file_hashes.items():
            try:
                new_hashes[self.path_key(Path(key))] = digest
            except OSError:
                new_hashes[key] = digest
        self.file_hashes = new_hashes


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


TaskStatus = Literal["pending", "completed"]
CompletionKind = Literal["checkbox", "todo_state", "prose", "queue_status"]


@dataclass
class TrackedTask:
    """A task with source provenance for briefing and stale detection."""

    text: str
    status: TaskStatus
    source_path: str
    source_date: str
    source_heading: str
    line_hint: int
    task_key: str
    completion_kind: CompletionKind
    first_seen: str
    last_seen: str

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "status": self.status,
            "source_path": self.source_path,
            "source_date": self.source_date,
            "source_heading": self.source_heading,
            "line_hint": self.line_hint,
            "task_key": self.task_key,
            "completion_kind": self.completion_kind,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TrackedTask:
        return cls(
            text=data.get("text", ""),
            status=data.get("status", "pending"),
            source_path=data.get("source_path", ""),
            source_date=data.get("source_date", ""),
            source_heading=data.get("source_heading", ""),
            line_hint=int(data.get("line_hint", 0)),
            task_key=data.get("task_key", ""),
            completion_kind=data.get("completion_kind", "checkbox"),
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
        )


@dataclass
class TaskStatusChange:
    """A pending task that became completed since the prior scan."""

    task_key: str
    text: str
    source_path: str
    source_date: str
    completion_kind: CompletionKind
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "task_key": self.task_key,
            "text": self.text,
            "source_path": self.source_path,
            "source_date": self.source_date,
            "completion_kind": self.completion_kind,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskStatusChange:
        return cls(
            task_key=data.get("task_key", ""),
            text=data.get("text", ""),
            source_path=data.get("source_path", ""),
            source_date=data.get("source_date", ""),
            completion_kind=data.get("completion_kind", "checkbox"),
            detail=data.get("detail", ""),
        )


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

    # Per-task first-seen dates for stale detection: {task_key: date_str}
    task_first_seen: dict[str, str] = field(default_factory=dict)

    # Full task registry with source provenance
    tracked_tasks: list[TrackedTask] = field(default_factory=list)

    # Pending → completed transitions detected on the latest scan
    task_status_changes: list[TaskStatusChange] = field(default_factory=list)

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
            "tracked_tasks": [t.to_dict() for t in self.tracked_tasks],
            "task_status_changes": [c.to_dict() for c in self.task_status_changes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ContextSnapshot:
        tracked_raw = data.get("tracked_tasks") or []
        changes_raw = data.get("task_status_changes") or []
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
            tracked_tasks=[TrackedTask.from_dict(t) for t in tracked_raw],
            task_status_changes=[TaskStatusChange.from_dict(c) for c in changes_raw],
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
