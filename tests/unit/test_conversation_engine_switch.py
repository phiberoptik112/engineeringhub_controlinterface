"""Tests for ConversationEngine session swap (/convo)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_hub.journaler.context_manager import PressureConfig
from engineering_hub.journaler.conversation_store import ConversationStore
from engineering_hub.journaler.daemon import ConversationsConfig
from engineering_hub.journaler.engine import ConversationEngine


class _FakeBackend:
    def chat(self, messages, max_tokens, max_thinking_tokens=0):
        return "ok"


def _engine(tmp_path: Path) -> ConversationEngine:
    return ConversationEngine(
        backend=_FakeBackend(),  # type: ignore[arg-type]
        system_prompt="test",
        log_dir=tmp_path,
        max_history=10,
        pressure_config=PressureConfig(max_history_turns=10),
    )


def test_switch_session_restores_history_and_clears_files(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conv_a = store.create("Alpha")
    conv_b = store.create("Beta")

    jsonl_a = store.jsonl_path(conv_a.id)
    jsonl_a.write_text(
        '{"timestamp": "2026-01-01T10:00:00", "role": "user", "content": "hello"}\n'
        '{"timestamp": "2026-01-01T10:00:01", "role": "assistant", "content": "hi"}\n',
        encoding="utf-8",
    )

    engine = _engine(tmp_path)
    cfg = ConversationsConfig(max_restore_history_turns=20)
    engine.attach_conversation_store(store, cfg)
    engine.switch_session(conv_a)

    assert engine.session_id == conv_a.id
    assert len(engine.history.turns) == 2
    assert engine._log_file == jsonl_a

    doc = tmp_path / "note.md"
    doc.write_text("# Note", encoding="utf-8")
    ok, _ = engine.load_file(doc)
    assert ok
    assert engine.list_loaded_files()

    msg = engine.switch_session(conv_b)
    assert engine.session_id == conv_b.id
    assert engine.list_loaded_files() == []
    assert "Beta" in msg

    engine.history.add("user", "new turn")
    engine.switch_session(conv_a)
    assert len(engine.history.turns) == 2
    store.close()


def test_restore_session_files(tmp_path: Path) -> None:
    doc = tmp_path / "spec.md"
    doc.write_text("content", encoding="utf-8")

    store = ConversationStore(tmp_path)
    conv = store.create("With files")
    store.write_manifest(conv.id, file_manifest=[str(doc.resolve())])

    engine = _engine(tmp_path)
    engine.attach_conversation_store(store, ConversationsConfig())
    engine.switch_session(conv)

    loaded, msg = engine.restore_session_files()
    assert loaded == 1
    assert "Restored" in msg
    assert engine.list_loaded_files()
    store.close()


def test_save_session_writes_topic_back(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conv = store.create("Topic test")
    engine = _engine(tmp_path)
    engine.attach_conversation_store(store, ConversationsConfig())
    engine.switch_session(conv)
    engine.topic_tracker.current_topic = "communications"
    engine.save_session()
    refreshed = store.get(conv.id)
    assert refreshed is not None
    assert refreshed.topic == "communications"
    store.close()
