"""Tests for Journaler task planner, pending-tasks.org parsing, and write-back."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import ParsedTask
from engineering_hub.notes.org_task_parser import OrgTaskParser
from engineering_hub.notes.org_task_writer import OrgTaskWriter
from engineering_hub.journaler.task_committer import TaskCommitter, ensure_pending_tasks_file
from engineering_hub.journaler.task_planner_models import ProposedTask


def test_org_task_parser_extra_file(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    pending = tmp_path / "pending-tasks.org"
    pending.write_text(
        "#+title: Test\n\n"
        "* Pending Agent Tasks\n\n"
        "** @research: Short title\n"
        ":PROPERTIES:\n"
        ":SESSION_ID: abc-123\n"
        ":STATUS: PENDING\n"
        ":END:\n"
        "- [ ] @research: Full task description [[django://project/7]] → [[outputs/x.md]]\n\n"
        "* Completed Agent Tasks\n",
        encoding="utf-8",
    )
    parser = OrgTaskParser(
        journal_dir=journal_dir,
        task_sections=["Pending Agent Tasks"],
        lookback_days=1,
        extra_files=[pending],
    )
    tasks = parser.parse_tasks()
    assert len(tasks) == 1
    t = tasks[0]
    assert t.agent == "research"
    assert t.project_id == 7
    assert t.deliverable == "outputs/x.md"
    assert t.source_path == str(pending.resolve())


def test_parsed_task_task_id_uses_source_path(tmp_path: Path) -> None:
    p = str(tmp_path / "f.org")
    t = ParsedTask(
        agent="research",
        status=TaskStatus.PENDING,
        description="d",
        start_line=3,
        end_line=3,
        raw_block="- [ ] @research: d",
        source_path=p,
    )
    assert t.task_id == f"{p}:3"


def test_task_committer_commit_and_rollback(tmp_path: Path) -> None:
    pending = tmp_path / "pending-tasks.org"
    committer = TaskCommitter(pending)
    opened = datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    sess_id = "sess-1"
    task = ProposedTask(
        agent_type="research",
        description="Do the thing",
        session_id=sess_id,
        session_timestamp=opened,
        proposed_at=now,
        keywords=["a", "b"],
        project_id=99,
        input_paths=["inputs/foo.md"],
        output_path="outputs/out.md",
        status="confirmed",
        confidence=0.9,
        clarification_needed=False,
    )
    ok, written = committer.commit_tasks([task], session_timestamp=opened)
    assert ok
    assert "Do the thing" in written
    text = pending.read_text(encoding="utf-8")
    assert ":SESSION_ID: sess-1" in text
    assert "[[django://project/99]]" in text

    n, msg = committer.rollback(sess_id, mode="all")
    assert n == 1
    assert "sess-1" not in pending.read_text(encoding="utf-8")


def test_org_task_writer_moves_completed_pending_block(tmp_path: Path) -> None:
    pending = tmp_path / "pending-tasks.org"
    ensure_pending_tasks_file(pending)
    content = pending.read_text(encoding="utf-8")
    insert = (
        "** @research: Block\n"
        ":PROPERTIES:\n"
        ":SESSION_ID: x\n"
        ":STATUS: PENDING\n"
        ":END:\n"
        "- [ ] @research: Block body\n\n"
    )
    marker = "* Completed Agent Tasks"
    idx = content.index(marker)
    new_c = content[:idx] + insert + content[idx:]
    pending.write_text(new_c, encoding="utf-8")

    lines = pending.read_text(encoding="utf-8").splitlines()
    cb_line = next(i for i, ln in enumerate(lines) if "Block body" in ln)
    task = ParsedTask(
        agent="research",
        status=TaskStatus.PENDING,
        description="Block body",
        start_line=cb_line,
        end_line=cb_line,
        raw_block=f"- [ ] @research: Block body",
        source_path=str(pending.resolve()),
        category="Pending Agent Tasks",
    )
    writer = OrgTaskWriter(journal_dir=tmp_path)
    writer.update_task_status(task, TaskStatus.COMPLETED)

    final = pending.read_text(encoding="utf-8")
    assert ":STATUS: DONE" in final
    assert "[x]" in final
    assert "* Completed Agent Tasks" in final
    # Original pending checkbox line should be moved, not duplicated as [ ]
    assert final.count("- [ ] @research: Block body") == 0
