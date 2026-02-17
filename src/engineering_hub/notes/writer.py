"""Writer for updating shared notes file."""

import re
from datetime import datetime
from pathlib import Path

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import AgentMessage, ParsedTask


class NotesWriter:
    """Writer for updating shared notes file in-place."""

    def __init__(self, path: Path) -> None:
        """Initialize writer with file path."""
        self.path = path

    def _read_content(self) -> str:
        """Read current file content."""
        return self.path.read_text(encoding="utf-8")

    def _write_content(self, content: str) -> None:
        """Write content to file."""
        self.path.write_text(content, encoding="utf-8")

    def update_task_status(self, task: ParsedTask, new_status: TaskStatus) -> None:
        """Update the status of a task in the notes file."""
        content = self._read_content()
        lines = content.split("\n")

        # Find and update the task header line
        header_pattern = re.compile(
            rf"^###\s+@{re.escape(task.agent)}:\s+{task.status.value}\s*$"
        )

        for i in range(task.start_line, min(task.end_line + 1, len(lines))):
            if header_pattern.match(lines[i]):
                lines[i] = f"### @{task.agent}: {new_status.value}"
                break

        self._write_content("\n".join(lines))

    def append_to_communication_thread(self, message: AgentMessage) -> None:
        """Append a message to the Agent Communication Thread section."""
        content = self._read_content()
        lines = content.split("\n")

        # Find the communication thread section
        thread_index = None
        for i, line in enumerate(lines):
            if line.strip() == "## Agent Communication Thread":
                thread_index = i
                break

        if thread_index is None:
            # Create the section if it doesn't exist
            lines.append("")
            lines.append("## Agent Communication Thread")
            lines.append("")
            lines.append(message.format_for_notes())
        else:
            # Find the next section to insert before it
            insert_index = thread_index + 1

            # Skip any existing content until we find the next ## section or end
            for i in range(thread_index + 1, len(lines)):
                if lines[i].startswith("## ") and i > thread_index:
                    insert_index = i
                    break
                insert_index = i + 1

            # Insert the message before the next section
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
            content = f"Task completed successfully.\n"
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
