"""Tests for upserting briefing content into today's org journal."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

from engineering_hub.journaler.daemon import (
    DISCUSSION_BRIEFING_JOURNAL_HEADING,
    MORNING_BRIEFING_JOURNAL_HEADING,
    JournalerConfig,
    _persist_discussion_briefing,
    _persist_morning_briefing,
    _write_briefing_to_journal,
)
from engineering_hub.journaler.org_writer import (
    read_section_body,
    upsert_section_in_today_journal,
)


def _minimal_config(tmp_path: Path, *, append_to_journal: bool = True) -> JournalerConfig:
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    state_dir = tmp_path / ".journaler"
    state_dir.mkdir()
    return JournalerConfig(
        model_path="test-model",
        org_roam_dir=tmp_path / "roam",
        journal_dir=journal_dir,
        workspace_dir=tmp_path,
        state_dir=state_dir,
        briefing_append_to_journal=append_to_journal,
    )


def test_upsert_section_creates_heading_on_new_journal(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()

    ok, msg = upsert_section_in_today_journal(
        journal_dir,
        MORNING_BRIEFING_JOURNAL_HEADING,
        "## Cross-Journal Trends\n\nTrend one.",
    )

    assert ok is True
    assert "Created" in msg
    today_path = journal_dir / f"{date.today().isoformat()}.org"
    assert today_path.is_file()
    body = read_section_body(today_path, MORNING_BRIEFING_JOURNAL_HEADING)
    assert "## Cross-Journal Trends" in body
    assert "Trend one." in body


def test_upsert_section_replaces_existing_body(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()

    upsert_section_in_today_journal(
        journal_dir,
        MORNING_BRIEFING_JOURNAL_HEADING,
        "First version",
    )
    upsert_section_in_today_journal(
        journal_dir,
        MORNING_BRIEFING_JOURNAL_HEADING,
        "Second version",
    )

    today_path = journal_dir / f"{date.today().isoformat()}.org"
    raw = today_path.read_text(encoding="utf-8")
    assert raw.count(f"* {MORNING_BRIEFING_JOURNAL_HEADING}") == 1
    body = read_section_body(today_path, MORNING_BRIEFING_JOURNAL_HEADING)
    assert "Second version" in body
    assert "First version" not in body


def test_morning_and_discussion_sections_coexist(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()

    upsert_section_in_today_journal(
        journal_dir,
        MORNING_BRIEFING_JOURNAL_HEADING,
        "## Today's Agenda\n\nShip feature.",
    )
    upsert_section_in_today_journal(
        journal_dir,
        DISCUSSION_BRIEFING_JOURNAL_HEADING,
        "# Topics Discussion Briefing\n\nPersona notes.",
    )

    today_path = journal_dir / f"{date.today().isoformat()}.org"
    morning_body = read_section_body(today_path, MORNING_BRIEFING_JOURNAL_HEADING)
    discussion_body = read_section_body(today_path, DISCUSSION_BRIEFING_JOURNAL_HEADING)
    assert "Ship feature." in morning_body
    assert "Persona notes." in discussion_body


def test_write_briefing_to_journal_respects_disabled_flag(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path, append_to_journal=False)

    with patch(
        "engineering_hub.journaler.daemon.upsert_section_in_today_journal"
    ) as mock_upsert:
        _write_briefing_to_journal(
            config,
            heading=MORNING_BRIEFING_JOURNAL_HEADING,
            text="Should not write",
        )
        mock_upsert.assert_not_called()


def test_persist_morning_briefing_writes_state_and_journal(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    today_str = date.today().isoformat()
    briefing = "## Today's Agenda\n\nReview acoustics report."

    output_path = _persist_morning_briefing(config, today_str, briefing)

    assert output_path.is_file()
    assert "Morning Briefing" in output_path.read_text(encoding="utf-8")
    today_path = config.journal_dir / f"{today_str}.org"
    body = read_section_body(today_path, MORNING_BRIEFING_JOURNAL_HEADING)
    assert "Review acoustics report." in body


def test_persist_discussion_briefing_writes_state_and_journal(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    today_str = date.today().isoformat()
    discussion = f"# Topics Discussion Briefing — {today_str}\n\n### Analyst\n\nInsight."

    output_path = _persist_discussion_briefing(config, today_str, discussion)

    assert output_path.is_file()
    assert "Topics Discussion Briefing" in output_path.read_text(encoding="utf-8")
    today_path = config.journal_dir / f"{today_str}.org"
    body = read_section_body(today_path, DISCUSSION_BRIEFING_JOURNAL_HEADING)
    assert "Insight." in body


def test_write_briefing_to_journal_logs_warning_on_failure(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)

    with patch(
        "engineering_hub.journaler.daemon.upsert_section_in_today_journal",
        return_value=(False, "disk full"),
    ):
        with patch("engineering_hub.journaler.daemon.logger") as mock_logger:
            _write_briefing_to_journal(
                config,
                heading=MORNING_BRIEFING_JOURNAL_HEADING,
                text="Briefing body",
            )
            mock_logger.warning.assert_called_once()
            assert "disk full" in mock_logger.warning.call_args[0][1]
