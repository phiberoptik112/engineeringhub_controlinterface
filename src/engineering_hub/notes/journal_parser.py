"""Parser for journal markdown file with dated sections and category-based tasks."""

import re
from pathlib import Path

import yaml

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.exceptions import NotesParseError
from engineering_hub.core.models import ParsedTask

# YAML frontmatter
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Match date sections: ## YYYY-MM-DD
DATE_SECTION_PATTERN = re.compile(r"^##\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)

# Match category headers: ### Category Name
CATEGORY_HEADER_PATTERN = re.compile(r"^###\s+(?P<category>.+?)\s*$", re.MULTILINE)

# Match checkbox list items: - [ ] or - [x]
CHECKBOX_ITEM_PATTERN = re.compile(r"^(\s*)[-\*]\s+\[([ xX])\]\s+(.+)$")

# Match project references: [[django://project/25]]
PROJECT_REF_PATTERN = re.compile(r"\[\[django://project/(?P<project_id>\d+)\]\]")

# Match deliverable: → [[path]] at end of line
DELIVERABLE_ARROW_PATTERN = re.compile(r"\s*→\s*\[\[(?P<path>[^\]]+)\]\]\s*$")

# Match any output path: [[/outputs/...]]
OUTPUT_PATH_PATTERN = re.compile(r"\[\[(/outputs/[^\]]+)\]\]")


class JournalParser:
    """Parser for journal format with dated sections and category-based task extraction."""

    def __init__(self, content: str, category_mapping: dict[str, str]) -> None:
        """Initialize parser with content and category-to-agent mapping.

        Args:
            content: Journal file content
            category_mapping: Dict mapping category header names to agent types
        """
        self.content = content
        self.lines = content.split("\n")
        self.category_mapping = category_mapping

    @classmethod
    def from_file(
        cls, path: Path, category_mapping: dict[str, str]
    ) -> "JournalParser":
        """Create parser from file path."""
        if not path.exists():
            raise NotesParseError(f"Journal file not found: {path}")
        return cls(path.read_text(encoding="utf-8"), category_mapping)

    def parse_frontmatter(self) -> dict:
        """Extract YAML frontmatter from the journal file."""
        match = FRONTMATTER_PATTERN.match(self.content)
        if not match:
            return {}
        try:
            return yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as e:
            raise NotesParseError(f"Invalid YAML frontmatter: {e}")

    def parse_tasks(self) -> list[ParsedTask]:
        """Extract all tasks from the journal (unchecked items under mapped categories)."""
        tasks = []
        current_date: str | None = None
        current_category: str | None = None
        current_agent: str | None = None

        for i, line in enumerate(self.lines):
            # Check for date section
            date_match = DATE_SECTION_PATTERN.match(line.strip())
            if date_match:
                current_date = date_match.group("date")
                current_category = None
                current_agent = None
                continue

            # Check for category header
            category_match = CATEGORY_HEADER_PATTERN.match(line.strip())
            if category_match and current_date:
                current_category = category_match.group("category").strip()
                current_agent = self.category_mapping.get(current_category)
                continue

            # Check for checkbox item under a mapped category
            if current_agent and current_date and current_category:
                checkbox_match = CHECKBOX_ITEM_PATTERN.match(line)
                if checkbox_match:
                    checked = checkbox_match.group(2).lower() == "x"
                    raw_text = checkbox_match.group(3)

                    # Skip items already in progress or blocked
                    if "(in progress)" in line or "(blocked:" in line:
                        continue

                    # Only unchecked items become PENDING tasks
                    if not checked:
                        task = self._parse_list_item(
                            raw_text=raw_text,
                            agent=current_agent,
                            journal_date=current_date,
                            category=current_category,
                            start_line=i,
                            end_line=i,
                            original_line=line,
                        )
                        tasks.append(task)

        return tasks

    def _parse_list_item(
        self,
        raw_text: str,
        agent: str,
        journal_date: str,
        category: str,
        start_line: int,
        end_line: int,
        original_line: str,
    ) -> ParsedTask:
        """Parse a list item into a ParsedTask."""
        # Extract deliverable from → [[path]] at end
        text = raw_text
        deliverable = None
        deliverable_match = DELIVERABLE_ARROW_PATTERN.search(text)
        if deliverable_match:
            deliverable = deliverable_match.group("path")
            text = DELIVERABLE_ARROW_PATTERN.sub("", text).strip()

        # Fallback: find [[/outputs/...]] anywhere
        if not deliverable:
            output_match = OUTPUT_PATH_PATTERN.search(text)
            if output_match:
                deliverable = output_match.group(1)

        # Extract project ID
        project_match = PROJECT_REF_PATTERN.search(text)
        project_id = int(project_match.group("project_id")) if project_match else None

        # Description is the remaining text (strip wikilinks for cleaner description)
        description = text.strip()
        if not description:
            description = raw_text.strip()

        return ParsedTask(
            agent=agent,
            status=TaskStatus.PENDING,
            project_id=project_id,
            description=description,
            context=None,
            deliverable=deliverable,
            start_line=start_line,
            end_line=end_line,
            raw_block=original_line,
            journal_date=journal_date,
            category=category,
        )

    def get_pending_tasks(self) -> list[ParsedTask]:
        """Get all pending tasks (all tasks from parse_tasks are PENDING)."""
        return self.parse_tasks()

    def get_communication_thread_position(self) -> int | None:
        """Find the line number of the Agent Communication Thread section."""
        for i, line in enumerate(self.lines):
            if line.strip() == "## Agent Communication Thread":
                return i
        return None
