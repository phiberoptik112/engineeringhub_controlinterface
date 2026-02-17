"""High-level manager for shared notes operations."""

from datetime import datetime
from pathlib import Path

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import AgentMessage, ParsedTask
from engineering_hub.notes.parser import NotesParser
from engineering_hub.notes.writer import NotesWriter


class SharedNotesManager:
    """Facade for shared notes parsing and writing operations."""

    def __init__(self, notes_path: Path) -> None:
        """Initialize manager with path to shared notes file."""
        self.path = notes_path
        self._writer = NotesWriter(notes_path)

    def _get_parser(self) -> NotesParser:
        """Get a fresh parser with current file content."""
        return NotesParser.from_file(self.path)

    def get_frontmatter(self) -> dict:
        """Get the YAML frontmatter configuration."""
        return self._get_parser().parse_frontmatter()

    def get_all_tasks(self) -> list[ParsedTask]:
        """Get all tasks from the notes file."""
        return self._get_parser().parse_tasks()

    def get_pending_tasks(self) -> list[ParsedTask]:
        """Get all tasks with PENDING status."""
        return self._get_parser().get_pending_tasks()

    def get_tasks_by_status(self, status: TaskStatus) -> list[ParsedTask]:
        """Get all tasks with the specified status."""
        return [t for t in self.get_all_tasks() if t.status == status]

    def update_task_status(self, task: ParsedTask, new_status: TaskStatus) -> None:
        """Update the status of a task."""
        self._writer.update_task_status(task, new_status)

    def mark_task_in_progress(self, task: ParsedTask) -> None:
        """Mark a task as in progress."""
        self.update_task_status(task, TaskStatus.IN_PROGRESS)

    def mark_task_completed(self, task: ParsedTask) -> None:
        """Mark a task as completed."""
        self.update_task_status(task, TaskStatus.COMPLETED)

    def mark_task_blocked(self, task: ParsedTask, reason: str | None = None) -> None:
        """Mark a task as blocked."""
        self.update_task_status(task, TaskStatus.BLOCKED)
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
        """Check if the notes file exists."""
        return self.path.exists()
