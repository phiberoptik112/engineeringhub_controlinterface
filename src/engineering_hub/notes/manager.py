"""High-level manager for shared notes operations."""

from datetime import datetime
from pathlib import Path

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import AgentMessage, ParsedTask
from engineering_hub.notes.journal_parser import JournalParser
from engineering_hub.notes.journal_writer import JournalWriter
from engineering_hub.notes.org_task_parser import OrgTaskParser
from engineering_hub.notes.org_task_writer import OrgTaskWriter
from engineering_hub.notes.parser import NotesParser
from engineering_hub.notes.writer import NotesWriter


class SharedNotesManager:
    """Facade for shared notes parsing and writing operations.

    Supports three modes selected by constructor flags (mutually exclusive,
    evaluated in priority order):

    1. **org mode** (``use_org_mode=True``) — reads tasks from ``* Overnight
       Agent Tasks`` sections of org-roam daily ``.org`` files.
    2. **journal mode** (``use_journal_mode=True``) — reads tasks from a
       ``journal.md`` file with dated sections and category headers.
    3. **legacy mode** (default) — reads tasks from a ``shared-notes.md`` file
       with ``### @agent: STATUS`` blocks.
    """

    def __init__(
        self,
        notes_path: Path,
        use_journal_mode: bool = False,
        journal_categories: dict[str, str] | None = None,
        use_org_mode: bool = False,
        org_task_sections: list[str] | None = None,
        org_lookback_days: int = 1,
    ) -> None:
        """Initialize manager.

        Args:
            notes_path: Path to notes file *or* org journal directory (org mode).
            use_journal_mode: Use journal.md category-based parser.
            journal_categories: Category-to-agent mapping for journal mode.
            use_org_mode: Use org-roam daily journal parser (takes priority).
            org_task_sections: Org heading names to scan for tasks.
            org_lookback_days: How many recent daily files to scan.
        """
        self.path = notes_path
        self._use_org = use_org_mode
        self._use_journal = use_journal_mode and not use_org_mode
        self._category_mapping = journal_categories or {}

        if use_org_mode:
            self._org_parser = OrgTaskParser(
                journal_dir=notes_path,
                task_sections=org_task_sections,
                lookback_days=org_lookback_days,
            )
            self._writer = OrgTaskWriter(journal_dir=notes_path)
        elif use_journal_mode:
            self._writer = JournalWriter(notes_path)
        else:
            self._writer = NotesWriter(notes_path)

    def _get_parser(self) -> NotesParser | JournalParser:
        """Get a fresh parser for legacy/journal modes."""
        if self._use_journal:
            return JournalParser.from_file(self.path, self._category_mapping)
        return NotesParser.from_file(self.path)

    def get_frontmatter(self) -> dict:
        """Get the YAML frontmatter configuration."""
        return self._get_parser().parse_frontmatter()

    def get_all_tasks(self) -> list[ParsedTask]:
        """Get all tasks from the notes file."""
        if self._use_org:
            return self._org_parser.parse_tasks()
        return self._get_parser().parse_tasks()

    def get_pending_tasks(self) -> list[ParsedTask]:
        """Get all tasks with PENDING status."""
        if self._use_org:
            return self._org_parser.get_pending_tasks()
        return self._get_parser().get_pending_tasks()

    def get_tasks_by_status(self, status: TaskStatus) -> list[ParsedTask]:
        """Get all tasks with the specified status."""
        return [t for t in self.get_all_tasks() if t.status == status]

    def update_task_status(
        self,
        task: ParsedTask,
        new_status: TaskStatus,
        blocked_reason: str | None = None,
    ) -> None:
        """Update the status of a task."""
        self._writer.update_task_status(task, new_status, blocked_reason)

    def mark_task_in_progress(self, task: ParsedTask) -> None:
        """Mark a task as in progress."""
        self.update_task_status(task, TaskStatus.IN_PROGRESS)

    def mark_task_completed(self, task: ParsedTask) -> None:
        """Mark a task as completed."""
        self.update_task_status(task, TaskStatus.COMPLETED)

    def mark_task_blocked(self, task: ParsedTask, reason: str | None = None) -> None:
        """Mark a task as blocked."""
        self.update_task_status(task, TaskStatus.BLOCKED, blocked_reason=reason)
        if reason:
            self.append_message(task.agent, f"Task blocked: {reason}")

    def append_message(self, agent: str, content: str) -> None:
        """Append a message to the communication thread."""
        message = AgentMessage(
            timestamp=datetime.now(),
            agent=agent,
            content=content,
        )
        self._writer.append_to_communication_thread(message)

    def record_task_result(
        self,
        task: ParsedTask,
        success: bool,
        output_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Record the result of a task execution."""
        self._writer.add_task_result_message(
            task=task,
            success=success,
            output_path=output_path,
            error_message=error_message,
        )

    def file_exists(self) -> bool:
        """Check if the notes file (or journal directory for org mode) exists."""
        return self.path.exists()
