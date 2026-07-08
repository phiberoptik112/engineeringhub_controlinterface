"""Per-persona history storage for the Discussion Briefing system.

Each persona in the discussion briefing maintains an append-only JSONL log of its
statements across discussion sessions.  A daily summary can be generated from those
statements and written as a markdown file.  Recent history is injected back into each
persona's LLM prompt so its discussion contributions are grounded in what it said before.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_HISTORY_FILENAME = "history.jsonl"
_SUMMARIES_DIR = "summaries"


@dataclass
class PersonaStatement:
    """A single statement made by a persona during a discussion session."""

    timestamp: str
    discussion_date: str
    persona_id: str
    topic: str
    statement: str
    source: str = "discussion"  # "discussion" | "coordination_scan"

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "discussion_date": self.discussion_date,
            "persona_id": self.persona_id,
            "topic": self.topic,
            "statement": self.statement,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> "PersonaStatement":
        return cls(
            timestamp=d.get("timestamp", ""),
            discussion_date=d.get("discussion_date", ""),
            persona_id=d.get("persona_id", ""),
            topic=d.get("topic", ""),
            statement=d.get("statement", ""),
            source=d.get("source", "discussion"),
        )


class PersonaHistoryStore:
    """Manages per-persona append-only history for the Discussion Briefing system.

    Storage layout under ``base_dir``::

        {base_dir}/
            {persona_id}/
                history.jsonl         ← all statements, newest last
                summaries/
                    YYYY-MM-DD.md     ← daily summary for that persona

    Usage::

        store = PersonaHistoryStore(Path(".journaler/personas"))
        store.append("project-manager", "2026-05-26", "today's agenda", "Statement text…")
        recent = store.get_recent("project-manager", n_days=7)
        block = store.format_context_block("project-manager", n_days=7)
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def _persona_dir(self, persona_id: str) -> Path:
        return self._base / persona_id

    def _history_path(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / _HISTORY_FILENAME

    def _summaries_dir(self, persona_id: str) -> Path:
        return self._persona_dir(persona_id) / _SUMMARIES_DIR

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(
        self,
        persona_id: str,
        discussion_date: str,
        topic: str,
        statement: str,
        *,
        source: str = "discussion",
    ) -> None:
        """Append a persona statement to the history log.

        Args:
            persona_id: Persona identifier, e.g. ``"project-manager"``.
            discussion_date: ISO date string for the discussion, e.g. ``"2026-05-26"``.
            topic: Short topic or briefing title for this statement.
            statement: The full statement text.
            source: Origin tag — ``"discussion"`` or ``"coordination_scan"``.
        """
        pdir = self._persona_dir(persona_id)
        pdir.mkdir(parents=True, exist_ok=True)
        entry = PersonaStatement(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            discussion_date=discussion_date,
            persona_id=persona_id,
            topic=topic,
            statement=statement,
            source=source,
        )
        hpath = self._history_path(persona_id)
        try:
            with open(hpath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
        except OSError as exc:
            logger.warning("PersonaHistoryStore: failed to write %s: %s", hpath, exc)

    def write_summary(self, persona_id: str, summary_date: str, text: str) -> Path:
        """Write a daily summary for a persona.

        Args:
            persona_id: Persona identifier.
            summary_date: ISO date string, e.g. ``"2026-05-26"``.
            text: Markdown summary text.

        Returns:
            Path to the written summary file.
        """
        sdir = self._summaries_dir(persona_id)
        sdir.mkdir(parents=True, exist_ok=True)
        path = sdir / f"{summary_date}.md"
        path.write_text(text, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent(
        self,
        persona_id: str,
        *,
        n_days: int = 7,
    ) -> list[PersonaStatement]:
        """Return all statements for a persona within the last ``n_days`` days.

        Statements are returned newest-last (chronological order).
        """
        hpath = self._history_path(persona_id)
        if not hpath.exists():
            return []

        cutoff = (date.today() - timedelta(days=n_days)).isoformat()
        statements: list[PersonaStatement] = []
        try:
            with open(hpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("discussion_date", "") >= cutoff:
                        statements.append(PersonaStatement.from_dict(d))
        except OSError as exc:
            logger.warning(
                "PersonaHistoryStore: failed to read %s: %s", hpath, exc
            )
        return statements

    def get_summary(self, persona_id: str, summary_date: str) -> str | None:
        """Load a daily summary for a persona, or None if it doesn't exist."""
        path = self._summaries_dir(persona_id) / f"{summary_date}.md"
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                pass
        return None

    def format_context_block(
        self,
        persona_id: str,
        display_name: str,
        *,
        n_days: int = 7,
        max_statements: int = 10,
    ) -> str:
        """Return a formatted markdown block of recent history for prompt injection.

        The block is injected into the persona's system or user prompt so the LLM
        can ground its current statement in what the persona said in prior sessions.

        Returns an empty string when there is no recent history.
        """
        statements = self.get_recent(persona_id, n_days=n_days)
        if not statements:
            return ""

        # Keep the most recent N statements to avoid overwhelming the context
        recent = statements[-max_statements:]

        lines: list[str] = [
            f"### {display_name} — Past Context (last {n_days} days)",
            "",
        ]
        for stmt in recent:
            date_label = stmt.discussion_date
            source_tag = (
                " *(coordination scan)*" if stmt.source == "coordination_scan" else ""
            )
            lines.append(f"**{date_label}**{source_tag} — *{stmt.topic}*")
            lines.append("")
            # Indent statement for readability
            for line in stmt.statement.splitlines():
                lines.append(f"> {line}" if line.strip() else ">")
            lines.append("")

        return "\n".join(lines)
