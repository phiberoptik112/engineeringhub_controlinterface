"""Tests for org-roam /open path guard and edit-target helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from engineering_hub.journaler.engine import ConversationEngine
from engineering_hub.journaler.org_writer import (
    append_to_heading,
    assert_org_path_under_roam,
)


def test_assert_org_path_under_roam_accepts_file_under_roam(tmp_path: Path) -> None:
    roam = tmp_path / "roam"
    roam.mkdir()
    note = roam / "20260101120000-note.org"
    note.write_text("#+title: Note\n\n* Body\n", encoding="utf-8")
    ok, res = assert_org_path_under_roam(note, roam)
    assert ok is True
    assert isinstance(res, Path)
    assert res == note.resolve()


def test_assert_org_path_under_roam_rejects_missing(tmp_path: Path) -> None:
    roam = tmp_path / "roam"
    roam.mkdir()
    missing = roam / "nope.org"
    ok, res = assert_org_path_under_roam(missing, roam)
    assert ok is False
    assert "not found" in str(res).lower()


def test_assert_org_path_under_roam_rejects_non_org(tmp_path: Path) -> None:
    roam = tmp_path / "roam"
    roam.mkdir()
    bad = roam / "readme.txt"
    bad.write_text("x", encoding="utf-8")
    ok, res = assert_org_path_under_roam(bad, roam)
    assert ok is False
    assert "org" in str(res).lower()


def test_assert_org_path_under_roam_rejects_outside_tree(tmp_path: Path) -> None:
    roam = tmp_path / "roam"
    roam.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    esc = outside / "escape.org"
    esc.write_text("#+title: X\n", encoding="utf-8")
    ok, res = assert_org_path_under_roam(esc, roam)
    assert ok is False
    assert "under org-roam" in str(res).lower()


def test_append_after_resolved_target_round_trip(tmp_path: Path) -> None:
    roam = tmp_path / "roam"
    roam.mkdir()
    note = roam / "node.org"
    note.write_text(
        "#+title: Node\n\n* Section A\n\nexisting\n",
        encoding="utf-8",
    )
    ok, resolved = assert_org_path_under_roam(note, roam)
    assert ok is True
    assert isinstance(resolved, Path)
    ok2, msg = append_to_heading(resolved, "Section A", "new line", create_heading_if_missing=True)
    assert ok2 is True
    text = note.read_text(encoding="utf-8")
    assert "existing" in text
    assert "new line" in text


def test_conversation_engine_roam_edit_target_get_set(tmp_path: Path) -> None:
    backend = MagicMock()
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    engine = ConversationEngine(backend, "system", log_dir, max_history=2)
    assert engine.get_roam_edit_target() is None
    target = tmp_path / "f.org"
    target.touch()
    engine.set_roam_edit_target(target)
    assert engine.get_roam_edit_target() == target.resolve()
    engine.set_roam_edit_target(None)
    assert engine.get_roam_edit_target() is None
