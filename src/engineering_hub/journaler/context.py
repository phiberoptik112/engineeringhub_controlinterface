"""JournalContext: mtime-based incremental scanner for org-roam workspace.

Scans the org-roam directory tree, builds a compressed context snapshot,
and maintains state for incremental scanning.  Designed to be called
every 10 minutes by the scheduler.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_hub.journaler.models import ContextSnapshot, ScanState
from engineering_hub.journaler.org_parser import (
    extract_completed_tasks,
    extract_pending_tasks,
    parse_org_file,
    summarize_file,
)

if TYPE_CHECKING:
    from engineering_hub.memory.service import MemoryService

logger = logging.getLogger(__name__)


class JournalContext:
    """Scans org-roam workspace and builds a compressed context snapshot.

    Designed to be called every 10 minutes by the scheduler.
    Only reads changed files (mtime-based diff) to keep scan latency low.
    """

    def __init__(
        self,
        org_roam_dir: Path,
        journal_dir: Path,
        workspace_dir: Path,
        memory_service: MemoryService | None,
        state_dir: Path,
        watch_dirs: list[Path] | None = None,
        scan_org_roam_tree: bool = True,
        journal_lookback_days: int = 5,
        journal_max_files: int = 5,
    ) -> None:
        self.org_roam_dir = org_roam_dir
        self.journal_dir = journal_dir
        self.workspace_dir = workspace_dir
        self.memory_service = memory_service
        self.state_dir = state_dir
        self.watch_dirs = watch_dirs or []
        self.scan_org_roam_tree = scan_org_roam_tree
        self.journal_lookback_days = max(0, journal_lookback_days)
        self.journal_max_files = max(1, journal_max_files)

        self.state_file = state_dir / "state.json"
        self.cache_file = state_dir / "context_cache.json"

        self._state = self._load_state()
        self._snapshot = self._load_cache()

    def _resolved_journal_dir(self) -> Path:
        return self.journal_dir.expanduser().resolve()

    def _selected_journal_files(self, today: date) -> set[Path]:
        """Daily journal *.org paths to track and parse (lookback + cap).

        Always includes today's file when it exists.
        """
        jd = self._resolved_journal_dir()
        if not jd.exists():
            return set()

        dated: list[tuple[date, Path]] = []
        for p in jd.glob("*.org"):
            d = self._extract_date_from_filename(p)
            if d is not None:
                dated.append((d, p.resolve()))

        dated.sort(key=lambda x: x[0], reverse=True)
        if not dated:
            out: set[Path] = set()
            today_p = jd / f"{today.isoformat()}.org"
            if today_p.exists():
                out.add(today_p.resolve())
            return out

        lookback = today - timedelta(days=self.journal_lookback_days)
        W = [(d, p) for d, p in dated if d >= lookback]
        max_f = self.journal_max_files
        if len(W) >= max_f:
            selected = [p for _, p in W[:max_f]]
        else:
            selected = [p for _, p in dated[:max_f]]

        out = set(selected)
        today_p = jd / f"{today.isoformat()}.org"
        if today_p.exists():
            out.add(today_p.resolve())
        return out

    def _should_parse_org_file(self, org_file: Path, journal_sel: set[Path]) -> bool:
        """Skip daily journals outside the selected recent window."""
        try:
            r = org_file.resolve()
        except OSError:
            return False
        jd = self._resolved_journal_dir()
        if not jd.exists():
            return True
        try:
            r.relative_to(jd)
        except ValueError:
            return True
        return r in journal_sel

    def _append_changed_org(
        self, org_file: Path, changed_files: list[Path], seen: set[str]
    ) -> None:
        key = str(org_file.resolve())
        if key in seen:
            return
        if self._state.is_changed(org_file):
            changed_files.append(org_file)
            self._state.record(org_file)
            seen.add(key)

    def _prune_journal_state(self, journal_sel: set[Path]) -> None:
        """Drop mtime entries for daily files outside the current journal window."""
        try:
            jd = self._resolved_journal_dir()
        except OSError:
            return
        if not jd.exists():
            return
        keep = {p.resolve() for p in journal_sel}
        new_mtimes: dict[str, float] = {}
        for key, mtime in self._state.file_mtimes.items():
            p = Path(key)
            try:
                rp = p.resolve()
            except OSError:
                continue
            try:
                rp.relative_to(jd)
            except ValueError:
                new_mtimes[key] = mtime
                continue
            if rp in keep:
                new_mtimes[key] = mtime
        self._state.file_mtimes = new_mtimes

    def scan(self) -> ContextSnapshot:
        """Incremental scan. Reads only changed files since last scan.

        Updates state.json and context_cache.json. Returns the new snapshot.
        """
        now = datetime.now()
        today = date.today()
        journal_sel = self._selected_journal_files(today)
        self._prune_journal_state(journal_sel)

        changed_files: list[Path] = []
        seen_keys: set[str] = set()

        if self.scan_org_roam_tree:
            scan_dirs = [self.org_roam_dir] + self.watch_dirs
            for scan_dir in scan_dirs:
                if not scan_dir.exists():
                    continue
                for org_file in scan_dir.rglob("*.org"):
                    self._append_changed_org(org_file, changed_files, seen_keys)
        else:
            for org_file in journal_sel:
                if org_file.exists():
                    self._append_changed_org(org_file, changed_files, seen_keys)
            for scan_dir in self.watch_dirs:
                if not scan_dir.exists():
                    continue
                for org_file in scan_dir.rglob("*.org"):
                    self._append_changed_org(org_file, changed_files, seen_keys)

        # Also check workspace outputs directory
        outputs_dir = self.workspace_dir / "outputs"
        new_outputs: list[Path] = []
        if outputs_dir.exists():
            for output_file in outputs_dir.rglob("*.md"):
                if self._state.is_changed(output_file):
                    new_outputs.append(output_file)
                    self._state.record(output_file)

        # Parse changed org files
        all_pending: list[str] = []
        all_completed: list[str] = []
        today_entries: list[dict[str, str]] = []
        project_changes: list[dict[str, str]] = []

        for org_file in changed_files:
            if not self._should_parse_org_file(org_file, journal_sel):
                continue
            info = parse_org_file(org_file)
            all_pending.extend(extract_pending_tasks(info.entries))
            all_completed.extend(extract_completed_tasks(info.entries))

            # Journal headings for today / yesterday only (within selected files)
            file_date = self._extract_date_from_filename(org_file)
            if file_date and file_date >= today - timedelta(days=1):
                for entry in info.entries:
                    ts_str = ""
                    if entry.timestamp:
                        ts_str = entry.timestamp.strftime("%H:%M")
                    today_entries.append({
                        "time": ts_str,
                        "heading": entry.title,
                        "content": entry.body[:300] if entry.body else "",
                    })

            # Track project note changes
            summary = summarize_file(info, max_chars=300)
            if summary:
                project_changes.append({
                    "file": str(org_file.relative_to(self.org_roam_dir))
                    if self._is_under(org_file, self.org_roam_dir)
                    else str(org_file),
                    "changed": now.isoformat(timespec="seconds"),
                    "summary": summary,
                })

        # Also scan today's journal even if it didn't change (for completeness on first run)
        today_journal = self.journal_dir / f"{today.isoformat()}.org"
        tj_resolved = today_journal.expanduser().resolve()
        if (
            today_journal.exists()
            and tj_resolved in journal_sel
            and not any(f.resolve() == tj_resolved for f in changed_files)
        ):
            info = parse_org_file(today_journal)
            # Merge pending/completed from today's journal if not already captured
            for task in extract_pending_tasks(info.entries):
                if task not in all_pending:
                    all_pending.append(task)
            for task in extract_completed_tasks(info.entries):
                if task not in all_completed:
                    all_completed.append(task)

        # Fetch recent agent outputs from memory
        recent_agent_outputs: list[dict[str, str]] = []
        if self.memory_service:
            try:
                recent = self.memory_service.browse_recent(limit=20, source="task_output")
                for mem in recent:
                    recent_agent_outputs.append({
                        "agent": mem.get("agent", "unknown"),
                        "date": (mem.get("created_at") or "")[:10],
                        "summary": (mem.get("content") or "")[:200],
                    })
            except Exception as exc:
                logger.warning(f"Memory browse failed during scan (non-fatal): {exc}")

        # Detect significance
        has_significant = bool(changed_files) or bool(new_outputs)
        change_parts: list[str] = []
        if changed_files:
            change_parts.append(f"{len(changed_files)} org files changed")
        if new_outputs:
            change_parts.append(f"{len(new_outputs)} new outputs")

        # Merge into snapshot (keep previous data for unchanged items)
        self._snapshot = ContextSnapshot(
            last_scan=now.isoformat(timespec="seconds"),
            today_date=today.isoformat(),
            today_entries=today_entries or self._snapshot.today_entries,
            pending_tasks=all_pending or self._snapshot.pending_tasks,
            completed_tasks=all_completed or self._snapshot.completed_tasks,
            recent_project_changes=project_changes or self._snapshot.recent_project_changes,
            recent_agent_outputs=recent_agent_outputs or self._snapshot.recent_agent_outputs,
            active_projects=self._snapshot.active_projects,
            has_significant_changes=has_significant,
            change_summary="; ".join(change_parts) if change_parts else "no changes",
        )

        self._state.last_scan = now.isoformat(timespec="seconds")
        self._save_state()
        self._save_cache()

        logger.info(
            f"Scan complete: {len(changed_files)} org files, "
            f"{len(new_outputs)} outputs, "
            f"{len(all_pending)} pending tasks"
        )
        return self._snapshot

    def get_current_context(self) -> str:
        """Format the cached snapshot as a markdown context block
        suitable for injection into the model's system prompt.

        Targeted at ~4000 tokens.
        """
        s = self._snapshot
        lines: list[str] = [
            f"## Current Context (updated {s.last_scan or 'never'})",
            "",
        ]

        if s.pending_tasks:
            lines.append("### Pending Tasks")
            for task in s.pending_tasks[:15]:
                lines.append(f"- [ ] {task}")
            if len(s.pending_tasks) > 15:
                lines.append(f"  _(+ {len(s.pending_tasks) - 15} more)_")
            lines.append("")

        if s.completed_tasks:
            lines.append("### Recently Completed")
            for task in s.completed_tasks[:10]:
                lines.append(f"- [x] {task}")
            lines.append("")

        if s.today_entries:
            lines.append("### Today's Journal Entries")
            for entry in s.today_entries[:10]:
                time_prefix = f"**{entry['time']}** " if entry.get("time") else ""
                lines.append(f"- {time_prefix}{entry.get('heading', '')}")
                if entry.get("content"):
                    lines.append(f"  {entry['content'][:150]}")
            lines.append("")

        if s.recent_project_changes:
            lines.append("### Recent Project Changes")
            for change in s.recent_project_changes[:8]:
                lines.append(f"- **{change['file']}** ({change['changed'][:10]})")
                lines.append(f"  {change['summary'][:150]}")
            lines.append("")

        if s.recent_agent_outputs:
            lines.append("### Recent Agent Outputs")
            for output in s.recent_agent_outputs[:5]:
                lines.append(
                    f"- @{output.get('agent', '?')} ({output.get('date', '?')}): "
                    f"{output.get('summary', '')[:120]}"
                )
            lines.append("")

        return "\n".join(lines)

    def get_briefing_context(self) -> str:
        """Richer context for morning briefings — includes yesterday's full
        activity, pending items across all projects, and unreviewed agent outputs.

        Targeted at ~8000 tokens.
        """
        s = self._snapshot
        lines: list[str] = [
            f"## Briefing Context (scanned {s.last_scan or 'never'})",
            f"Date: {s.today_date}",
            "",
        ]

        # Yesterday's journal: scan yesterday's file directly for full content
        yesterday = date.today() - timedelta(days=1)
        yesterday_file = self.journal_dir / f"{yesterday.isoformat()}.org"
        if yesterday_file.exists():
            info = parse_org_file(yesterday_file, max_body_chars=1000)
            lines.append("### Yesterday's Activity")
            for entry in info.entries:
                state_marker = f" [{entry.state}]" if entry.state else ""
                lines.append(f"- {entry.title}{state_marker}")
                if entry.body.strip():
                    lines.append(f"  {entry.body.strip()[:300]}")
            lines.append("")

        # All pending tasks
        if s.pending_tasks:
            lines.append("### All Pending Tasks")
            for task in s.pending_tasks:
                lines.append(f"- [ ] {task}")
            lines.append("")

        # Completed tasks
        if s.completed_tasks:
            lines.append("### Recently Completed Tasks")
            for task in s.completed_tasks:
                lines.append(f"- [x] {task}")
            lines.append("")

        # Recent project changes (full detail)
        if s.recent_project_changes:
            lines.append("### Recent Project Changes")
            for change in s.recent_project_changes:
                lines.append(f"- **{change['file']}** (changed {change['changed']})")
                lines.append(f"  {change['summary'][:500]}")
            lines.append("")

        # Agent outputs (full detail)
        if s.recent_agent_outputs:
            lines.append("### Recent Agent Outputs")
            for output in s.recent_agent_outputs[:10]:
                lines.append(
                    f"- @{output.get('agent', '?')} ({output.get('date', '?')}): "
                    f"{output.get('summary', '')[:300]}"
                )
            lines.append("")

        # Memory stats
        if self.memory_service:
            try:
                stats = self.memory_service.get_stats()
                if stats:
                    lines.append("### Memory Stats")
                    lines.append(f"- Total memories: {stats.get('total', 0)}")
                    lines.append(
                        f"- Recent (7 days): {stats.get('recent_7d', 'unknown')}"
                        if "recent_7d" in stats
                        else f"- Sources: {stats.get('by_source', {})}"
                    )
                    lines.append("")
            except Exception:
                pass

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> ScanState:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return ScanState(
                    last_scan=data.get("last_scan", ""),
                    file_mtimes=data.get("file_mtimes", {}),
                )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Could not load scan state (starting fresh): {exc}")
        return ScanState()

    def _save_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "last_scan": self._state.last_scan,
            "file_mtimes": self._state.file_mtimes,
        }
        self.state_file.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    def _load_cache(self) -> ContextSnapshot:
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                return ContextSnapshot.from_dict(data)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Could not load context cache (starting fresh): {exc}")
        return ContextSnapshot()

    def _save_cache(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(
            json.dumps(self._snapshot.to_dict(), indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_date_from_filename(path: Path) -> date | None:
        """Try to parse YYYY-MM-DD from an org filename."""
        stem = path.stem
        try:
            return date.fromisoformat(stem)
        except ValueError:
            return None

    @staticmethod
    def _is_under(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
