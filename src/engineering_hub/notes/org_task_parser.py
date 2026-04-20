"""Parser for agent tasks embedded in org-roam daily journal files.

Tasks are written under a configured org heading (default: ``* Overnight Agent Tasks``)
using the format::

    - [ ] @research: Draft test protocol [[django://project/25]] → [[/outputs/docs/protocol-25.md]]
    - [ ] @technical-writer: Expand section 3 on ASTM E336
    - [x] @research: Already completed task (skipped)

The ``@agent-type:`` prefix determines dispatch target.  Project and deliverable
wikilinks follow the same conventions as the legacy ``journal.md`` system.
"""

import re
from datetime import date, timedelta
from pathlib import Path

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import ParsedTask
from engineering_hub.notes.weekly_reader import OrgDayEntry, OrgJournalReader

# ---------------------------------------------------------------------------
# Shared regex (mirrors journal_parser.py conventions)
# ---------------------------------------------------------------------------

# Matches: - [ ] @agent-type: description...
_TASK_ITEM = re.compile(
    r"^(?P<indent>\s*)[-*]\s+\[(?P<check>[ xX])\]\s+@(?P<agent>[\w-]+):\s+(?P<text>.+)$"
)

# [[django://project/25]]
_PROJECT_REF = re.compile(r"\[\[django://project/(?P<project_id>\d+)\]\]")

# → [[/path]] at end of text
_DELIVERABLE_ARROW = re.compile(r"\s*→\s*\[\[(?P<path>[^\]]+)\]\]\s*$")

# [[/outputs/...]] anywhere
_OUTPUT_PATH = re.compile(r"\[\[(/outputs/[^\]]+)\]\]")

# any [[wikilink]]
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")

# status suffixes written back by OrgTaskWriter
_IN_PROGRESS_RE = re.compile(r"\s*\(in progress\)\s*$")
_BLOCKED_RE = re.compile(r"\s*\(blocked:[^)]*\)\s*$")

# Top-level org headings (single star) — for standalone queue files without subheading collapse
_TOP_ORG_HEADING = re.compile(r"^\* (.+)$", re.MULTILINE)


def _split_top_level_sections_raw(text: str) -> dict[str, str]:
    """Split org on top-level `* Heading`; preserve `**` subheadings in bodies."""
    headings = list(_TOP_ORG_HEADING.finditer(text))
    if not headings:
        return {}
    sections: dict[str, str] = {}
    for i, match in enumerate(headings):
        heading = match.group(1).strip()
        body_start = match.end()
        body_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        sections[heading] = text[body_start:body_end]
    return sections


