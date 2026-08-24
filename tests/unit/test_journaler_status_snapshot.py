"""Tests for Journaler daemon status snapshots and suggestions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from engineering_hub.journaler.status_snapshot import (
    collect_status_snapshot,
    write_status_file,
)


def test_collect_status_snapshot_uses_fresh_heartbeat(tmp_path: Path) -> None:
    state_dir = tmp_path / ".journaler"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "last_scan": datetime.now().isoformat(timespec="seconds"),
                "file_mtimes": {"a.org": 1.0, "b.org": 2.0},
            }
        ),
        encoding="utf-8",
    )
    (state_dir / "context_cache.json").write_text(
        json.dumps(
            {
                "pending_tasks": ["task one"],
                "completed_tasks": ["done"],
                "stale_tasks": [],
            }
        ),
        encoding="utf-8",
    )
    write_status_file(
        state_dir,
        {
            "pid": 123,
            "status": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "last_heartbeat": datetime.now().isoformat(timespec="seconds"),
            "model_loaded": True,
            "scan_interval_min": 10,
            "engine": {"utilization": "42%", "pressure": "low"},
        },
    )

    snapshot = collect_status_snapshot(
        state_dir,
        chat_host="127.0.0.1",
        chat_port=9,
        chat_enabled=False,
        use_http=False,
    )

    assert snapshot["daemon_online"] is True
    assert snapshot["tracked_files"] == 2
    assert snapshot["pending_tasks"] == 1
    assert snapshot["engine"]["pressure"] == "low"


def test_collect_status_snapshot_marks_stale_heartbeat_offline(tmp_path: Path) -> None:
    state_dir = tmp_path / ".journaler"
    old = datetime.now() - timedelta(minutes=5)
    write_status_file(
        state_dir,
        {
            "last_heartbeat": old.isoformat(timespec="seconds"),
            "status": "running",
            "model_loaded": True,
        },
    )

    snapshot = collect_status_snapshot(
        state_dir,
        chat_host="127.0.0.1",
        chat_port=9,
        chat_enabled=False,
        heartbeat_stale_after_sec=1,
        use_http=False,
    )

    assert snapshot["daemon_online"] is False
    assert any("offline" in suggestion for suggestion in snapshot["suggestions"])


def test_status_suggestions_include_pressure_and_stale_tasks(tmp_path: Path) -> None:
    state_dir = tmp_path / ".journaler"
    now = datetime.now().isoformat(timespec="seconds")
    write_status_file(
        state_dir,
        {
            "last_heartbeat": now,
            "last_scan": now,
            "status": "running",
            "model_loaded": True,
            "pending_tasks": 12,
            "stale_tasks": 2,
            "engine": {"utilization": "88%", "pressure": "high"},
        },
    )

    snapshot = collect_status_snapshot(
        state_dir,
        chat_host="127.0.0.1",
        chat_port=9,
        chat_enabled=False,
        use_http=False,
    )

    suggestions = "\n".join(snapshot["suggestions"])
    assert "Context utilization is high" in suggestions
    assert "Review 2 stale task" in suggestions
    assert "Pending queue is large" in suggestions
