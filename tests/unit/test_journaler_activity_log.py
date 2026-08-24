"""Tests for org-mode Journaler activity logging."""

from __future__ import annotations

from pathlib import Path

from engineering_hub.journaler.activity_log import ActivityLogConfig, JournalerActivityLog


def test_activity_log_appends_to_daily_journal(tmp_path: Path) -> None:
    roam = tmp_path / "roam"
    journal_dir = roam / "journals"
    journal_dir.mkdir(parents=True)
    log = JournalerActivityLog(
        ActivityLogConfig(enabled=True, mode="daily_journal"),
        org_roam_dir=roam,
        journal_dir=journal_dir,
    )

    log.append_event(
        "scan_complete",
        "Scan complete",
        details={"Pending tasks": 2},
        suggestions=["Review pending work."],
        properties={"pid": 42},
    )

    files = list(journal_dir.glob("*.org"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "* Journaler Activity" in text
    assert "** [" in text
    assert ":EVENT: scan_complete" in text
    assert ":PID: 42" in text
    assert "- [ ] Review pending work." in text


def test_activity_log_creates_dedicated_file(tmp_path: Path) -> None:
    roam = tmp_path / "roam"
    journal_dir = roam / "journals"
    journal_dir.mkdir(parents=True)
    target = roam / "journaler-activity.org"
    log = JournalerActivityLog(
        ActivityLogConfig(
            enabled=True,
            mode="dedicated_file",
            path=target,
            heading="Daemon Log",
            include_suggestions=False,
        ),
        org_roam_dir=roam,
        journal_dir=journal_dir,
    )

    log.append_event(
        "daemon_start",
        "Daemon started",
        details={"Model": "hub/model"},
        suggestions=["Should not be written."],
    )

    text = target.read_text(encoding="utf-8")
    assert "#+title: Journaler Activity" in text
    assert "* Daemon Log" in text
    assert ":EVENT: daemon_start" in text
    assert "- Model: hub/model" in text
    assert "Should not be written" not in text
