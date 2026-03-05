"""Reader for org-roam daily journal files used in weekly review aggregation."""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


# Matches the top-level :PROPERTIES: ... :END: block at the start of a file
_PROPERTIES_BLOCK = re.compile(r"^:PROPERTIES:.*?:END:\s*\n", re.DOTALL)

# Matches #+keyword: value lines (org-mode file-level metadata)
_KEYWORD_LINE = re.compile(r"^\s*#\+[A-Za-z_]+:.*\n", re.MULTILINE)

# Matches a top-level org heading:  * Heading Text
_TOP_HEADING = re.compile(r"^\* (.+)$", re.MULTILINE)


@dataclass
class OrgDayEntry:
    """Parsed content of a single org-roam journal file."""

    date: date
    title: str
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def weekday_name(self) -> str:
        return self.date.strftime("%A")

    def is_empty(self) -> bool:
        """True when every section is blank after stripping whitespace."""
        return all(not v.strip() for v in self.sections.values())


class OrgJournalReader:
    """Reads org-roam daily journal files from a directory for weekly review."""

    def __init__(self, journal_dir: Path) -> None:
        self.journal_dir = journal_dir

    def collect_week(self, days: int = 7) -> list[OrgDayEntry]:
        """Return parsed entries for the last *days* days (most recent last).

        Only files that actually exist are included; missing days are skipped.
        """
        today = date.today()
        entries: list[OrgDayEntry] = []

        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            path = self.journal_dir / f"{day.isoformat()}.org"
            if path.exists():
                entry = self._parse_file(path, day)
                entries.append(entry)

        return entries

    def format_context(self, entries: list[OrgDayEntry]) -> str:
        """Render a list of OrgDayEntry objects as a structured context block.

        Returns a plain string (not XML-wrapped — caller wraps in <journal_content>).
        """
        if not entries:
            return "(No journal entries found for this period.)"

        lines: list[str] = []
        for entry in entries:
            lines.append(f"## {entry.date.isoformat()} {entry.weekday_name}")
            lines.append("")

            if not entry.sections:
                lines.append("*(no content)*")
                lines.append("")
                continue

            for heading, body in entry.sections.items():
                body_stripped = body.strip()
                lines.append(f"### {heading}")
                if body_stripped:
                    lines.append(body_stripped)
                else:
                    lines.append("*(empty)*")
                lines.append("")

        return "\n".join(lines).rstrip()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_file(self, path: Path, day: date) -> OrgDayEntry:
        """Parse a single .org journal file into an OrgDayEntry."""
        raw = path.read_text(encoding="utf-8")

        # Strip :PROPERTIES: ... :END: block
        raw = _PROPERTIES_BLOCK.sub("", raw, count=1)

        # Strip #+keyword lines and extract title while we're at it
        title = f"Journal Entry {day.isoformat()}"
        for match in _KEYWORD_LINE.finditer(raw):
            line = match.group(0).strip()
            if line.lower().startswith("#+title:"):
                title = line.split(":", 1)[1].strip()
        raw = _KEYWORD_LINE.sub("", raw)

        sections = self._split_sections(raw)
        return OrgDayEntry(date=day, title=title, sections=sections)

    def _split_sections(self, text: str) -> dict[str, str]:
        """Split org text on top-level `* Heading` markers.

        Returns an ordered dict of {heading: body_text}.
        Any content before the first heading is discarded (usually blank).
        """
        headings = list(_TOP_HEADING.finditer(text))
        if not headings:
            return {}

        sections: dict[str, str] = {}
        for i, match in enumerate(headings):
            heading = match.group(1).strip()
            body_start = match.end()
            body_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[body_start:body_end]
            # Collapse sub-headings (** or deeper) into plain text by stripping leading *s
            body = re.sub(r"^\*{2,}\s+", "  ", body, flags=re.MULTILINE)
            sections[heading] = body

        return sections
