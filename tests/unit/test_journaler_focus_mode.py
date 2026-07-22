"""Tests for Journaler focus technical-writing mode."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console

from engineering_hub.cli import _handle_chat_slash_command
from engineering_hub.journaler.context_manager import ClearStrategy
from engineering_hub.journaler.engine import ConversationEngine, FocusDocument


def _engine(tmp_path: Path) -> ConversationEngine:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    return ConversationEngine(
        MagicMock(),
        "AMBIENT SYSTEM PROMPT",
        log_dir,
        model_context_window=8192,
        max_tokens=512,
    )


def test_focus_document_replaces_ambient_context_and_loaded_files(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    loaded = tmp_path / "loaded.md"
    loaded.write_text("loaded file context", encoding="utf-8")
    engine.update_context("ambient workspace context")
    ok, _ = engine.load_file(loaded)
    assert ok is True

    focus = tmp_path / "draft.md"
    focus.write_text("focused draft text", encoding="utf-8")
    engine.set_focus_document(
        FocusDocument(path=focus, label="draft.md", content="focused draft text")
    )

    system = engine._build_messages()[0]["content"]

    assert "focused draft text" in system
    assert "Focus Document" in system
    assert "AMBIENT SYSTEM PROMPT" not in system
    assert "ambient workspace context" not in system
    assert "loaded file context" not in system
    assert engine.budget.context_snapshot_tokens == 0
    assert engine.budget.loaded_files_tokens > 0


def test_delegate_context_uses_focus_document_only(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    loaded = tmp_path / "loaded.md"
    loaded.write_text("ordinary loaded context", encoding="utf-8")
    ok, _ = engine.load_file(loaded)
    assert ok is True

    focus = tmp_path / "report.md"
    engine.set_focus_document(
        FocusDocument(path=focus, label="report.md", content="revise this report")
    )

    result = engine.build_delegate_context_result("revise the executive summary")

    assert "Focus document from Journaler chat" in result.context
    assert "revise this report" in result.context
    assert "ordinary loaded context" not in result.context
    assert result.web_search_attempted is False


def test_focus_slash_command_loads_document_and_clears_history(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.inject_turn("before", "old context")
    assert engine.history.turns
    focus = tmp_path / "draft.md"
    focus.write_text("# Draft\n\nNeeds revision.", encoding="utf-8")
    chat_console = Console(record=True)

    _handle_chat_slash_command(f"/focus {focus}", engine, chat_console)

    document = engine.get_focus_document()
    assert document is not None
    assert document.path == focus.resolve()
    assert "Needs revision" in document.content
    assert len(engine.history.turns) == 0
    assert "Focus document loaded" in chat_console.export_text()


def test_focus_slash_output_and_off(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    focus = tmp_path / "draft.md"
    focus.write_text("body", encoding="utf-8")
    output = tmp_path / "edited.md"
    chat_console = Console(record=True)

    _handle_chat_slash_command(f"/focus {focus}", engine, chat_console)
    _handle_chat_slash_command(f"/focus output {output}", engine, chat_console)

    document = engine.get_focus_document()
    assert document is not None
    assert document.output_path == output.resolve()

    engine.inject_turn("revise", "edited body")
    _handle_chat_slash_command("/focus off", engine, chat_console)

    assert engine.get_focus_document() is None
    assert len(engine.history.turns) == 0
    assert engine.budget.context_snapshot_tokens == 0


def test_clear_restores_context_budget_after_focus_off(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.update_context("ambient context")
    assert engine.budget.context_snapshot_tokens > 0
    focus = tmp_path / "draft.md"
    engine.set_focus_document(FocusDocument(path=focus, label="draft.md", content="body"))
    assert engine.budget.context_snapshot_tokens == 0

    engine.clear_focus_document()
    engine.clear(ClearStrategy.SOFT)

    assert engine.budget.context_snapshot_tokens > 0
