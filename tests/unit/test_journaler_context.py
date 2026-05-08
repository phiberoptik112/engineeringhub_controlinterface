from __future__ import annotations

from datetime import datetime
from pathlib import Path

from engineering_hub.journaler.context import JournalContext
from engineering_hub.journaler.models import OrgEntry, OrgFileInfo


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
