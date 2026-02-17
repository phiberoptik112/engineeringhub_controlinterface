"""Parser for shared notes markdown file."""

import re
from pathlib import Path

import yaml

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.exceptions import NotesParseError
from engineering_hub.core.models import ParsedTask

# Regex patterns for parsing
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Match task headers like "### @research: PENDING"
TASK_HEADER_PATTERN = re.compile(
    r"^###\s+@(?P<agent>[\w-]+):\s+(?P<status>PENDING|IN_PROGRESS|COMPLETED|BLOCKED)\s*$",
    re.MULTILINE,
)

# Match project references like "[[django://project/25]]"
PROJECT_REF_PATTERN = re.compile(r"\[\[django://project/(?P<project_id>\d+)\]\]")

# Match deliverable references like "> Deliverable: [[/outputs/research/file.md]]"
DELIVERABLE_PATTERN = re.compile(r">\s*Deliverable:\s*\[\[(?P<path>[^\]]+)\]\]")


class NotesParser:
    """Parser for extracting tasks and metadata from shared notes."""

    def __init__(self, content: str) -> None:
        """Initialize parser with file content."""
        self.content = content
        self.lines = content.split("\n")

    @classmethod
    def from_file(cls, path: Path) -> "NotesParser":
        """Create parser from file path."""
        if not path.exists():
            raise NotesParseError(f"Notes file not found: {path}")
        return cls(path.read_text(encoding="utf-8"))

    def parse_frontmatter(self) -> dict:
        """Extract YAML frontmatter from the notes file."""
        match = FRONTMATTER_PATTERN.match(self.content)
        if not match:
            return {}
        try:
            return yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as e:
            raise NotesParseError(f"Invalid YAML frontmatter: {e}")

    def parse_tasks(self) -> list[ParsedTask]:
        """Extract all tasks from the notes file."""
        tasks = []

        # Find all task headers with their positions
        for match in TASK_HEADER_PATTERN.finditer(self.content):
            start_pos = match.start()
            start_line = self.content[:start_pos].count("\n")

            # Find the end of this task block (next header or section)
            end_pos = self._find_task_end(match.end())
            end_line = self.content[:end_pos].count("\n")

            # Extract the full task block
            raw_block = self.content[match.start() : end_pos].strip()

            # Parse task details
            task = self._parse_task_block(
                agent=match.group("agent"),
                status=TaskStatus(match.group("status")),
                raw_block=raw_block,
                start_line=start_line,
                end_line=end_line,
            )
            tasks.append(task)

        return tasks

    def _find_task_end(self, start_pos: int) -> int:
        """Find the end position of a task block."""
        # Look for next header (## or ###) or end of file
        remaining = self.content[start_pos:]

        # Find next section/task header
        next_header = re.search(r"\n#{2,3}\s+", remaining)
        if next_header:
            return start_pos + next_header.start()

        return len(self.content)

    def _parse_task_block(
        self,
        agent: str,
        status: TaskStatus,
        raw_block: str,
        start_line: int,
        end_line: int,
    ) -> ParsedTask:
        """Parse a task block into a ParsedTask object."""
        # Extract project ID
        project_match = PROJECT_REF_PATTERN.search(raw_block)
        project_id = int(project_match.group("project_id")) if project_match else None

        # Extract deliverable path
        deliverable_match = DELIVERABLE_PATTERN.search(raw_block)
        deliverable = deliverable_match.group("path") if deliverable_match else None

        # Extract description (line starting with "> Task:")
        description = ""
        context = ""
        for line in raw_block.split("\n"):
            if line.strip().startswith("> Task:"):
                description = line.replace("> Task:", "").strip()
            elif line.strip().startswith("> Context:"):
                context = line.replace("> Context:", "").strip()

        return ParsedTask(
            agent=agent,
            status=status,
            project_id=project_id,
            description=description,
            context=context if context else None,
            deliverable=deliverable,
            start_line=start_line,
            end_line=end_line,
            raw_block=raw_block,
        )

    def get_pending_tasks(self) -> list[ParsedTask]:
        """Get all tasks with PENDING status."""
        return [t for t in self.parse_tasks() if t.status == TaskStatus.PENDING]

    def get_communication_thread_position(self) -> int | None:
        """Find the line number of the Agent Communication Thread section."""
        for i, line in enumerate(self.lines):
            if line.strip() == "## Agent Communication Thread":
                return i
        return None
