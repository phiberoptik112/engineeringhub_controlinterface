"""Tests for context-aware /load caps and token budget fields."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from engineering_hub.journaler.context_manager import TokenBudget, estimate_tokens
from engineering_hub.journaler.engine import ConversationEngine, LoadFileBudgetConfig


def test_token_budget_used_includes_loaded_and_corpus() -> None:
    b = TokenBudget(
        window_size=1000,
        system_prompt_tokens=100,
        context_snapshot_tokens=50,
        history_tokens=200,
        loaded_files_tokens=80,
        corpus_injection_tokens=20,
        reserved_for_generation=100,
    )
    assert b.used == 100 + 50 + 200 + 80 + 20
    assert b.available == 1000 - 450 - 100


def test_load_file_budget_config_rejects_bad_fraction() -> None:
    with pytest.raises(ValueError, match="max_context_fraction"):
        LoadFileBudgetConfig(max_context_fraction=0.0)
    with pytest.raises(ValueError, match="max_context_fraction"):
        LoadFileBudgetConfig(max_context_fraction=1.5)


def test_dynamic_load_truncates_to_small_window(tmp_path: Path) -> None:
    backend = MagicMock()
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    budget = LoadFileBudgetConfig(
        max_context_fraction=0.5,
        max_chars_absolute=50_000,
        min_chars=100,
        slack_tokens=0,
    )
    engine = ConversationEngine(
        backend,
        "sys",
        log_dir,
        max_history=5,
        model_context_window=12_000,
        load_file_budget=budget,
    )
    huge = "a" * 20_000
    p = tmp_path / "big.md"
    p.write_text(huge, encoding="utf-8")

    ok, msg = engine.load_file(p, extensions=frozenset({".md"}))
    assert ok is True
    loaded = engine._loaded_files["big.md"]
    assert len(loaded) < len(huge)
    cap = len(loaded)
    assert "truncated" in msg.lower()
    assert cap <= 20_000
    engine._sync_loaded_files_budget()
    assert engine.budget.loaded_files_tokens == estimate_tokens(engine._loaded_files_section())


def test_load_directory_shares_budget(tmp_path: Path) -> None:
    backend = MagicMock()
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    budget = LoadFileBudgetConfig(
        max_context_fraction=0.99,
        max_chars_absolute=400,
        min_chars=0,
        slack_tokens=0,
    )
    engine = ConversationEngine(
        backend,
        "x",
        log_dir,
        max_history=2,
        max_tokens=256,
        model_context_window=2500,
        load_file_budget=budget,
    )
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.md").write_text("b" * 500, encoding="utf-8")
    (d / "c.md").write_text("d" * 500, encoding="utf-8")

    ok, msg = engine.load_directory(d, extensions=frozenset({".md"}), recursive=False)
    assert ok is True
    total = sum(len(t) for t in engine._loaded_files.values())
    assert total < 1000
    assert "a.md" in engine._loaded_files
    assert "c.md" in engine._loaded_files or "skipped" in msg.lower()


def test_clear_loaded_files_resets_budget(tmp_path: Path) -> None:
    backend = MagicMock()
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    engine = ConversationEngine(
        backend, "s", log_dir, model_context_window=4096, max_tokens=512
    )
    p = tmp_path / "f.md"
    p.write_text("hello", encoding="utf-8")
    engine.load_file(p, extensions=frozenset({".md"}))
    assert engine.budget.loaded_files_tokens > 0
    engine.clear_loaded_files()
    assert engine.budget.loaded_files_tokens == 0
    assert not engine._loaded_files


def test_load_file_no_budget_returns_false(tmp_path: Path) -> None:
    backend = MagicMock()
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    engine = ConversationEngine(
        backend,
        "s" * 9000,
        log_dir,
        max_history=2,
        model_context_window=500,
        load_file_budget=LoadFileBudgetConfig(
            max_context_fraction=0.1,
            max_chars_absolute=50_000,
            min_chars=0,
            slack_tokens=400,
        ),
    )
    p = tmp_path / "n.md"
    p.write_text("x", encoding="utf-8")
    ok, msg = engine.load_file(p, extensions=frozenset({".md"}))
    assert ok is False
    assert "budget" in msg.lower()


def test_explicit_max_chars_bypasses_dynamic(tmp_path: Path) -> None:
    backend = MagicMock()
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    engine = ConversationEngine(
        backend,
        "x",
        log_dir,
        model_context_window=500,
        load_file_budget=LoadFileBudgetConfig(
            max_context_fraction=0.01,
            max_chars_absolute=20_000,
            min_chars=0,
            slack_tokens=0,
        ),
    )
    p = tmp_path / "t.md"
    p.write_text("z" * 800, encoding="utf-8")
    ok, _msg = engine.load_file(p, max_chars=600, extensions=frozenset({".md"}))
    assert ok is True
    assert len(engine._loaded_files["t.md"]) == 600


def test_chat_clears_corpus_tokens_after_turn(tmp_path: Path) -> None:
    backend = MagicMock()
    backend.chat = MagicMock(return_value="ok")
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    corpus = MagicMock()
    corpus.is_available = MagicMock(return_value=True)
    corpus.search = MagicMock(return_value=[{"x": 1}])
    corpus.format_for_context = MagicMock(return_value="RAG block" * 50)

    engine = ConversationEngine(
        backend,
        "system prompt",
        log_dir,
        model_context_window=8192,
        corpus_service=corpus,
    )
    engine.chat("hi")
    assert engine.budget.corpus_injection_tokens == 0


def test_chat_sets_corpus_tokens_before_pressure(tmp_path: Path) -> None:
    backend = MagicMock()
    backend.chat = MagicMock(return_value="ok")
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    corpus = MagicMock()
    corpus.is_available = MagicMock(return_value=True)
    corpus.search = MagicMock(return_value=[{"x": 1}])
    rag = "corpus " * 100
    corpus.format_for_context = MagicMock(return_value=rag)

    engine = ConversationEngine(
        backend,
        "s",
        log_dir,
        model_context_window=8192,
        corpus_service=corpus,
    )
    orig_pre = engine.pressure_manager.pre_call_check

    def wrapped() -> list:
        assert engine.budget.corpus_injection_tokens == estimate_tokens(rag)
        return orig_pre()

    engine.pressure_manager.pre_call_check = wrapped  # type: ignore[method-assign]
    engine.chat("q")
