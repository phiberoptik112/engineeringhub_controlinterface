"""Tests for ConversationStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_hub.journaler.conversation_store import (
    ConversationStore,
    slugify,
)


def test_slugify_basic() -> None:
    assert slugify("Reverb Calc Review") == "reverb-calc-review"
    assert slugify("  Foo!!! Bar  ") == "foo-bar"


def test_create_list_get(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conv = store.create("Test Chat", project_id=42, topic="standards_ASTM_E336")
    assert conv.id == "test-chat"
    assert conv.title == "Test Chat"
    assert conv.project_id == 42
    assert conv.topic == "standards_ASTM_E336"

    fetched = store.get("test-chat")
    assert fetched is not None
    assert fetched.title == "Test Chat"

    all_convs = store.list()
    assert len(all_convs) == 1
    store.close()


def test_slug_collision(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    c1 = store.create("My Topic")
    c2 = store.create("My Topic")
    assert c1.id == "my-topic"
    assert c2.id == "my-topic-2"
    store.close()


def test_archive_delete(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conv = store.create("Archive Me")
    assert store.archive(conv.id) is True
    assert store.list() == []
    assert len(store.list(include_archived=True)) == 1

    assert store.delete(conv.id) is True
    assert store.get(conv.id) is None
    assert not store.conv_dir(conv.id).exists()
    store.close()


def test_seed_from_legacy_jsonl(tmp_path: Path) -> None:
    legacy = tmp_path / "conversation.jsonl"
    legacy.write_text(
        '{"timestamp": "2026-01-01T12:00:00", "role": "user", "content": "hello"}\n'
        '{"timestamp": "2026-01-01T12:00:01", "role": "assistant", "content": "hi"}\n',
        encoding="utf-8",
    )
    store = ConversationStore(tmp_path)
    conv = store.seed_from_legacy_jsonl(legacy)
    assert conv.id == "default"
    assert conv.title == "Default"
    assert conv.turn_count == 2
    assert store.jsonl_path("default").is_file()
    turns = store.read_turns("default")
    assert len(turns) == 2
    store.close()


def test_manifest_and_update(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conv = store.create("Manifest Test")
    store.write_manifest(conv.id, file_manifest=["/tmp/a.md", "/tmp/b.md"])
    updated = store.update_meta(
        conv.id,
        turn_count=5,
        topic="communications",
        file_manifest=["/tmp/a.md"],
    )
    assert updated is not None
    assert updated.turn_count == 5
    assert updated.topic == "communications"
    assert updated.file_manifest == ["/tmp/a.md"]
    store.close()


def test_active_jsonl_path(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conv = store.create("Active")
    store.set_active(conv.id)
    assert store.active_jsonl_path() == store.jsonl_path(conv.id)
    store.close()


def test_ensure_initialized_seeds_legacy(tmp_path: Path) -> None:
    legacy = tmp_path / "conversation.jsonl"
    legacy.write_text(
        '{"timestamp": "t", "role": "user", "content": "x"}\n',
        encoding="utf-8",
    )
    store = ConversationStore(tmp_path)
    conv = store.ensure_initialized()
    assert conv.id == "default"
    assert store.get_active_id() == "default"
    store.close()


def test_find_by_title_fragment(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    store.create("Wall assembly STC")
    store.create("Fee proposal draft")
    hits = store.find_by_title_fragment("wall")
    assert len(hits) == 1
    assert hits[0].title == "Wall assembly STC"
    store.close()