class OrgTaskParser:
    """Extracts ``ParsedTask`` objects from org-roam daily journal files.

    Parameters
    ----------
    journal_dir:
        Directory containing ``YYYY-MM-DD.org`` files.
    task_sections:
        Org heading names to scan for tasks.  Items in these sections that
        begin with ``@agent-type:`` are extracted.
    lookback_days:
        Number of recent days to scan (today = 1, yesterday + today = 2, …).
    """

    def __init__(
        self,
        journal_dir: Path,
        task_sections: list[str] | None = None,
        lookback_days: int = 1,
        extra_files: list[Path] | None = None,
    ) -> None:
        self.journal_dir = journal_dir
        self.task_sections: list[str] = task_sections or ["Overnight Agent Tasks"]
        self.lookback_days = lookback_days
        self.extra_files: list[Path] = [p.expanduser().resolve() for p in (extra_files or [])]
        self._reader = OrgJournalReader(journal_dir)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_pending_tasks(self) -> list[ParsedTask]:
        """Return all unchecked, non-in-progress tasks from recent journal files."""
        return self.parse_tasks()

    def parse_tasks(self) -> list[ParsedTask]:
        """Parse tasks from the last ``lookback_days`` days of journal files.

        Only unchecked (``[ ]``) items without an ``(in progress)`` or
        ``(blocked: …)`` suffix are returned with ``PENDING`` status.
        """
        entries = self._reader.collect_week(days=self.lookback_days)
        tasks: list[ParsedTask] = []

        for entry in entries:
            file_path = self.journal_dir / f"{entry.date.isoformat()}.org"
            for section_name in self.task_sections:
                body = entry.sections.get(section_name, "")
                if not body.strip():
                    continue
                section_tasks = self._extract_tasks_from_body(
                    body=body,
                    journal_date=entry.date.isoformat(),
                    file_path=file_path,
                    section_name=section_name,
                    source_path=None,
                )
                tasks.extend(section_tasks)

        for xf in self.extra_files:
            if xf.is_file():
                tasks.extend(self._parse_extra_org_file(xf))

        return tasks

    def org_file_for_date(self, journal_date: str) -> Path:
        """Return the path to the org file for a given ISO date string."""
        return self.journal_dir / f"{journal_date}.org"

    def _parse_extra_org_file(self, file_path: Path) -> list[ParsedTask]:
        """Parse task sections from a standalone org file (e.g. pending-tasks.org)."""
        raw = file_path.read_text(encoding="utf-8")
        sections = _split_top_level_sections_raw(raw)
        resolved = str(file_path.resolve())
        found: list[ParsedTask] = []
        for section_name in self.task_sections:
            body = sections.get(section_name, "")
            if not body.strip():
                continue
            found.extend(
                self._extract_tasks_from_body(
                    body=body,
                    journal_date=None,
                    file_path=file_path,
                    section_name=section_name,
                    source_path=resolved,
                )
            )
        return found

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_tasks_from_body(
        self,
        body: str,
        journal_date: str | None,
        file_path: Path,
        section_name: str,
        source_path: str | None = None,
    ) -> list[ParsedTask]:
        """Parse task items from a single section body."""
        tasks: list[ParsedTask] = []
        # We need line offsets within the full file to support write-back.
        # Re-read the file to find absolute line numbers.
        abs_line_map = self._build_line_map(file_path, section_name)

        for rel_idx, line in enumerate(body.splitlines()):
            match = _TASK_ITEM.match(line)
            if not match:
                continue

            checked = match.group("check").lower() == "x"
            if checked:
                continue

            raw_text = match.group("text")

            # Skip in-progress and blocked items
            if _IN_PROGRESS_RE.search(raw_text) or _BLOCKED_RE.search(raw_text):
                continue

            abs_line = abs_line_map.get(rel_idx)
            if abs_line is None:
                # Fall back: store relative index (write-back will re-scan)
                abs_line = rel_idx

            task = self._build_task(
                raw_text=raw_text,
                original_line=line,
                agent=match.group("agent"),
                journal_date=journal_date,
                category=section_name,
                abs_line=abs_line,
                source_path=source_path,
            )
            tasks.append(task)

        return tasks

    def _build_line_map(self, file_path: Path, section_name: str) -> dict[int, int]:
        """Map relative line index within a section body to absolute file line numbers.

        Returns ``{relative_body_line: absolute_file_line}``.
        """
        if not file_path.exists():
            return {}

        lines = file_path.read_text(encoding="utf-8").splitlines()
        in_section = False
        rel_idx = 0
        result: dict[int, int] = {}

        for abs_idx, line in enumerate(lines):
            stripped = line.strip()

            # Detect top-level heading matching our section
            if stripped == f"* {section_name}":
                in_section = True
                rel_idx = 0
                continue

            # Leave section on next top-level heading
            if in_section and stripped.startswith("* ") and not stripped.startswith("**"):
                break

            if in_section:
                # OrgJournalReader collapses sub-headings; track raw body lines
                result[rel_idx] = abs_idx
                rel_idx += 1

        return result

    def _build_task(
        self,
        raw_text: str,
        original_line: str,
        agent: str,
        journal_date: str | None,
        category: str,
        abs_line: int,
        source_path: str | None = None,
    ) -> ParsedTask:
        text = raw_text

        # Extract deliverable (→ [[path]])
        deliverable: str | None = None
        deliverable_match = _DELIVERABLE_ARROW.search(text)
        if deliverable_match:
            deliverable = deliverable_match.group("path")
            text = _DELIVERABLE_ARROW.sub("", text).strip()

        # Fallback deliverable: [[/outputs/...]]
        if not deliverable:
            output_match = _OUTPUT_PATH.search(text)
            if output_match:
                deliverable = output_match.group(1)

        # Project ID
        project_match = _PROJECT_REF.search(text)
        project_id = int(project_match.group("project_id")) if project_match else None

        # Input paths: all wikilinks that aren't the deliverable, django:// refs,
        # or org-roam internal cross-links (roam: prefix).
        all_links = _WIKILINK.findall(raw_text)
        input_paths = [
            p
            for p in all_links
            if not p.startswith("django://")
            and not p.startswith("roam:")
            and p != deliverable
        ]

        description = text.strip() or raw_text.strip()

        return ParsedTask(
            agent=agent,
            status=TaskStatus.PENDING,
            project_id=project_id,
            description=description,
            context=None,
            deliverable=deliverable,
            input_paths=input_paths,
            start_line=abs_line,
            end_line=abs_line,
            raw_block=original_line,
            journal_date=journal_date,
            category=category,
            source_path=source_path,
        )
