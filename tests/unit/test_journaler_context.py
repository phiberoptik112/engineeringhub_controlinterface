from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from engineering_hub.journaler.context import JournalContext
from engineering_hub.journaler.models import OrgEntry, OrgFileInfo, ScanState


def _make_ctx(tmp_path: Path, *, scan_tree: bool = True) -> JournalContext:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    roam_dir = tmp_path / "roam"
    roam_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = workspace / ".journaler"
    state.mkdir(parents=True)
    return JournalContext(
        org_roam_dir=roam_dir,
        journal_dir=journal_dir,
        workspace_dir=workspace,
        memory_service=None,
        state_dir=state,
        scan_org_roam_tree=scan_tree,
        journal_lookback_days=30,
        journal_max_files=30,
    )


def test_journal_window_entries_include_topic_keywords(tmp_path: Path) -> None:
    ctx = JournalContext(
        org_roam_dir=tmp_path,
        journal_dir=tmp_path,
        workspace_dir=tmp_path,
        memory_service=None,
        state_dir=tmp_path / "state",
    )
    info = OrgFileInfo(
        path=tmp_path / "2026-05-06.org",
        filetags=["acoustics"],
        entries=[
            OrgEntry(
                level=1,
                title="Client call",
                tags=["meeting"],
                timestamp=datetime(2026, 5, 6, 9, 30),
                body="Discuss ASTM E336 test planning.",
            )
        ],
    )

    entries = ctx._journal_window_entries(info)

    assert entries[0]["time"] == "09:30"
    assert entries[0]["heading"] == "Client call"
    assert "keywords" in entries[0]
    assert "acoustics" in entries[0]["keywords"]
    assert "meeting: Client call" in entries[0]["keywords"]
    assert "ASTM E336" in entries[0]["keywords"]


def test_path_key_canonical(tmp_path: Path) -> None:
    target = tmp_path / "note.org"
    target.write_text("* Heading\n", encoding="utf-8")

    assert ScanState.path_key(target) == ScanState.path_key(Path(str(target)))
    assert ScanState.path_key(target) == ScanState.path_key(
        target.resolve().parent / "note.org"
    )


