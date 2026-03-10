"""Writer for updating task status in org-roam daily journal files.

Handles two write-back operations:

1. **Checkbox status** — updates the ``[ ]``/``[x]`` marker and appends/removes
   ``(in progress)`` or ``(blocked: reason)`` suffixes on the task line.

2. **Agent messages** — appends timestamped entries under a
   ``* Engineering Hub Messages`` heading in the same ``.org`` file, creating
   the heading if it does not yet exist.
"""

import re
from datetime import datetime
from pathlib import Path

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import AgentMessage, ParsedTask


# Status suffix patterns (mirrors JournalWriter)
_IN_PROGRESS_SUFFIX = " (in progress)"
_BLOCKED_PREFIX = " (blocked: "

_IN_PROGRESS_RE = re.compile(r"\s*\(in progress\)\s*$")
_BLOCKED_RE = re.compile(r"\s*\(blocked:[^)]*\)\s*$")
_CHECKBOX_RE = re.compile(r"\[\s\]")

# Heading used for agent feedback inside the org file
_MESSAGES_HEADING = "* Engineering Hub Messages"


class OrgTaskWriter:
    """Writes task status updates and agent messages back to ``.org`` journal files.

    The writer resolves the target file from ``task.journal_date`` at call-time,
    so it does not hold a reference to a specific file path.

    Parameters
    ----------
    journal_dir:
        Directory containing ``YYYY-MM-DD.org`` files.
    """

    def __init__(self, journal_dir: Path) -> None:
        self.journal_dir = journal_dir

    # ------------------------------------------------------------------
    # Task status
    # ------------------------------------------------------------------

    def update_task_status(
        self,
        task: ParsedTask,
        new_status: TaskStatus,
        blocked_reason: str | None = None,
    ) -> None:
        """Rewrite the checkbox line for *task* in its ``.org`` file."""
        if task.journal_date is None:
            return

        org_path = self.journal_dir / f"{task.journal_date}.org"
        if not org_path.exists():
            return

        lines = org_path.read_text(encoding="utf-8").splitlines(keepends=True)

        if task.start_line >= len(lines):
            # Line number stale — attempt a rescan by content
            task_line_idx = self._find_task_line(lines, task.raw_block)
            if task_line_idx is None:
                return
        else:
            task_line_idx = task.start_line
            # Verify the line still looks like our task; rescan if not
            if not self._line_matches_task(lines[task_line_idx], task):
                found = self._find_task_line(lines, task.raw_block)
                if found is None:
                    return
                task_line_idx = found

        line = lines[task_line_idx].rstrip("\n")

        if new_status == TaskStatus.COMPLETED:
            line = self._mark_completed(line)
        elif new_status == TaskStatus.IN_PROGRESS:
            line = self._mark_in_progress(line)
        elif new_status == TaskStatus.BLOCKED:
            line = self._mark_blocked(line, blocked_reason)

        lines[task_line_idx] = line + "\n"
        org_path.write_text("".join(lines), encoding="utf-8")

    # ------------------------------------------------------------------
    # Agent messages
    # ------------------------------------------------------------------

    def append_to_communication_thread(self, message: AgentMessage) -> None:
        """Append *message* to the Engineering Hub Messages section.

        Uses today's journal file.  The section is created if absent.
        """
        today = datetime.now().date().isoformat()
        org_path = self.journal_dir / f"{today}.org"
        self._append_message_to_file(org_path, message)

    def add_task_result_message(
        self,
        task: ParsedTask,
        success: bool,
        output_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Write a completion/failure message for *task* into its journal file."""
        if success:
            content = "Task completed successfully."
            if output_path:
                content += f"\nOutput: [[{output_path}]]"
        else:
            content = f"Task failed: {error_message or 'Unknown error'}"

        message = AgentMessage(
            timestamp=datetime.now(),
            agent=task.agent,
            content=content,
        )

        # Write to the file where the task originated, if it exists
        if task.journal_date:
            org_path = self.journal_dir / f"{task.journal_date}.org"
        else:
            today = datetime.now().date().isoformat()
            org_path = self.journal_dir / f"{today}.org"

        self._append_message_to_file(org_path, message)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_message_to_file(self, org_path: Path, message: AgentMessage) -> None:
        """Append *message* to the Engineering Hub Messages section of *org_path*."""
        if not org_path.exists():
            # Create a minimal file so messages are not lost
            org_path.write_text(
                f"#+title: Journal Entry {org_path.stem}\n\n{_MESSAGES_HEADING}\n\n",
                encoding="utf-8",
            )

        content = org_path.read_text(encoding="utf-8")
        formatted = self._format_org_message(message)

        if _MESSAGES_HEADING in content:
            # Insert right after the heading line
            idx = content.index(_MESSAGES_HEADING) + len(_MESSAGES_HEADING)
            # Skip any blank lines immediately after the heading
            rest = content[idx:]
            content = content[:idx] + "\n\n" + formatted + rest
        else:
            # Append new section at end of file
            sep = "\n" if content.endswith("\n") else "\n\n"
            content = content + sep + _MESSAGES_HEADING + "\n\n" + formatted + "\n"

        org_path.write_text(content, encoding="utf-8")

    @staticmethod
    def _format_org_message(message: AgentMessage) -> str:
        ts = message.timestamp.strftime("%Y-%m-%d %H:%M")
        header = f"** [{ts}] @{message.agent}"
        body = message.content.strip()
        return f"{header}\n{body}\n"

    @staticmethod
    def _mark_completed(line: str) -> str:
        line = _IN_PROGRESS_RE.sub("", line)
        line = _BLOCKED_RE.sub("", line)
        return _CHECKBOX_RE.sub("[x]", line, count=1)

    @staticmethod
    def _mark_in_progress(line: str) -> str:
        if _IN_PROGRESS_SUFFIX in line:
            return line
        line = _BLOCKED_RE.sub("", line).rstrip()
        return line + _IN_PROGRESS_SUFFIX

    @staticmethod
    def _mark_blocked(line: str, reason: str | None = None) -> str:
        line = _IN_PROGRESS_RE.sub("", line)
        line = _BLOCKED_RE.sub("", line).rstrip()
        return line + _BLOCKED_PREFIX + (reason or "see messages") + ")"

    @staticmethod
    def _line_matches_task(line: str, task: ParsedTask) -> bool:
        """Quick sanity check that *line* looks like the expected task line."""
        return f"@{task.agent}:" in line

    @staticmethod
    def _find_task_line(lines: list[str], raw_block: str) -> int | None:
        """Scan *lines* for a line that matches *raw_block* content."""
        needle = raw_block.strip()
        for idx, line in enumerate(lines):
            if line.strip() == needle:
                return idx
        return None
