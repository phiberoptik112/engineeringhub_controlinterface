"""Tests for Journaler file browser listing and search helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from engineering_hub.journaler.file_browser import (
    _format_created_at,
    _scan_directory,
    _search_loadable_files,
)


def test_scan_directory_includes_hidden_dirs_and_supported_dotfiles(
    tmp_path: Path,
) -> None:
    hidden_dir = tmp_path / ".config"
    hidden_dir.mkdir()
    hidden_org = tmp_path / ".notes.org"
    hidden_org.write_text("* Hidden note\n", encoding="utf-8")
    visible_md = tmp_path / "visible.md"
    visible_md.write_text("# Visible\n", encoding="utf-8")
    unsupported_hidden = tmp_path / ".secret"
    unsupported_hidden.write_text("ignore me\n", encoding="utf-8")

    entries = _scan_directory(tmp_path, tmp_path, frozenset({".md", ".org"}))

    names = [entry.name for entry in entries]
    assert ".config/" in names
    assert ".notes.org" in names
    assert "visible.md" in names
    assert ".secret" not in names

    hidden_entry = next(entry for entry in entries if entry.name == ".notes.org")
    assert hidden_entry.size == len("* Hidden note\n")
    assert hidden_entry.created_at is not None


def test_format_created_at_uses_iso_date() -> None:
    created_at = datetime(2024, 1, 2, 9, 30).timestamp()

    assert _format_created_at(created_at) == "2024-01-02"
    assert _format_created_at(None) == "-"


def test_search_loadable_files_searches_home_relative_paths(tmp_path: Path) -> None:
    hidden_dir = tmp_path / ".config"
    hidden_dir.mkdir()
    hidden_note = hidden_dir / ".meeting.org"
    hidden_note.write_text("* Meeting\n", encoding="utf-8")
    report = tmp_path / "Project Report.md"
    report.write_text("# Report\n", encoding="utf-8")
    ignored = tmp_path / "meeting.bin"
    ignored.write_text("ignore me\n", encoding="utf-8")

    meeting_results = _search_loadable_files(
        tmp_path,
        "meeting",
        frozenset({".md", ".org"}),
        limit=10,
    )
    report_results = _search_loadable_files(
        tmp_path,
        "project report",
        frozenset({".md", ".org"}),
        limit=10,
    )

    assert [entry.name for entry in meeting_results] == [".config/.meeting.org"]
    assert meeting_results[0].path == hidden_note
    assert [entry.path for entry in report_results] == [report]


def test_search_loadable_files_honors_limit(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"file-{index}.md").write_text("# Match\n", encoding="utf-8")

    results = _search_loadable_files(
        tmp_path,
        "file",
        frozenset({".md"}),
        limit=2,
    )

    assert len(results) == 2