def test_second_scan_zero_content_changes(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    for name in ("alpha.org", "beta.org", "gamma.org"):
        (ctx.org_roam_dir / name).write_text(f"* {name}\n", encoding="utf-8")

    first = ctx.scan()
    assert first.change_summary  # baseline scan establishes hashes

    second = ctx.scan()
    assert not second.has_significant_changes
    assert "no content changes" in second.change_summary
    assert "0 significant" in second.change_summary


def test_mtime_bump_not_significant(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    today = date.today().isoformat()
    journal = ctx.journal_dir / f"{today}.org"
    journal.write_text("* Morning notes\n", encoding="utf-8")

    ctx.scan()
    original_mtime = journal.stat().st_mtime
    os.utime(journal, (original_mtime + 60, original_mtime + 60))

    snapshot = ctx.scan()
    assert not snapshot.has_significant_changes
    assert "mtime-only" in snapshot.change_summary or "0 significant" in snapshot.change_summary


def test_journal_entry_diff(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=False)
    today = date.today().isoformat()
    journal = ctx.journal_dir / f"{today}.org"
    journal.write_text("* First entry\n", encoding="utf-8")

    ctx.scan()
    journal.write_text("* First entry\n* Second entry\n", encoding="utf-8")

    snapshot = ctx.scan()
    assert snapshot.has_significant_changes
    assert "+1 entry" in snapshot.change_summary or "+2 entries" in snapshot.change_summary
    assert "Second entry" in snapshot.change_summary


def test_roam_content_not_significant(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    roam_note = ctx.org_roam_dir / "projects" / "client.org"
    roam_note.parent.mkdir(parents=True)
    roam_note.write_text("* Project alpha\n", encoding="utf-8")

    ctx.scan()
    roam_note.write_text("* Project alpha\n* New roam heading\n", encoding="utf-8")

    snapshot = ctx.scan()
    assert not snapshot.has_significant_changes
    assert "roam updated" in snapshot.change_summary


def test_state_persists_hashes(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    (ctx.org_roam_dir / "note.org").write_text("* Test\n", encoding="utf-8")

    ctx.scan()

    state_path = ctx.state_dir / "state.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert "file_hashes" in data
    assert len(data["file_hashes"]) >= 1


def test_diff_journal_entries() -> None:
    ctx = JournalContext(
        org_roam_dir=Path("/tmp"),
        journal_dir=Path("/tmp"),
        workspace_dir=Path("/tmp"),
        memory_service=None,
        state_dir=Path("/tmp/state"),
    )
    old = [{"time": "09:00", "heading": "Existing", "content": ""}]
    new = [
        {"time": "09:00", "heading": "Existing", "content": ""},
        {"time": "10:00", "heading": "Added", "content": ""},
    ]
    added = ctx._diff_journal_entries(old, new)
    assert added == ["Added"]


def test_journal_changes_merge_into_recent_project_changes(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=False)
    today = date.today().isoformat()
    journal = ctx.journal_dir / f"{today}.org"
    journal.write_text("* Initial\n", encoding="utf-8")
    ctx.scan()

    journal.write_text("* Initial\n* Follow-up task\n", encoding="utf-8")
    snapshot = ctx.scan()

    assert snapshot.recent_project_changes
    assert any(today in c["file"] for c in snapshot.recent_project_changes)


def test_pending_tasks_file_counts_as_significant(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=False)
    pending = ctx.workspace_dir / ".journaler" / "pending-tasks.org"
    pending.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    pending.write_text(
        f"** {today} 10:00 :STATUS: PENDING\n- [ ] Queue item\n",
        encoding="utf-8",
    )
    ctx._pending_tasks_file = pending

    first = ctx.scan()
    assert first.has_significant_changes

    second = ctx.scan()
    assert not second.has_significant_changes


def test_checkbox_completion_removes_pending(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=False)
    day = date.today().isoformat()
    journal = ctx.journal_dir / f"{day}.org"
    journal.write_text(
        "* Work\n- [ ] Finish ASTM E336 protocol draft\n",
        encoding="utf-8",
    )
    first = ctx.scan()
    assert "Finish ASTM E336 protocol draft" in first.pending_tasks

    journal.write_text(
        "* Work\n- [X] Finish ASTM E336 protocol draft\n",
        encoding="utf-8",
    )
    second = ctx.scan()
    assert "Finish ASTM E336 protocol draft" not in second.pending_tasks
    assert "Finish ASTM E336 protocol draft" in second.completed_tasks


def test_cross_day_pending_preserved_when_one_journal_edited(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=False)
    today = date.today()
    yesterday = today - timedelta(days=1)
    y_journal = ctx.journal_dir / f"{yesterday.isoformat()}.org"
    t_journal = ctx.journal_dir / f"{today.isoformat()}.org"
    y_journal.write_text("* Yesterday\n- [ ] Old pending item\n", encoding="utf-8")
    t_journal.write_text("* Today\n- [ ] Today pending item\n", encoding="utf-8")
    ctx.scan()

    t_journal.write_text(
        "* Today\n- [X] Today pending item\n",
        encoding="utf-8",
    )
    snapshot = ctx.scan()
    assert "Old pending item" in snapshot.pending_tasks
    assert "Today pending item" not in snapshot.pending_tasks


def test_prose_completion_marks_task_done(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=False)
    ctx.prose_completion_detection = True
    day = date.today().isoformat()
    journal = ctx.journal_dir / f"{day}.org"
    journal.write_text(
        "* Work\n- [ ] ASTM E336 protocol draft\n",
        encoding="utf-8",
    )
    ctx.scan()

    journal.write_text(
        "* Work\n- [ ] ASTM E336 protocol draft\nFinished ASTM E336 protocol draft.\n",
        encoding="utf-8",
    )
    snapshot = ctx.scan()
    assert "ASTM E336 protocol draft" in snapshot.completed_tasks
    assert "ASTM E336 protocol draft" not in snapshot.pending_tasks


def test_long_section_checkbox_still_detected(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=False)
    day = date.today().isoformat()
    journal = ctx.journal_dir / f"{day}.org"
    padding = "x" * 700
    journal.write_text(
        f"* Work\n{padding}\n- [ ] Deep checkbox task\n",
        encoding="utf-8",
    )
    snapshot = ctx.scan()
    assert "Deep checkbox task" in snapshot.pending_tasks


def test_roam_checkbox_in_task_registry(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=True)
    roam_note = ctx.org_roam_dir / "projects" / "client.org"
    roam_note.parent.mkdir(parents=True)
    roam_note.write_text("* Project\n- [ ] Roam-side deliverable\n", encoding="utf-8")
    snapshot = ctx.scan()
    assert "Roam-side deliverable" in snapshot.pending_tasks
    assert any("client.org" in t.source_path for t in snapshot.tracked_tasks)


def test_briefing_includes_task_provenance(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, scan_tree=False)
    day = date.today().isoformat()
    journal = ctx.journal_dir / f"{day}.org"
    journal.write_text("* Client Work\n- [ ] Draft ASTM section\n", encoding="utf-8")
    ctx.scan()
    briefing = ctx.get_briefing_context()
    assert "Draft ASTM section" in briefing
    assert day in briefing
    assert "Client Work" in briefing or f"{day}.org" in briefing
