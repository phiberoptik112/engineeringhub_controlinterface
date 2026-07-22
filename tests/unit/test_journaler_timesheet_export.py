"""Tests for monthly timesheet export."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from engineering_hub.journaler.org_writer import append_timesheet_entry
from engineering_hub.journaler.timesheet_export import (
    collect_month_entries,
    export_monthly_timesheet,
    handle_timesheet_export_command,
    parse_timesheet_export_command,
)
from engineering_hub.journaler.timesheet_slash import handle_timesheet_slash_command


def _journal_dir(tmp_path: Path) -> Path:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    return journal_dir


def test_parse_timesheet_export_command() -> None:
    request = parse_timesheet_export_command(
        '/timesheet export --month 2026-07 --project "LVT Phase B" --project-id 42'
    )
    assert request.month == "2026-07"
    assert request.project == "LVT Phase B"
    assert request.project_id == "42"


def test_append_timesheet_entry_creates_monthly_note_with_project_id(
    tmp_path: Path,
) -> None:
    journal_dir = _journal_dir(tmp_path)
    ok, _ = append_timesheet_entry(
        journal_dir,
        project="LVT Phase B",
        hours=1.25,
        description="reviewed test data",
        project_id="42",
        now=datetime(2026, 7, 15, 10, 0),
    )
    assert ok is True

    monthly = tmp_path / "timesheets" / "2026-07-project-42.org"
    text = monthly.read_text(encoding="utf-8")
    assert "#+title: 2026-07 Timesheet — LVT Phase B" in text
    assert ":PROJECT_ID: 42" in text
    assert "#+filetags: :timesheet:monthly:project_42:" in text
    assert "1.25h :: reviewed test data" in text


def test_collect_month_entries_from_monthly_note(tmp_path: Path) -> None:
    journal_dir = _journal_dir(tmp_path)
    append_timesheet_entry(
        journal_dir,
        project="Project X",
        hours=2,
        description="drafting",
        now=datetime(2026, 7, 1, 9, 0),
    )
    append_timesheet_entry(
        journal_dir,
        project="Project X",
        hours=1,
        description="review",
        now=datetime(2026, 7, 2, 9, 0),
    )

    entries = collect_month_entries(journal_dir, "2026-07", "Project X", None)
    assert len(entries) == 2
    assert sum(item.hours for item in entries) == 3.0


def test_export_monthly_timesheet_fills_template(tmp_path: Path) -> None:
    journal_dir = _journal_dir(tmp_path)
    append_timesheet_entry(
        journal_dir,
        project="LVT Phase B",
        hours=2.5,
        description="report drafting",
        project_id="42",
        now=datetime(2026, 7, 10, 14, 0),
    )

    template = tmp_path / "template.org"
    template.write_text(
        "#+title: ${month} — ${project}\nTotal: ${total_hours}\n${entry_lines}\n",
        encoding="utf-8",
    )

    request = parse_timesheet_export_command(
        '/timesheet export --month 2026-07 --project "LVT Phase B" --project-id 42'
    )
    ok, msg = export_monthly_timesheet(
        journal_dir,
        request,
        template_path=template,
        now=datetime(2026, 7, 15, 16, 0),
    )
    assert ok is True
    assert "2.50h" in msg

    output = tmp_path / "timesheets" / "exports" / "2026-07-project-42-final.org"
    text = output.read_text(encoding="utf-8")
    assert "#+title: 2026-07 — LVT Phase B" in text
    assert "Total: 2.50" in text
    assert "2.50h :: report drafting" in text


def test_handle_timesheet_export_command_custom_output(tmp_path: Path) -> None:
    journal_dir = _journal_dir(tmp_path)
    append_timesheet_entry(
        journal_dir,
        project="Project X",
        hours=1,
        description="planning",
        now=datetime(2026, 7, 5, 11, 0),
    )

    template = tmp_path / "custom.org"
    template.write_text("Month ${month}\nHours ${total_hours}\n", encoding="utf-8")
    output = tmp_path / "final.org"

    msg = handle_timesheet_slash_command(
        '/timesheet export --month 2026-07 --project "Project X" '
        f'--template {template} -o {output}',
        journal_dir,
    )
    assert "Exported 2026-07 timesheet" in msg
    assert output.read_text(encoding="utf-8") == "Month 2026-07\nHours 1.00\n"


def test_timesheet_agent_alias_resolves() -> None:
    from engineering_hub.journaler.delegator import AgentDelegator

    delegator = AgentDelegator(mlx_backend=object())
    assert delegator.resolve_agent_type("timesheet") == "timesheet-reviewer"
    assert delegator.resolve_agent_type("timesheets") == "timesheet-reviewer"


@pytest.mark.parametrize(
    "raw, message",
    [
        ("/timesheet export", "Month must be"),
        ("/timesheet export --month bad --project X", "Month must be"),
        ("/timesheet export --month 2026-07", "Project is required"),
    ],
)
def test_parse_export_validation(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_timesheet_export_command(raw)
