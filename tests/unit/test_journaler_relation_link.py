"""Tests for Journaler cross-reference link helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from engineering_hub.journaler.engine import (
    _relation_excerpt,
    _resolve_relation_file_link,
    _write_relation_link,
)
from engineering_hub.memory.service import MemoryResult


def _hit(
    *,
    content: str = "Journaler daily summary (2026-04-15):\nDiscussed ASTM scope.",
    created_at: str = "2026-04-15T10:00:00",
    tags: list[str] | None = None,
) -> MemoryResult:
    return MemoryResult(
        id=1,
        content=content,
        source="journaler",
        similarity=0.85,
        tags=tags or ["daily_summary", "2026-04-15"],
        created_at=created_at,
    )


def test_resolve_relation_file_link_prefers_daily_journal(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    journal_file = journal_dir / "2026-04-15.org"
    journal_file.write_text("* Notes\n", encoding="utf-8")

    state_dir = tmp_path / ".journaler"
    summary_dir = state_dir / "daily_summaries"
    summary_dir.mkdir(parents=True)
    (summary_dir / "2026-04-15.md").write_text("# summary\n", encoding="utf-8")

    link = _resolve_relation_file_link("2026-04-15", journal_dir, state_dir)
    assert link.startswith("[[file:")
    assert "2026-04-15.org" in link
    assert "2026-04-15 daily journal" in link


def test_resolve_relation_file_link_falls_back_to_summary(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    state_dir = tmp_path / ".journaler"
    summary_dir = state_dir / "daily_summaries"
    summary_dir.mkdir(parents=True)
    summary_file = summary_dir / "2026-04-15.md"
    summary_file.write_text("# summary\n", encoding="utf-8")

    link = _resolve_relation_file_link("2026-04-15", journal_dir, state_dir)
    assert str(summary_file.resolve()) in link
    assert "Journaler summary 2026-04-15" in link


def test_relation_excerpt_respects_char_limit() -> None:
    content = "x" * 500
    excerpt = _relation_excerpt(content, excerpt_chars=400)
    assert excerpt.endswith("...")
    assert len(excerpt) == 403


def test_write_relation_link_appends_with_longer_excerpt(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    today = date.today().isoformat()
    today_path = journal_dir / f"{today}.org"
    today_path.write_text("* Notes\n", encoding="utf-8")

    related = journal_dir / "2026-04-15.org"
    related.write_text("* Notes\n", encoding="utf-8")

    state_dir = tmp_path / ".journaler"
    long_content = "A" * 500
    hit = _hit(content=long_content)

    _write_relation_link(journal_dir, state_dir, hit, excerpt_chars=400)

    body = today_path.read_text(encoding="utf-8")
    assert "* Journaler Cross-References" in body
    assert "2026-04-15.org" in body
    assert "85% match" in body
    assert "A" * 400 in body
    assert "[[journaler:" not in body


def test_write_relation_link_dedup_skips_same_date(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    today = date.today().isoformat()
    today_path = journal_dir / f"{today}.org"
    today_path.write_text(
        "* Journaler Cross-References\n"
        "- [[file:/old/path/2026-04-15.org][2026-04-15 daily journal]] (85% match): first\n",
        encoding="utf-8",
    )

    related = journal_dir / "2026-04-15.org"
    related.write_text("* Notes\n", encoding="utf-8")

    state_dir = tmp_path / ".journaler"
    hit = _hit(content="second attempt")

    _write_relation_link(journal_dir, state_dir, hit, excerpt_chars=400)

    body = today_path.read_text(encoding="utf-8")
    assert body.count("2026-04-15") == 2
    assert "second attempt" not in body
