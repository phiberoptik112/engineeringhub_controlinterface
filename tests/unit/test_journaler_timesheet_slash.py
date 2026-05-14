"""Tests for Journaler /timesheet command handling."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from engineering_hub.journaler.org_writer import append_timesheet_entry
from engineering_hub.journaler.timesheet_slash import (
    handle_timesheet_slash_command,
    parse_timesheet_slash_command,
)


def _journal_dir(tmp_path: Path) -> Path:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    return journal_dir


def test_parse_project_keyword_with_description_delimiter() -> None:
    entry = parse_timesheet_slash_command(
        '/timesheet 2 project "Project X" :: report drafting and coordination'
    )

    assert entry.hours == 2
    assert entry.project == "Project X"
    assert entry.description == "report drafting and coordination"
    assert entry.project_id is None


def test_parse_flags_with_project_id() -> None:
    entry = parse_timesheet_slash_command(
        '/timesheet 1.5 --project "Phase B" --project-id 42 --desc "data review"'
    )

    assert entry.hours == 1.5
    assert entry.project == "Phase B"
    assert entry.description == "data review"
    assert entry.project_id == "42"


def test_parse_numeric_project_creates_django_project_label() -> None:
    entry = parse_timesheet_slash_command("/timesheet 0.25 project 42 :: follow-up")

    assert entry.project == "Project 42"
    assert entry.project_id == "42"


@pytest.mark.parametrize(
    "raw, message",
    [
        ("/timesheet", "Usage:"),
        ("/timesheet nope project X :: work", "Hours must be a number"),
        ("/timesheet 0 project X :: work", "Hours must be greater than 0"),
        ("/timesheet 1 :: work", "Project is required"),
        ("/timesheet 1 project X", "Description is required"),
    ],
)
def test_parse_validation(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_timesheet_slash_command(raw)


def test_append_timesheet_entry_creates_daily_journal_and_groups_projects(
    tmp_path: Path,
) -> None:
    journal_dir = _journal_dir(tmp_path)
    now = datetime(2026, 5, 7, 22, 55)
    ok, msg = append_timesheet_entry(
        journal_dir,
        project="Project X",
        hours=2,
        description="report drafting",
        now=now,
    )
    assert ok is True
    assert "Logged 2.00h to Project X" in msg

    ok, _ = append_timesheet_entry(
        journal_dir,
        project="Project X",
        hours=0.5,
        description="client follow-up",
        now=now,
    )
    assert ok is True

    journal = journal_dir / f"{datetime.now().strftime('%Y-%m-%d')}.org"
    text = journal.read_text(encoding="utf-8")

    assert "* Timesheet" in text
    assert text.count("** Project X") == 1
    assert "- [2026-05-07 Thu 22:55] 2.00h :: report drafting" in text
    assert "- [2026-05-07 Thu 22:55] 0.50h :: client follow-up" in text

    reference = tmp_path / "timesheets" / "timesheet-reference.org"
    reference_text = reference.read_text(encoding="utf-8")
    assert "#+title: Timesheet Reference" in reference_text
    assert "#+filetags: :timesheet:agent-context:worklog:" in reference_text
    assert reference_text.count("** Project X :project:project_project_x:") == 1
    assert f"[[file:{journal}][{journal.name}]]" in reference_text


def test_append_timesheet_entry_links_project_id(tmp_path: Path) -> None:
    journal_dir = _journal_dir(tmp_path)
    ok, _ = append_timesheet_entry(
        journal_dir,
        project="Project 42",
        hours=1,
        description="field notes",
        project_id="42",
        now=datetime(2026, 5, 7, 9, 0),
    )
    assert ok is True

    journal = journal_dir / f"{datetime.now().strftime('%Y-%m-%d')}.org"
    text = journal.read_text(encoding="utf-8")
    assert "** [[django://project/42][Project 42]]" in text

    reference = tmp_path / "timesheets" / "timesheet-reference.org"
    reference_text = reference.read_text(encoding="utf-8")
    assert "** [[django://project/42][Project 42]] :project:project_42:" in reference_text
    assert "- Project link: [[django://project/42][Project 42]]" in reference_text


def test_handle_timesheet_slash_command_writes_entry(tmp_path: Path) -> None:
    journal_dir = _journal_dir(tmp_path)
    msg = handle_timesheet_slash_command(
        '/timesheet 1 project "Project X" :: planning',
        journal_dir,
    )

    assert "Logged 1.00h to Project X" in msg
    assert "Updated timesheet reference:" in msg
    journal = journal_dir / f"{datetime.now().strftime('%Y-%m-%d')}.org"
    assert "1.00h :: planning" in journal.read_text(encoding="utf-8")
