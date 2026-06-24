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
from typing import TYPE_CHECKING, Literal

from engineering_hub.journaler.models import (
    ContextSnapshot,
    OrgFileInfo,
    ScanState,
    TaskStatusChange,
    TrackedTask,
)
from engineering_hub.journaler.org_parser import (
    extract_tasks_with_provenance,
    extract_topic_keywords,
    parse_org_file,
    parsed_lines_to_tracked_tasks,
    summarize_file,
)
from engineering_hub.journaler.task_resolution import (
    derive_task_lists,
    detect_prose_completions,
    merge_tracked_tasks,
    normalize_task_key,
    text_mentions_task,
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
        journal_lookback_days: int = 30,
        journal_max_files: int = 30,
        pending_tasks_file: Path | None = None,
        conversation_lookback_days: int = 7,
        conversation_summary_excerpt_chars: int = 800,
        roam_task_lookback_days: int = 14,
        roam_task_max_files: int = 30,
        prose_completion_detection: bool = True,
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
        self.roam_task_lookback_days = max(0, roam_task_lookback_days)
        self.roam_task_max_files = max(1, roam_task_max_files)
        self.prose_completion_detection = prose_completion_detection

        self.state_file = state_dir / "state.json"
        self.cache_file = state_dir / "context_cache.json"
        self.file_text_cache_path = state_dir / "file_text_cache.json"

        self._state = self._load_state()
        self._state.normalize_legacy_keys()
        self._snapshot = self._load_cache()
        self._file_text_cache = self._load_file_text_cache()

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

    def _resolved_pending_tasks_path(self) -> Path:
        if self._pending_tasks_file is not None:
            return self._pending_tasks_file.expanduser().resolve()
        return (self.workspace_dir / ".journaler" / "pending-tasks.org").resolve()

    def _rel_source_path(self, path: Path) -> str:
        resolved = path.expanduser().resolve()
        for root in (
            self.org_roam_dir.expanduser().resolve(),
            self.workspace_dir.expanduser().resolve(),
        ):
            try:
                return str(resolved.relative_to(root))
            except ValueError:
                continue
        return resolved.name

    def _roam_task_files(self, today: date) -> list[Path]:
        """Recently modified org-roam notes (excluding daily journals)."""
        if not self.scan_org_roam_tree:
            return []
        roam_dir = self.org_roam_dir.expanduser().resolve()
        journal_dir = self._resolved_journal_dir()
        cutoff = today - timedelta(days=self.roam_task_lookback_days)
        cutoff_mtime = datetime.combine(cutoff, datetime.min.time()).timestamp()

        candidates: list[tuple[float, Path]] = []
        for org_file in roam_dir.rglob("*.org"):
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
                candidates.append((mtime, org_file.resolve()))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [path for _, path in candidates[: self.roam_task_max_files]]

    def _rebuild_task_registry(self, today: date) -> list[TrackedTask]:
        """Full-window task aggregation from journals, queue, and roam notes."""
        records: list[TrackedTask] = []
        journal_sel = self._selected_journal_files(today)

        for org_file in journal_sel:
            if not org_file.exists():
                continue
            file_date = self._extract_date_from_filename(org_file)
            date_str = file_date.isoformat() if file_date else today.isoformat()
            rel = self._rel_source_path(org_file)
            lines = extract_tasks_with_provenance(org_file)
            records.extend(
                parsed_lines_to_tracked_tasks(
                    lines, source_path=rel, source_date=date_str
                )
            )

        pending_path = self._resolved_pending_tasks_path()
        if pending_path.is_file():
            rel = self._rel_source_path(pending_path)
            lines = extract_tasks_with_provenance(pending_path)
            records.extend(
                parsed_lines_to_tracked_tasks(
                    lines, source_path=rel, source_date=today.isoformat()
                )
            )

        for org_file in self._roam_task_files(today):
            if not org_file.exists():
                continue
            rel = self._rel_source_path(org_file)
            lines = extract_tasks_with_provenance(org_file)
            file_date = self._extract_date_from_filename(org_file)
            if file_date:
                date_str = file_date.isoformat()
            else:
                try:
                    mtime = org_file.stat().st_mtime
                    date_str = datetime.fromtimestamp(mtime).date().isoformat()
                except OSError:
                    date_str = today.isoformat()
            records.extend(
                parsed_lines_to_tracked_tasks(
                    lines, source_path=rel, source_date=date_str
                )
            )

        merged = merge_tracked_tasks(records)
        for task in merged:
            stored = self._snapshot.task_first_seen.get(task.task_key)
            if stored and stored < task.first_seen:
                task.first_seen = stored
        return merged

    def _apply_prose_completions(
        self,
        tracked: list[TrackedTask],
        content_changed: list[Path],
        prose_old_texts: dict[str, str] | None = None,
    ) -> list[TrackedTask]:
        if not self.prose_completion_detection or not content_changed:
            return tracked

        old_texts = prose_old_texts or {}
        by_key = {t.task_key: t for t in tracked}
        pending = [t for t in tracked if t.status == "pending"]
        today_str = date.today().isoformat()

        for path in content_changed:
            key = ScanState.path_key(path)
            try:
                new_text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            old_text = old_texts.get(key, self._file_text_cache.get(key, ""))
            for hit in detect_prose_completions(
                pending, new_text=new_text, old_text=old_text
            ):
                existing = by_key.get(hit.task_key)
                if existing is None or existing.status != "pending":
                    continue
                by_key[hit.task_key] = TrackedTask(
                    text=existing.text,
                    status="completed",
                    source_path=existing.source_path,
                    source_date=existing.source_date,
                    source_heading=existing.source_heading,
                    line_hint=existing.line_hint,
                    task_key=existing.task_key,
                    completion_kind="prose",
                    first_seen=existing.first_seen,
                    last_seen=today_str,
                )
            self._file_text_cache[key] = new_text

        return list(by_key.values())

    def completed_task_keys(self) -> set[str]:
        """Normalized keys for tasks currently marked completed."""
        return {
            t.task_key
            for t in self._snapshot.tracked_tasks
            if t.status == "completed"
        }

    def _compute_task_status_changes(
        self,
        old_tasks: list[TrackedTask],
        new_tasks: list[TrackedTask],
    ) -> list[TaskStatusChange]:
        old_by_key = {t.task_key: t for t in old_tasks}
        changes: list[TaskStatusChange] = []
        for task in new_tasks:
            prior = old_by_key.get(task.task_key)
            if prior is None or prior.status != "pending" or task.status != "completed":
                continue
            changes.append(
                TaskStatusChange(
                    task_key=task.task_key,
                    text=task.text,
                    source_path=task.source_path,
                    source_date=task.source_date,
                    completion_kind=task.completion_kind,
                    detail=f"Resolved via {task.completion_kind}",
                )
            )
        return changes

    def _refresh_file_text_cache(self, today: date) -> None:
        """Snapshot raw org text for prose diff detection on the next scan."""
        for org_file in self._selected_journal_files(today):
            if org_file.exists():
                self._update_file_text_cache(org_file)
        pending_path = self._resolved_pending_tasks_path()
        if pending_path.is_file():
            self._update_file_text_cache(pending_path)
        for org_file in self._roam_task_files(today):
            if org_file.exists():
                self._update_file_text_cache(org_file)

    def _update_file_text_cache(self, path: Path) -> None:
        key = ScanState.path_key(path)
        try:
            self._file_text_cache[key] = path.read_text(encoding="utf-8")
        except OSError:
            pass

    def _load_file_text_cache(self) -> dict[str, str]:
        if not self.file_text_cache_path.exists():
            return {}
        try:
            data = json.loads(self.file_text_cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load file text cache (starting fresh): %s", exc)
        return {}

    def _save_file_text_cache(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.file_text_cache_path.write_text(
            json.dumps(self._file_text_cache, indent=2),
            encoding="utf-8",
        )

    def rebuild_tasks(self) -> ContextSnapshot:
        """Force a full task registry rebuild from all journal/roam sources."""
        today = date.today()
        old_tracked = list(self._snapshot.tracked_tasks)
        tracked = self._rebuild_task_registry(today)
        tracked = self._apply_prose_completions(tracked, [])
        pending, completed, first_seen = derive_task_lists(tracked)
        stale = self._flag_stale_tasks_from_tracked(
            tracked, self._snapshot.journal_window
        )
        status_changes = self._compute_task_status_changes(old_tracked, tracked)
        self._snapshot = ContextSnapshot(
            last_scan=self._snapshot.last_scan,
            today_date=self._snapshot.today_date,
            today_entries=self._snapshot.today_entries,
            pending_tasks=pending,
            completed_tasks=completed,
            recent_project_changes=self._snapshot.recent_project_changes,
            recent_agent_outputs=self._snapshot.recent_agent_outputs,
            active_projects=self._snapshot.active_projects,
            has_significant_changes=self._snapshot.has_significant_changes,
            change_summary=self._snapshot.change_summary,
            journal_window=self._snapshot.journal_window,
            recurring_topics=self._snapshot.recurring_topics,
            active_roam_nodes=self._snapshot.active_roam_nodes,
            stale_tasks=stale,
            task_first_seen=first_seen,
            tracked_tasks=tracked,
            task_status_changes=status_changes,
        )
        self._save_cache()
        self._save_file_text_cache()
        return self._snapshot

    def _classify_org_file(
        self, org_file: Path, journal_sel: set[Path]
    ) -> Literal["journal", "roam", "pending"]:
        pending_path = self._resolved_pending_tasks_path()
        try:
            if org_file.resolve() == pending_path:
                return "pending"
        except OSError:
            pass
        try:
            r = org_file.resolve()
        except OSError:
            return "roam"
        jd = self._resolved_journal_dir()
        if jd.exists():
            try:
                r.relative_to(jd)
                if r in journal_sel:
                    return "journal"
                return "roam"
            except ValueError:
                pass
        return "roam"

    def _entry_signature(self, entry: dict[str, str]) -> tuple[str, str]:
        return (entry.get("time", ""), entry.get("heading", ""))

    def _diff_journal_entries(
        self,
        old_entries: list[dict[str, str]],
        new_entries: list[dict[str, str]],
    ) -> list[str]:
        """Return labels for journal entries present in new but not old."""
        old_sigs = {self._entry_signature(e) for e in old_entries if e.get("heading")}
        added: list[str] = []
        for entry in new_entries:
            if not entry.get("heading"):
                continue
            sig = self._entry_signature(entry)
            if sig not in old_sigs:
                added.append(entry["heading"])
        return added

    def _build_change_summary(
        self,
        *,
        journal_diffs: list[tuple[str, list[str]]],
        new_outputs: list[Path],
        content_changed_count: int,
        mtime_only_count: int,
        unchanged_count: int,
        roam_content_count: int,
        checked_count: int,
    ) -> str:
        parts: list[str] = []
        for filename, headings in journal_diffs:
            if not headings:
                parts.append(f"journal {filename}: updated")
            elif len(headings) == 1:
                parts.append(f"journal {filename}: +1 entry ({headings[0]})")
            else:
                preview = ", ".join(headings[:3])
                if len(headings) > 3:
                    preview += f", +{len(headings) - 3} more"
                parts.append(
                    f"journal {filename}: +{len(headings)} entries ({preview})"
                )
        if new_outputs:
            parts.append(
                f"{len(new_outputs)} output"
                f"{'' if len(new_outputs) == 1 else 's'} changed"
            )
        if parts:
            return "; ".join(parts)
        return (
            f"no content changes ({content_changed_count} significant; "
            f"{mtime_only_count} mtime-only; {unchanged_count} unchanged; "
            f"{checked_count} checked"
            + (f"; {roam_content_count} roam updated" if roam_content_count else "")
            + ")"
        )

    def _merge_recent_project_changes(
        self,
        new_changes: list[dict[str, str]],
        *,
        cap: int = 12,
    ) -> list[dict[str, str]]:
        merged = list(self._snapshot.recent_project_changes)
        for change in new_changes:
            file_key = change["file"]
            merged = [c for c in merged if c.get("file") != file_key]
            merged.insert(0, change)
        return merged[:cap]

    def _track_org_file(
        self,
        org_file: Path,
        *,
        seen: set[str],
        content_changed: list[Path],
    ) -> Literal["unchanged", "mtime_only", "content_changed", "duplicate"]:
        key = ScanState.path_key(org_file)
        if key in seen:
            return "duplicate"
        seen.add(key)

        status, file_hash = self._state.inspect(org_file)
        if status == "unchanged":
            return "unchanged"
        if status == "mtime_only":
            self._state.record(org_file, file_hash=file_hash)
            return "mtime_only"

        content_changed.append(org_file)
        self._state.record(org_file, file_hash=file_hash)
        return "content_changed"

    def _journal_window_entries(self, info: OrgFileInfo) -> list[dict[str, str]]:
        """Return daily journal entries plus file-level topic signals."""
        topic_signals = "|".join(extract_topic_keywords(info)[:40])
        day_entries: list[dict[str, str]] = []

        for idx, entry in enumerate(info.entries):
            ts_str = entry.timestamp.strftime("%H:%M") if entry.timestamp else ""
            row = {
                "time": ts_str,
                "heading": entry.title,
                "content": entry.body[:300] if entry.body else "",
                "state": entry.state or "",
                "tags": ",".join(entry.tags),
            }
            if idx == 0 and topic_signals:
                row["keywords"] = topic_signals
            day_entries.append(row)

        if not day_entries and topic_signals:
            day_entries.append({
                "time": "",
                "heading": "",
                "content": "",
                "state": "",
                "tags": "",
                "keywords": topic_signals,
            })

        return day_entries

    def _prune_journal_state(self, journal_sel: set[Path]) -> None:
        """Drop scan state for daily files outside the current journal window."""
        try:
            jd = self._resolved_journal_dir()
        except OSError:
            return
        if not jd.exists():
            return
        keep = {p.resolve() for p in journal_sel}

        def _prune_dict(store: dict[str, object]) -> dict[str, object]:
            pruned: dict[str, object] = {}
            for key, value in store.items():
                p = Path(key)
                try:
                    rp = p.resolve()
                except OSError:
                    continue
                try:
                    rp.relative_to(jd)
                except ValueError:
                    pruned[key] = value
                    continue
                if rp in keep:
                    pruned[key] = value
            return pruned

        self._state.file_mtimes = {
            k: float(v) for k, v in _prune_dict(self._state.file_mtimes).items()
        }
        self._state.file_hashes = {
            k: str(v) for k, v in _prune_dict(self._state.file_hashes).items()
        }

    def scan(self) -> ContextSnapshot:
        """Incremental scan using content hashes and journal-centric significance.

        Updates state.json and context_cache.json. Returns the new snapshot.
        """
        now = datetime.now()
        today = date.today()
        journal_sel = self._selected_journal_files(today)
        self._prune_journal_state(journal_sel)

        content_changed: list[Path] = []
        seen_keys: set[str] = set()
        unchanged_count = 0
        mtime_only_count = 0
        roam_content_count = 0

        def _walk_org(org_file: Path) -> None:
            nonlocal unchanged_count, mtime_only_count
            result = self._track_org_file(
                org_file, seen=seen_keys, content_changed=content_changed
            )
            if result == "duplicate":
                return
            if result == "unchanged":
                unchanged_count += 1
            elif result == "mtime_only":
                mtime_only_count += 1

        pending_path = self._resolved_pending_tasks_path()
        if pending_path.is_file():
            _walk_org(pending_path)

        if self.scan_org_roam_tree:
            scan_dirs = [self.org_roam_dir] + self.watch_dirs
            for scan_dir in scan_dirs:
                if not scan_dir.exists():
                    continue
                for org_file in scan_dir.rglob("*.org"):
                    _walk_org(org_file)
        else:
            for org_file in journal_sel:
                if org_file.exists():
                    _walk_org(org_file)
            for scan_dir in self.watch_dirs:
                if not scan_dir.exists():
                    continue
                for org_file in scan_dir.rglob("*.org"):
                    _walk_org(org_file)

        outputs_dir = self.workspace_dir / "outputs"
        new_outputs: list[Path] = []
        if outputs_dir.exists():
            for output_file in outputs_dir.rglob("*.md"):
                key = ScanState.path_key(output_file)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                status, file_hash = self._state.inspect(output_file)
                if status == "unchanged":
                    unchanged_count += 1
                    continue
                if status == "mtime_only":
                    mtime_only_count += 1
                    self._state.record(output_file, file_hash=file_hash)
                    continue
                new_outputs.append(output_file)
                self._state.record(output_file, file_hash=file_hash)

        today_entries: list[dict[str, str]] = []
        journal_project_changes: list[dict[str, str]] = []
        journal_window_delta: dict[str, list[dict[str, str]]] = {}
        journal_diffs: list[tuple[str, list[str]]] = []
        significant_journal_changes = 0
        task_source_changed: list[Path] = []
        roam_task_paths = {p.resolve() for p in self._roam_task_files(today)}

        for org_file in content_changed:
            file_kind = self._classify_org_file(org_file, journal_sel)
            if file_kind == "roam":
                roam_content_count += 1
                if org_file.resolve() in roam_task_paths:
                    task_source_changed.append(org_file)
                continue

            if file_kind == "journal" and not self._should_parse_org_file(
                org_file, journal_sel
            ):
                continue

            info = parse_org_file(org_file)
            task_source_changed.append(org_file)
            significant_journal_changes += 1

            file_date = self._extract_date_from_filename(org_file)
            rel_file = (
                str(org_file.relative_to(self.org_roam_dir))
                if self._is_under(org_file, self.org_roam_dir)
                else org_file.name
            )

            if file_kind == "journal" and file_date:
                date_key = file_date.isoformat()
                old_entries = self._snapshot.journal_window.get(date_key, [])
                new_entries = self._journal_window_entries(info)
                added = self._diff_journal_entries(old_entries, new_entries)
                if added:
                    journal_diffs.append((org_file.name, added))

                journal_window_delta[date_key] = new_entries

                if file_date >= today - timedelta(days=1):
                    for entry in info.entries:
                        ts_str = ""
                        if entry.timestamp:
                            ts_str = entry.timestamp.strftime("%H:%M")
                        today_entries.append({
                            "time": ts_str,
                            "heading": entry.title,
                            "content": entry.body[:300] if entry.body else "",
                        })

                summary = summarize_file(info, max_chars=300)
                if summary:
                    journal_project_changes.append({
                        "file": rel_file,
                        "changed": now.isoformat(timespec="seconds"),
                        "summary": summary,
                    })
            elif file_kind == "pending":
                journal_diffs.append((org_file.name, ["pending queue updated"]))
                summary = summarize_file(info, max_chars=300)
                if summary:
                    journal_project_changes.append({
                        "file": rel_file,
                        "changed": now.isoformat(timespec="seconds"),
                        "summary": summary,
                    })

        today_journal = self.journal_dir / f"{today.isoformat()}.org"
        tj_resolved = today_journal.expanduser().resolve()
        content_changed_keys = {ScanState.path_key(f) for f in content_changed}
        if (
            today_journal.exists()
            and tj_resolved in journal_sel
            and ScanState.path_key(today_journal) not in content_changed_keys
        ):
            info = parse_org_file(today_journal)
            date_key = today.isoformat()
            if date_key not in journal_window_delta:
                journal_window_delta[date_key] = self._journal_window_entries(info)

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

        merged_window = dict(self._snapshot.journal_window)
        merged_window.update(journal_window_delta)
        lookback_cutoff = today - timedelta(days=self.journal_lookback_days)
        merged_window = {
            k: v for k, v in merged_window.items()
            if _parse_date_key(k) is not None and _parse_date_key(k) >= lookback_cutoff  # type: ignore[operator]
        }

        prose_targets = list(task_source_changed)
        pending_path = self._resolved_pending_tasks_path()
        if (
            pending_path.is_file()
            and ScanState.path_key(pending_path) in content_changed_keys
            and pending_path not in prose_targets
        ):
            prose_targets.append(pending_path)

        old_tracked = list(self._snapshot.tracked_tasks)
        prose_old_texts = {
            ScanState.path_key(path): self._file_text_cache.get(
                ScanState.path_key(path), ""
            )
            for path in prose_targets
        }
        tracked = self._rebuild_task_registry(today)
        tracked = self._apply_prose_completions(
            tracked, prose_targets, prose_old_texts
        )
        pending_tasks, completed_tasks, task_first_seen = derive_task_lists(tracked)

        recurring_topics = self._build_recurring_topics(merged_window)
        active_roam_nodes = self._build_active_roam_nodes()
        stale_tasks = self._flag_stale_tasks_from_tracked(tracked, merged_window)
        task_status_changes = self._compute_task_status_changes(old_tracked, tracked)

        checked_count = (
            len(content_changed) + mtime_only_count + unchanged_count + len(new_outputs)
        )
        content_changed_count = significant_journal_changes + len(new_outputs)
        has_significant = bool(significant_journal_changes) or bool(new_outputs)
        change_summary = self._build_change_summary(
            journal_diffs=journal_diffs,
            new_outputs=new_outputs,
            content_changed_count=content_changed_count,
            mtime_only_count=mtime_only_count,
            unchanged_count=unchanged_count,
            roam_content_count=roam_content_count,
            checked_count=checked_count,
        )

        merged_project_changes = self._merge_recent_project_changes(
            journal_project_changes
        )

        self._snapshot = ContextSnapshot(
            last_scan=now.isoformat(timespec="seconds"),
            today_date=today.isoformat(),
            today_entries=today_entries or self._snapshot.today_entries,
            pending_tasks=pending_tasks,
            completed_tasks=completed_tasks,
            recent_project_changes=merged_project_changes,
            recent_agent_outputs=recent_agent_outputs or self._snapshot.recent_agent_outputs,
            active_projects=self._snapshot.active_projects,
            has_significant_changes=has_significant,
            change_summary=change_summary,
            journal_window=merged_window,
            recurring_topics=recurring_topics,
            active_roam_nodes=active_roam_nodes,
            stale_tasks=stale_tasks,
            task_first_seen=task_first_seen,
            tracked_tasks=tracked,
            task_status_changes=task_status_changes,
        )

        self._state.last_scan = now.isoformat(timespec="seconds")
        self._refresh_file_text_cache(today)
        self._save_state()
        self._save_cache()
        self._save_file_text_cache()

        if has_significant:
            log_detail = change_summary
        else:
            log_detail = (
                f"no content changes "
                f"({checked_count} checked, "
                f"{mtime_only_count} mtime-only, {unchanged_count} unchanged"
                + (f", {roam_content_count} roam updated" if roam_content_count else "")
                + ")"
            )
        logger.info(
            f"Scan complete: {content_changed_count} content change"
            f"{'' if content_changed_count == 1 else 's'} — {log_detail}; "
            f"{len(pending_tasks)} pending tasks, "
            f"{len(completed_tasks)} completed tasks, "
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
            journal_window[date_key] = self._journal_window_entries(info)
            self._state.record(org_file)

        old_tracked = list(self._snapshot.tracked_tasks)
        tracked = self._rebuild_task_registry(today)
        tracked = self._apply_prose_completions(tracked, [])
        pending_tasks, completed_tasks, task_first_seen = derive_task_lists(tracked)

        recurring_topics = self._build_recurring_topics(journal_window)
        active_roam_nodes = self._build_active_roam_nodes()
        stale_tasks = self._flag_stale_tasks_from_tracked(tracked, journal_window)
        task_status_changes = self._compute_task_status_changes(old_tracked, tracked)

        self._snapshot = ContextSnapshot(
            last_scan=self._snapshot.last_scan,
            today_date=self._snapshot.today_date,
            today_entries=self._snapshot.today_entries,
            pending_tasks=pending_tasks,
            completed_tasks=completed_tasks,
            recent_project_changes=self._snapshot.recent_project_changes,
            recent_agent_outputs=self._snapshot.recent_agent_outputs,
            active_projects=self._snapshot.active_projects,
            has_significant_changes=False,
            change_summary="deep scan refresh",
            journal_window=journal_window,
            recurring_topics=recurring_topics,
            active_roam_nodes=active_roam_nodes,
            stale_tasks=stale_tasks,
            task_first_seen=task_first_seen,
            tracked_tasks=tracked,
            task_status_changes=task_status_changes,
        )

        self._save_state()
        self._refresh_file_text_cache(today)
        self._save_cache()
        self._save_file_text_cache()
        logger.info(
            f"Deep scan complete: {len(journal_window)} journal days, "
            f"{len(pending_tasks)} pending tasks, "
            f"{len(completed_tasks)} completed tasks, "
            f"{len(recurring_topics)} recurring topics, "
            f"{len(active_roam_nodes)} active roam nodes, "
            f"{len(stale_tasks)} stale tasks"
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

    def _load_agent_work_status(self) -> str:
        """Read today's background agent work status, if any."""
        path = self.state_dir / "agent_work_status" / f"{date.today().isoformat()}.md"
        if not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if not text:
            return ""
        lines = text.splitlines()
        if lines and lines[0].startswith("#"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    def _load_topic_hints(self) -> str:
        """Read today's proactive topic scout output, if any."""
        path = self.state_dir / "topic_hints" / f"{date.today().isoformat()}.md"
        if not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if not text:
            return ""
        lines = text.splitlines()
        if lines and lines[0].startswith("#"):
            lines = lines[1:]
        body = "\n".join(lines).strip()
        if not body:
            return ""
        return body[:1200] + ("..." if len(body) > 1200 else "")

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

        topic_hints = self._load_topic_hints()
        if topic_hints:
            lines.append("### Topic hints (auto)")
            lines.append(
                "_Generated after the latest significant journal scan. "
                "Use as conversation starters or delegation suggestions._"
            )
            lines.append("")
            lines.append(topic_hints)
            lines.append("")

        if s.pending_tasks:
            lines.append("### Pending Tasks")
            for task in s.pending_tasks[:15]:
                lines.append(f"- [ ] {task}")
            if len(s.pending_tasks) > 15:
                lines.append(f"  _(+ {len(s.pending_tasks) - 15} more)_")
            lines.append("")

        if s.stale_tasks:
            lines.append("### Possibly Stalled")
            for task_text in s.stale_tasks[:8]:
                tracked = self._tracked_task_by_text(s.tracked_tasks, task_text)
                provenance = self._format_task_provenance(tracked) if tracked else ""
                lines.append(f"- [ ] {task_text}{provenance}  _(no recent journal mention)_")
            lines.append("")

        completed_tracked = [t for t in s.tracked_tasks if t.status == "completed"]
        if completed_tracked:
            lines.append("### Recently Completed")
            for task in completed_tracked[:10]:
                lines.append(f"- [x] {task.text}{self._format_task_provenance(task)}")
            lines.append("")
        elif s.completed_tasks:
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
                    topic_signals = [
                        keyword
                        for entry in day_entries
                        for keyword in entry.get("keywords", "").split("|")
                        if keyword
                    ]
                    if topic_signals:
                        lines.append(
                            f"  Topic signals: {', '.join(topic_signals[:6])}"
                        )
                    for entry in day_entries[:5]:
                        if not entry.get("heading") and not entry.get("content"):
                            continue
                        state_prefix = (
                            f"[{entry['state']}] " if entry.get("state") else ""
                        )
                        time_prefix = (
                            f"**{entry['time']}** " if entry.get("time") else ""
                        )
                        lines.append(
                            f"- {time_prefix}{state_prefix}"
                            f"{entry.get('heading', '')}"
                        )
                        if entry.get("content"):
                            lines.append(f"  {entry['content'][:120]}")
                    if len(day_entries) > 5:
                        lines.append(f"  _(+ {len(day_entries) - 5} more entries)_")
                lines.append("")

        if s.recurring_topics:
            lines.append("### Cross-Journal Content Trends")
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

        work_status = self._load_agent_work_status()
        if work_status:
            lines.append("### Background Agent Work (today)")
            lines.append(work_status)
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
                    topic_signals = [
                        keyword
                        for entry in day_entries
                        for keyword in entry.get("keywords", "").split("|")
                        if keyword
                    ]
                    if topic_signals:
                        lines.append(
                            f"  Topic signals: {', '.join(topic_signals[:10])}"
                        )
                    for entry in day_entries[:8]:
                        if not entry.get("heading") and not entry.get("content"):
                            continue
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
            lines.append("### Cross-Journal Content Trends")
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

        # Stale / pending / completed tasks with provenance
        lines.extend(self._format_task_sections_for_briefing(s))

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

        work_status = self._load_agent_work_status()
        if work_status:
            lines.append("### Background Agent Work (today)")
            lines.append(work_status)
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
                candidates = [
                    entry.get("heading", ""),
                    *entry.get("tags", "").split(","),
                    *entry.get("keywords", "").split("|"),
                ]
                for candidate in candidates:
                    normalized = candidate.strip().lower().rstrip(".")
                    if not normalized:
                        continue
                    # Skip generic catch-all headings that are not meaningful.
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

    def _flag_stale_tasks_from_tracked(
        self,
        tracked: list[TrackedTask],
        window: dict[str, list[dict[str, str]]],
        threshold_days: int = 3,
    ) -> list[str]:
        """Return pending tasks with no recent journal mention and old enough."""
        today = date.today()
        cutoff = (today - timedelta(days=threshold_days)).isoformat()

        all_text = " ".join(
            f"{e.get('heading', '')} {e.get('content', '')}".lower()
            for entries in window.values()
            for e in entries
        )

        stale: list[str] = []
        for task in tracked:
            if task.status != "pending":
                continue
            if task.first_seen > cutoff:
                continue
            if text_mentions_task(all_text, task.text):
                continue
            stale.append(task.text)

        return stale[:10]

    def _flag_stale_tasks(
        self,
        pending_tasks: list[str],
        window: dict[str, list[dict[str, str]]],
        task_first_seen: dict[str, str],
        threshold_days: int = 3,
    ) -> list[str]:
        """Legacy stale helper for callers that only have plain pending strings."""
        tracked = [
            TrackedTask(
                text=text,
                status="pending",
                source_path="",
                source_date=task_first_seen.get(normalize_task_key(text), ""),
                source_heading="",
                line_hint=0,
                task_key=normalize_task_key(text),
                completion_kind="checkbox",
                first_seen=task_first_seen.get(normalize_task_key(text), date.today().isoformat()),
                last_seen=date.today().isoformat(),
            )
            for text in pending_tasks
        ]
        return self._flag_stale_tasks_from_tracked(tracked, window, threshold_days)

    def _tracked_task_by_text(
        self, tracked: list[TrackedTask], text: str
    ) -> TrackedTask | None:
        key = normalize_task_key(text)
        for task in tracked:
            if task.task_key == key:
                return task
        return None

    def _format_task_provenance(self, task: TrackedTask) -> str:
        parts: list[str] = []
        if task.source_date:
            parts.append(task.source_date)
        if task.source_path:
            parts.append(task.source_path)
        if task.source_heading:
            parts.append(f"› {task.source_heading}")
        if not parts:
            return ""
        return " _({})_".format(" ".join(parts))

    def _format_task_sections_for_briefing(self, snapshot: ContextSnapshot) -> list[str]:
        lines: list[str] = []

        if snapshot.task_status_changes:
            lines.append("### Task Status Changes Since Last Scan")
            for change in snapshot.task_status_changes[:15]:
                lines.append(
                    f"- [x] {change.text}{self._format_task_provenance_from_change(change)} "
                    f"_(resolved via {change.completion_kind})_"
                )
                if change.detail:
                    lines.append(f"  {change.detail[:200]}")
            lines.append("")

        if snapshot.stale_tasks:
            lines.append("### Stalled / Stale Tasks")
            for task_text in snapshot.stale_tasks:
                tracked = self._tracked_task_by_text(snapshot.tracked_tasks, task_text)
                first_seen = (
                    tracked.first_seen
                    if tracked
                    else snapshot.task_first_seen.get(normalize_task_key(task_text), "unknown")
                )
                provenance = self._format_task_provenance(tracked) if tracked else ""
                lines.append(f"- [ ] {task_text}{provenance}  _(first seen {first_seen})_")
            lines.append("")

        pending_tracked = [t for t in snapshot.tracked_tasks if t.status == "pending"]
        if pending_tracked:
            lines.append("### All Pending Tasks")
            for task in pending_tracked:
                lines.append(f"- [ ] {task.text}{self._format_task_provenance(task)}")
            lines.append("")
        elif snapshot.pending_tasks:
            lines.append("### All Pending Tasks")
            for task in snapshot.pending_tasks:
                lines.append(f"- [ ] {task}")
            lines.append("")

        completed_tracked = [
            t for t in snapshot.tracked_tasks if t.status == "completed"
        ]
        if completed_tracked:
            lines.append("### Recently Completed Tasks")
            for task in completed_tracked[:25]:
                lines.append(
                    f"- [x] {task.text}{self._format_task_provenance(task)} "
                    f"_({task.completion_kind})_"
                )
            lines.append("")
        elif snapshot.completed_tasks:
            lines.append("### Recently Completed Tasks")
            for task in snapshot.completed_tasks:
                lines.append(f"- [x] {task}")
            lines.append("")

        return lines

    def _format_task_provenance_from_change(self, change: TaskStatusChange) -> str:
        parts: list[str] = []
        if change.source_date:
            parts.append(change.source_date)
        if change.source_path:
            parts.append(change.source_path)
        if not parts:
            return ""
        return " _({})_".format(" ".join(parts))

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
                    file_hashes=data.get("file_hashes", {}),
                )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Could not load scan state (starting fresh): {exc}")
        return ScanState()

    def _save_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "last_scan": self._state.last_scan,
            "file_mtimes": self._state.file_mtimes,
            "file_hashes": self._state.file_hashes,
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
