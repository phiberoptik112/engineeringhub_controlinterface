"""Assembles context for the weekly reviewer agent."""

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from engineering_hub.config.settings import Settings
from engineering_hub.memory import MemoryService
from engineering_hub.notes.weekly_reader import OrgJournalReader

logger = logging.getLogger(__name__)


class WeeklyReviewBuilder:
    """Builds the context string for the weekly reviewer agent."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_context(
        self,
        days: int = 7,
        focus: str | None = None,
    ) -> str:
        """Assemble journal entries, agent work, and output file list
        into a single context string for the weekly reviewer prompt.

        Returns the full context string ready to pass to AgentWorker.
        """
        journal_block = self._read_journal_entries(days)
        memory_block = self._read_agent_work(days)
        output_files_block = self._scan_output_files(days)

        period_start = (date.today() - timedelta(days=days - 1)).isoformat()
        period_end = date.today().isoformat()

        parts = [
            f"Review period: {period_start} through {period_end} ({days} days)",
            "",
            "<journal_content>",
            journal_block,
            "</journal_content>",
            "",
            "<agent_work>",
            memory_block,
            "</agent_work>",
            "",
            "<output_files>",
            output_files_block,
            "</output_files>",
        ]
        if focus:
            parts += ["", f"USER FOCUS: {focus}"]

        return "\n".join(parts)

    def default_output_path(self) -> Path:
        iso_year, iso_week, _ = date.today().isocalendar()
        return (
            self.settings.output_dir
            / "reviews"
            / f"weekly-{iso_year}-W{iso_week:02d}.md"
        )

    def _read_journal_entries(self, days: int) -> str:
        journal_dir = self.settings.org_journal_dir
        if not journal_dir.exists():
            return "(No journal directory found.)"

        reader = OrgJournalReader(journal_dir)
        entries = reader.collect_week(days=days)
        if not entries:
            return "(No journal entries found for this period.)"

        return reader.format_context(entries)

    def _read_agent_work(self, days: int) -> str:
        try:
            svc = MemoryService.from_workspace(
                workspace_dir=self.settings.workspace_dir,
                ollama_host=self.settings.ollama_host,
                ollama_model=self.settings.ollama_embed_model,
                enabled=self.settings.memory_enabled,
            )
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            recent_rows = svc.browse_recent(limit=100)
            week_rows = [
                r for r in recent_rows
                if (r.get("created_at") or "") >= cutoff
                and r.get("source") in ("task_output", "agent_message")
            ]
            svc.db.close()

            if not week_rows:
                return "(No agent work entries found for this period.)"

            lines: list[str] = []
            for r in week_rows:
                day = (r.get("created_at") or "")[:10]
                agent = f"@{r['agent']}" if r.get("agent") else "agent"
                source_label = (
                    "Output" if r["source"] == "task_output" else "Message"
                )
                proj = (
                    f" · project {r['project_id']}"
                    if r.get("project_id") else ""
                )
                lines.append(f"**{source_label} · {agent}{proj} · {day}**")
                lines.append(r["content"][:600].strip())
                lines.append("")
            return "\n".join(lines).rstrip()

        except Exception as exc:
            logger.warning(f"Could not read memory: {exc}")
            return "(Memory service unavailable.)"

    def _scan_output_files(self, days: int) -> str:
        try:
            cutoff_ts = datetime.combine(
                date.today() - timedelta(days=days),
                datetime.min.time(),
            ).timestamp()
            output_dir = self.settings.output_dir
            if not output_dir.exists():
                return "(outputs/ directory not found.)"

            recent: list[str] = []
            for f in sorted(
                output_dir.rglob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ):
                if f.stat().st_mtime >= cutoff_ts:
                    rel = f.relative_to(output_dir)
                    mtime = datetime.fromtimestamp(
                        f.stat().st_mtime
                    ).strftime("%Y-%m-%d %H:%M")
                    recent.append(f"- `outputs/{rel}` (modified {mtime})")

            return (
                "\n".join(recent)
                if recent
                else "(No output files modified during this period.)"
            )
        except Exception as exc:
            logger.warning(f"Could not scan outputs: {exc}")
            return "(Could not scan outputs directory.)"
