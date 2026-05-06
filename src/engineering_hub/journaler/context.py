"""JournalContext: mtime-based incremental scanner for org-roam workspace.

Scans the org-roam directory tree, builds a compressed context snapshot,
and maintains state for incremental scanning.  Designed to be called
every 10 minutes by the scheduler.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
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


def _parse_date_key(key: str) -> date | None:
    """Parse an ISO date string (YYYY-MM-DD) used as a journal_window key."""
    try:
        return date.fromisoformat(key)
    except ValueError:
        return None


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
        pending_tasks_file: Path | None = None,
        conversation_lookback_days: int = 7,
        conversation_summary_excerpt_chars: int = 800,
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
        self._pending_tasks_file = pending_tasks_file
        self.conversation_lookback_days = max(0, conversation_lookback_days)
        self.conversation_summary_excerpt_chars = max(
            200,
            conversation_summary_excerpt_chars,
        )

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
        # journal_window_delta accumulates entries from changed journal files only;
        # merged with the cached window at snapshot time.
        journal_window_delta: dict[str, list[dict[str, str]]] = {}

        for org_file in changed_files:
            if not self._should_parse_org_file(org_file, journal_sel):
                continue
            info = parse_org_file(org_file)
            all_pending.extend(extract_pending_tasks(info.entries))
            all_completed.extend(extract_completed_tasks(info.entries))

            file_date = self._extract_date_from_filename(org_file)

            # today_entries: today + yesterday (backward-compatible)
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

            # journal_window: full lookback window, grouped by date
            if file_date:
                date_key = file_date.isoformat()
                day_entries: list[dict[str, str]] = []
                for entry in info.entries:
                    ts_str = entry.timestamp.strftime("%H:%M") if entry.timestamp else ""
                    day_entries.append({
                        "time": ts_str,
                        "heading": entry.title,
                        "content": entry.body[:200] if entry.body else "",
                        "state": entry.state or "",
                        "tags": ",".join(entry.tags),
                    })
                journal_window_delta[date_key] = day_entries

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

        # Ensure today's journal is always represented in the window, even when unchanged
        today_journal = self.journal_dir / f"{today.isoformat()}.org"
        tj_resolved = today_journal.expanduser().resolve()
        if (
            today_journal.exists()
            and tj_resolved in journal_sel
            and not any(f.resolve() == tj_resolved for f in changed_files)
        ):
            info = parse_org_file(today_journal)
            for task in extract_pending_tasks(info.entries):
                if task not in all_pending:
                    all_pending.append(task)
            for task in extract_completed_tasks(info.entries):
                if task not in all_completed:
                    all_completed.append(task)
            # Add today to window even when not changed, for completeness
            date_key = today.isoformat()
            if date_key not in journal_window_delta:
                day_entries = []
                for entry in info.entries:
                    ts_str = entry.timestamp.strftime("%H:%M") if entry.timestamp else ""
                    day_entries.append({
                        "time": ts_str,
                        "heading": entry.title,
                        "content": entry.body[:200] if entry.body else "",
                        "state": entry.state or "",
                        "tags": ",".join(entry.tags),
                    })
                journal_window_delta[date_key] = day_entries

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

        # Merge the incremental window delta with the cached window, then prune
        # dates outside the lookback window.
        merged_window = dict(self._snapshot.journal_window)
        merged_window.update(journal_window_delta)
        lookback_cutoff = today - timedelta(days=self.journal_lookback_days)
        merged_window = {
            k: v for k, v in merged_window.items()
            if _parse_date_key(k) is not None and _parse_date_key(k) >= lookback_cutoff  # type: ignore[operator]
        }

        # Carry over previous task_first_seen and record any new pending tasks
        task_first_seen = dict(self._snapshot.task_first_seen)
        today_str = today.isoformat()
        new_pending = all_pending or self._snapshot.pending_tasks
        for task in new_pending:
            key = task[:80]
            if key not in task_first_seen:
                task_first_seen[key] = today_str

        # Build derived topic data from the full merged window
        recurring_topics = self._build_recurring_topics(merged_window)
        active_roam_nodes = self._build_active_roam_nodes()
        stale_tasks = self._flag_stale_tasks(new_pending, merged_window, task_first_seen)

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
            pending_tasks=new_pending,
            completed_tasks=all_completed or self._snapshot.completed_tasks,
            recent_project_changes=project_changes or self._snapshot.recent_project_changes,
            recent_agent_outputs=recent_agent_outputs or self._snapshot.recent_agent_outputs,
            active_projects=self._snapshot.active_projects,
            has_significant_changes=has_significant,
            change_summary="; ".join(change_parts) if change_parts else "no changes",
            journal_window=merged_window,
            recurring_topics=recurring_topics,
            active_roam_nodes=active_roam_nodes,
            stale_tasks=stale_tasks,
            task_first_seen=task_first_seen,
        )

        self._state.last_scan = now.isoformat(timespec="seconds")
        self._save_state()
        self._save_cache()

        logger.info(
            f"Scan complete: {len(changed_files)} org files, "
            f"{len(new_outputs)} outputs, "
            f"{len(all_pending)} pending tasks, "
            f"{len(recurring_topics)} recurring topics, "
            f"{len(stale_tasks)} stale tasks"
        )
        return self._snapshot

    def full_window_scan(self) -> ContextSnapshot:
        """Force-reparse all files in the journal lookback window regardless of mtime.

        Intended for the periodic deep-scan schedule (default every 60 min) to
        keep topic digests fresh even when no files have been modified.
        """
        today = date.today()
        journal_sel = self._selected_journal_files(today)

        journal_window: dict[str, list[dict[str, str]]] = {}

        for org_file in journal_sel:
            if not org_file.exists():
                continue
            file_date = self._extract_date_from_filename(org_file)
            if not file_date:
                continue
            info = parse_org_file(org_file)
            date_key = file_date.isoformat()
            day_entries: list[dict[str, str]] = []
            for entry in info.entries:
                ts_str = entry.timestamp.strftime("%H:%M") if entry.timestamp else ""
                day_entries.append({
                    "time": ts_str,
                    "heading": entry.title,
                    "content": entry.body[:200] if entry.body else "",
                    "state": entry.state or "",
                    "tags": ",".join(entry.tags),
                })
            journal_window[date_key] = day_entries
            # Update mtime record so incremental scan won't re-parse unchanged files
            self._state.record(org_file)

        recurring_topics = self._build_recurring_topics(journal_window)
        active_roam_nodes = self._build_active_roam_nodes()
        stale_tasks = self._flag_stale_tasks(
            self._snapshot.pending_tasks, journal_window, self._snapshot.task_first_seen
        )

        self._snapshot = ContextSnapshot(
            last_scan=self._snapshot.last_scan,
            today_date=self._snapshot.today_date,
            today_entries=self._snapshot.today_entries,
            pending_tasks=self._snapshot.pending_tasks,
            completed_tasks=self._snapshot.completed_tasks,
            recent_project_changes=self._snapshot.recent_project_changes,
            recent_agent_outputs=self._snapshot.recent_agent_outputs,
            active_projects=self._snapshot.active_projects,
            has_significant_changes=False,
            change_summary="deep scan refresh",
            journal_window=journal_window,
            recurring_topics=recurring_topics,
            active_roam_nodes=active_roam_nodes,
            stale_tasks=stale_tasks,
            task_first_seen=self._snapshot.task_first_seen,
        )

        self._save_state()
        self._save_cache()
        logger.info(
            f"Deep scan complete: {len(journal_window)} journal days, "
            f"{len(recurring_topics)} recurring topics, "
            f"{len(active_roam_nodes)} active roam nodes"
        )
        return self._snapshot

    def _load_daily_summaries(self, n: int) -> list[dict[str, str]]:
        """Read the last *n* daily conversation summaries from disk.

        Returns a list of ``{"date": "YYYY-MM-DD", "text": "..."}`` dicts
        sorted newest-first.  Falls back to an empty list when the
        ``daily_summaries/`` directory does not exist or files cannot be read.
        """
        summary_dir = self.state_dir / "daily_summaries"
        if not summary_dir.exists():
            return []

        dated: list[tuple[date, Path]] = []
        for p in summary_dir.glob("*.md"):
            stem = p.stem
            try:
                d = date.fromisoformat(stem)
                dated.append((d, p))
            except ValueError:
                continue

        dated.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, str]] = []
        for d, p in dated[:n]:
            try:
                text = p.read_text(encoding="utf-8", errors="replace").strip()
                # Strip the heading line ("# Journaler Daily Summary — YYYY-MM-DD")
                lines = text.splitlines()
                if lines and lines[0].startswith("#"):
                    lines = lines[1:]
                text = "\n".join(lines).strip()
                results.append({"date": d.isoformat(), "text": text})
            except OSError:
                continue
        return results

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

        if s.stale_tasks:
            lines.append("### Possibly Stalled")
            for task in s.stale_tasks[:8]:
                lines.append(f"- [ ] {task}  _(no recent journal mention)_")
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

        # Multi-day journal thread (all lookback days beyond today/yesterday)
        if s.journal_window:
            sorted_dates = sorted(s.journal_window.keys(), reverse=True)
            older_dates = [d for d in sorted_dates if d < date.today().isoformat()]
            if older_dates:
                lines.append(f"### Journal Thread (last {self.journal_lookback_days} days)")
                for date_key in older_dates[:4]:
                    day_entries = s.journal_window[date_key]
                    if not day_entries:
                        continue
                    lines.append(f"**{date_key}**")
                    for entry in day_entries[:5]:
                        state_prefix = f"[{entry['state']}] " if entry.get("state") else ""
                        time_prefix = f"**{entry['time']}** " if entry.get("time") else ""
                        lines.append(f"- {time_prefix}{state_prefix}{entry.get('heading', '')}")
                        if entry.get("content"):
                            lines.append(f"  {entry['content'][:120]}")
                    if len(day_entries) > 5:
                        lines.append(f"  _(+ {len(day_entries) - 5} more entries)_")
                lines.append("")

        if s.recurring_topics:
            lines.append("### Recurring Topics")
            for topic in s.recurring_topics[:10]:
                days = topic.get("days_seen", 1)
                last = topic.get("last_seen", "")
                lines.append(
                    f"- **{topic.get('topic', '')}** "
                    f"_(seen {days}d, last {last})_"
                )
            lines.append("")

        if s.active_roam_nodes:
            lines.append("### Active Project Notes")
            for node in s.active_roam_nodes[:8]:
                tags_str = f" `{node['tags']}`" if node.get("tags") else ""
                lines.append(
                    f"- **{node.get('title', node.get('path_rel', '?'))}**{tags_str}"
                    f" (modified {node.get('modified', '')[:10]})"
                )
                if node.get("top_headings"):
                    lines.append(f"  {node['top_headings']}")
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

        if self.conversation_lookback_days > 0:
            summaries = self._load_daily_summaries(self.conversation_lookback_days)
            if summaries:
                lines.append("### Recent Conversation Summaries")
                lines.append(
                    "_Compressed summaries of past Journaler sessions "
                    "(newest first). Use these to recall prior discussions "
                    "and identify continuing threads._"
                )
                lines.append("")
                max_chars = self.conversation_summary_excerpt_chars
                for entry in summaries:
                    excerpt = entry["text"][:max_chars].replace("\n", " ").strip()
                    if len(entry["text"]) > max_chars:
                        excerpt += "..."
                    lines.append(f"**{entry['date']}**: {excerpt}")
                lines.append("")

        return "\n".join(lines)

    def _resolved_pending_tasks_path(self) -> Path:
        if self._pending_tasks_file is not None:
            return self._pending_tasks_file.expanduser().resolve()
        return (self.workspace_dir / ".journaler" / "pending-tasks.org").resolve()

    def _format_pending_queue_for_briefing(self) -> str:
        """Summarize Journaler queue entries from the last ~36h for briefing context."""
        path = self._resolved_pending_tasks_path()
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        today = date.today()
        yday = today - timedelta(days=1)
        markers = (today.isoformat(), yday.isoformat())
        if not any(m in text for m in markers):
            return ""

        chunks = text.split("** ")
        hits: list[str] = []
        for chunk in chunks[1:]:
            block = "** " + chunk
            if ":STATUS: PENDING" not in block and ":STATUS: DONE" not in block:
                continue
            if not any(m in block for m in markers):
                continue
            first_line = block.splitlines()[0].strip()
            status_m = re.search(r":STATUS:\s*(\S+)", block, re.IGNORECASE)
            st = status_m.group(1) if status_m else "?"
            hits.append(f"- ({st}) {first_line[:120]}")

        if not hits:
            lines_out = [
                "### Journaler overnight queue (pending-tasks.org)",
                f"_Path: `{path}` — recent session markers found; no extractable blocks._",
                "",
            ]
            return "\n".join(lines_out)

        lines_out = [
            "### Journaler overnight queue (pending-tasks.org)",
            f"_Path: `{path}` — entries with timestamps touching {yday} or {today}:_",
        ]
        lines_out.extend(hits[:12])
        if len(hits) > 12:
            lines_out.append(f"_… +{len(hits) - 12} more_")
        lines_out.append(
            "_Compare with “Recent Agent Outputs” below (memory) for completed work._"
        )
        lines_out.append("")

        return "\n".join(lines_out)

    def get_briefing_context(self) -> str:
        """Richer context for morning briefings — includes multi-day journal
        thread, recurring topics, active roam nodes, stale tasks, yesterday's
        full activity, pending items, and unreviewed agent outputs.

        Targeted at ~12 000 tokens to support verbose briefing generation.
        """
        s = self._snapshot
        lines: list[str] = [
            f"## Briefing Context (scanned {s.last_scan or 'never'})",
            f"Date: {s.today_date}",
            "",
        ]

        pq = self._format_pending_queue_for_briefing()
        if pq:
            lines.append(pq)

        # Yesterday's journal: scan yesterday's file directly for full content
        yesterday = date.today() - timedelta(days=1)
        yesterday_file = self.journal_dir / f"{yesterday.isoformat()}.org"
        if yesterday_file.exists():
            info = parse_org_file(yesterday_file, max_body_chars=1500)
            lines.append("### Yesterday's Activity")
            for entry in info.entries:
                state_marker = f" [{entry.state}]" if entry.state else ""
                tags_str = f"  :{':'.join(entry.tags)}:" if entry.tags else ""
                lines.append(f"- {entry.title}{state_marker}{tags_str}")
                if entry.body.strip():
                    lines.append(f"  {entry.body.strip()[:600]}")
            lines.append("")

        # Multi-day journal thread (full lookback window beyond yesterday)
        if s.journal_window:
            sorted_dates = sorted(s.journal_window.keys(), reverse=True)
            older_dates = [
                d for d in sorted_dates
                if d < date.today().isoformat() and d != yesterday.isoformat()
            ]
            if older_dates:
                lines.append(
                    f"### Journal Thread (last {self.journal_lookback_days} days)"
                )
                for date_key in older_dates:
                    day_entries = s.journal_window[date_key]
                    if not day_entries:
                        continue
                    lines.append(f"**{date_key}**")
                    for entry in day_entries[:8]:
                        state_prefix = (
                            f"[{entry['state']}] " if entry.get("state") else ""
                        )
                        time_prefix = (
                            f"**{entry['time']}** " if entry.get("time") else ""
                        )
                        tags_suffix = (
                            f"  :{entry['tags']}:" if entry.get("tags") else ""
                        )
                        lines.append(
                            f"- {time_prefix}{state_prefix}"
                            f"{entry.get('heading', '')}{tags_suffix}"
                        )
                        if entry.get("content"):
                            lines.append(f"  {entry['content'][:200]}")
                    if len(day_entries) > 8:
                        lines.append(
                            f"  _(+ {len(day_entries) - 8} more entries)_"
                        )
                lines.append("")

        # Recurring topics across multiple days
        if s.recurring_topics:
            lines.append("### Recurring Topics")
            for topic in s.recurring_topics[:10]:
                days = topic.get("days_seen", 1)
                count = topic.get("count", 1)
                last = topic.get("last_seen", "")
                lines.append(
                    f"- **{topic.get('topic', '')}** "
                    f"_(seen on {days} days, {count} mentions, last {last})_"
                )
            lines.append("")

        # Continuing threads: semantic match across recent daily summaries
        if self.memory_service and s.recurring_topics:
            try:
                topic_query = " ".join(
                    t.get("topic", "") for t in s.recurring_topics[:5]
                ).strip()
                if topic_query:
                    hits = self.memory_service.search(
                        topic_query,
                        source="journaler",
                        k=5,
                        threshold=0.50,
                    )
                    if hits:
                        lines.append("### Continuing Threads")
                        lines.append(
                            "_Conversations from past sessions that overlap with "
                            "today's recurring topics — threads worth revisiting._"
                        )
                        lines.append("")
                        for hit in hits[:5]:
                            date_str = (hit.created_at or "")[:10]
                            excerpt = hit.content[:300].replace("\n", " ").strip()
                            if len(hit.content) > 300:
                                excerpt += "..."
                            lines.append(
                                f"- **{date_str}** _{hit.similarity:.0%} match_: {excerpt}"
                            )
                        lines.append("")
            except Exception as exc:
                logger.warning("Continuing threads search failed (non-fatal): %s", exc)

        # Stale tasks (pending with no recent journal mention)
        if s.stale_tasks:
            lines.append("### Stalled / Stale Tasks")
            for task in s.stale_tasks:
                first_seen = s.task_first_seen.get(task, "unknown")
                lines.append(f"- [ ] {task}  _(first seen {first_seen})_")
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

        # Active roam nodes (recently modified project notes)
        if s.active_roam_nodes:
            lines.append("### Active Project Notes")
            for node in s.active_roam_nodes[:10]:
                tags_str = f" `{node['tags']}`" if node.get("tags") else ""
                lines.append(
                    f"- **{node.get('title', node.get('path_rel', '?'))}**"
                    f"{tags_str} (modified {node.get('modified', '')[:10]})"
                )
                if node.get("top_headings"):
                    lines.append(f"  Sections: {node['top_headings']}")
            lines.append("")

        # Recent project changes (full detail)
        if s.recent_project_changes:
            lines.append("### Recent Project Changes")
            for change in s.recent_project_changes:
                lines.append(
                    f"- **{change['file']}** (changed {change['changed']})"
                )
                lines.append(f"  {change['summary'][:800]}")
            lines.append("")

        # Agent outputs (full detail)
        if s.recent_agent_outputs:
            lines.append("### Recent Agent Outputs")
            for output in s.recent_agent_outputs[:10]:
                lines.append(
                    f"- @{output.get('agent', '?')} ({output.get('date', '?')}): "
                    f"{output.get('summary', '')[:500]}"
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
    # Topic / node analysis helpers
    # ------------------------------------------------------------------

    def _build_recurring_topics(
        self, window: dict[str, list[dict[str, str]]]
    ) -> list[dict[str, str | int]]:
        """Identify topics that appear on 2+ distinct days in the journal window.

        Returns a list of dicts sorted by days_seen descending, capped at 10.
        """
        # topic_str -> set of date strings where it appeared
        topic_days: dict[str, set[str]] = defaultdict(set)
        # topic_str -> total occurrence count
        topic_count: dict[str, int] = defaultdict(int)

        for date_key, entries in window.items():
            seen_today: set[str] = set()
            for entry in entries:
                heading = (entry.get("heading") or "").strip()
                if not heading:
                    continue
                normalized = heading.lower().rstrip(".")
                # Skip generic catch-all headings that aren't meaningful
                if normalized in {
                    "notes", "tasks", "overnight agent tasks", "log",
                    "meetings", "todo", "done", "agenda",
                }:
                    continue
                topic_count[normalized] += 1
                seen_today.add(normalized)
            for topic in seen_today:
                topic_days[topic].add(date_key)

        results = []
        for topic, days in topic_days.items():
            if len(days) >= 2:
                last_seen = max(days)
                results.append({
                    "topic": topic,
                    "days_seen": len(days),
                    "count": topic_count[topic],
                    "last_seen": last_seen,
                })

        results.sort(key=lambda x: (-x["days_seen"], -x["count"]))  # type: ignore[operator]
        return results[:10]

    def _build_active_roam_nodes(self) -> list[dict[str, str]]:
        """Return recently modified org-roam nodes (excluding daily journals).

        Filters to files with mtime within the lookback window, capped at 12.
        """
        roam_dir = self.org_roam_dir.expanduser().resolve()
        journal_dir = self._resolved_journal_dir()
        if not roam_dir.exists():
            return []

        cutoff_mtime = (
            datetime.now() - timedelta(days=self.journal_lookback_days)
        ).timestamp()

        candidates: list[tuple[float, Path]] = []
        for org_file in roam_dir.rglob("*.org"):
            # Skip files in the daily journals directory
            try:
                org_file.relative_to(journal_dir)
                continue
            except ValueError:
                pass
            try:
                mtime = org_file.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff_mtime:
                candidates.append((mtime, org_file))

        # Sort newest-first, take top 12
        candidates.sort(key=lambda x: x[0], reverse=True)
        nodes: list[dict[str, str]] = []
        for mtime, org_file in candidates[:12]:
            info = parse_org_file(org_file, max_body_chars=100)
            try:
                path_rel = str(org_file.relative_to(roam_dir))
            except ValueError:
                path_rel = str(org_file)
            top_headings = ", ".join(
                e.title for e in info.entries[:2] if e.title
            )
            nodes.append({
                "title": info.title or org_file.stem,
                "tags": " ".join(info.filetags),
                "path_rel": path_rel,
                "modified": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                "top_headings": top_headings,
            })
        return nodes

    def _flag_stale_tasks(
        self,
        pending_tasks: list[str],
        window: dict[str, list[dict[str, str]]],
        task_first_seen: dict[str, str],
        threshold_days: int = 3,
    ) -> list[str]:
        """Return pending tasks that have no recent journal mention and are old enough.

        A task is considered stale when:
        - It first appeared >= threshold_days ago, AND
        - No journal window entry's heading or content contains a 4+-word fragment of it.
        """
        today = date.today()
        cutoff = (today - timedelta(days=threshold_days)).isoformat()

        # Build a flat searchable corpus from all window entries
        all_text = " ".join(
            f"{e.get('heading', '')} {e.get('content', '')}".lower()
            for entries in window.values()
            for e in entries
        )

        stale: list[str] = []
        for task in pending_tasks:
            key = task[:80]
            first_seen = task_first_seen.get(key, today.isoformat())
            if first_seen > cutoff:
                continue  # too recent to flag
            # Check if any significant fragment of the task appears in the window
            words = task.lower().split()
            if len(words) <= 2:
                # Short tasks: require exact substring match
                if task.lower() in all_text:
                    continue
            else:
                # Longer tasks: look for a 3-word contiguous fragment
                found = any(
                    " ".join(words[i:i + 3]) in all_text
                    for i in range(len(words) - 2)
                )
                if found:
                    continue
            stale.append(task)

        return stale[:10]

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
