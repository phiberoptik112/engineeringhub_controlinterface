"""Writer for updating journal markdown file in-place."""

import re
from datetime import datetime
from pathlib import Path

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import AgentMessage, ParsedTask


class JournalWriter:
    """Writer for journal format - updates checkboxes and communication thread."""

    IN_PROGRESS_SUFFIX = " (in progress)"
    BLOCKED_SUFFIX_PREFIX = " (blocked: "

    def __init__(self, path: Path) -> None:
        """Initialize writer with journal file path."""
        self.path = path

    def _read_content(self) -> str:
        """Read current file content."""
        return self.path.read_text(encoding="utf-8")

    def _write_content(self, content: str) -> None:
        """Write content to file."""
        self.path.write_text(content, encoding="utf-8")

    def update_task_status(
        self,
        task: ParsedTask,
        new_status: TaskStatus,
        blocked_reason: str | None = None,
    ) -> None:
        """Update the status of a task in the journal (checkbox line)."""
        if task.start_line >= 0 and task.journal_date is None:
            return  # Not a journal task, no-op

        content = self._read_content()
        lines = content.split("\n")

        if task.start_line >= len(lines):
            return

        line = lines[task.start_line]

        if new_status == TaskStatus.COMPLETED:
            # Change - [ ] to - [x], remove (in progress) or (blocked: ...) if present
            lines[task.start_line] = self._mark_completed(line)
        elif new_status == TaskStatus.IN_PROGRESS:
            # Add (in progress) suffix if not already present
            lines[task.start_line] = self._mark_in_progress(line)
        elif new_status == TaskStatus.BLOCKED:
            lines[task.start_line] = self._mark_blocked(line, blocked_reason)
        # PENDING: no change to checkbox

        self._write_content("\n".join(lines))

    def _mark_completed(self, line: str) -> str:
        """Change unchecked to checked, remove status suffixes."""
        # Remove (in progress) or (blocked: ...)
        result = re.sub(r"\s*\(in progress\)\s*$", "", line)
        result = re.sub(r"\s*\(blocked:[^)]*\)\s*$", "", result)
        # Change [ ] to [x]
        result = re.sub(r"\[\s\]", "[x]", result, count=1)
        return result

    def _mark_in_progress(self, line: str) -> str:
        """Add (in progress) suffix if not present."""
        if self.IN_PROGRESS_SUFFIX in line:
            return line
        return line.rstrip() + self.IN_PROGRESS_SUFFIX

    def _mark_blocked(self, line: str, reason: str | None = None) -> str:
        """Add (blocked: reason) suffix."""
        # Remove existing blocked suffix if present
        result = re.sub(r"\s*\(blocked:[^)]*\)\s*$", "", line)
        suffix = self.BLOCKED_SUFFIX_PREFIX + (reason or "see thread") + ")"
        return result.rstrip() + " " + suffix

    def append_to_communication_thread(self, message: AgentMessage) -> None:
        """Append a message to the Agent Communication Thread section."""
        content = self._read_content()
        lines = content.split("\n")

        thread_index = None
        for i, line in enumerate(lines):
            if line.strip() == "## Agent Communication Thread":
                thread_index = i
                break

        if thread_index is None:
            lines.append("")
            lines.append("## Agent Communication Thread")
            lines.append("")
            lines.append(message.format_for_notes())
        else:
            insert_index = thread_index + 1
            for i in range(thread_index + 1, len(lines)):
                if lines[i].startswith("## ") and i > thread_index:
                    insert_index = i
                    break
                insert_index = i + 1

            lines.insert(insert_index, "")
            lines.insert(insert_index + 1, message.format_for_notes())

        self._write_content("\n".join(lines))

    def add_task_result_message(
        self,
        task: ParsedTask,
        success: bool,
        output_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Add a result message for a completed task."""
        timestamp = datetime.now()

        if success:
            content = "Task completed successfully.\n"
            if output_path:
                content += f"Output: [[{output_path}]]"
        else:
            content = f"Task failed: {error_message or 'Unknown error'}"

        message = AgentMessage(
            timestamp=timestamp,
            agent=task.agent,
            content=content,
        )
        self.append_to_communication_thread(message)
