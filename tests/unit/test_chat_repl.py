"""Tests for journaler chat REPL helpers."""

from __future__ import annotations

import json
from pathlib import Path

from engineering_hub.journaler.chat_repl import extract_user_prompts_from_jsonl_tail


def test_extract_user_prompts_from_jsonl_tail_orders_and_caps(tmp_path: Path) -> None:
    log = tmp_path / "conversation.jsonl"
    lines = []
    for i in range(60):
        lines.append(json.dumps({"role": "user", "content": f"msg{i}"}))
        lines.append(json.dumps({"role": "assistant", "content": f"rep{i}"}))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    got = extract_user_prompts_from_jsonl_tail(log, max_user_prompts=5)
    assert got == ["msg55", "msg56", "msg57", "msg58", "msg59"]


def test_extract_user_prompts_dedupes_consecutive(tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    entries = [
        {"role": "user", "content": "same"},
        {"role": "user", "content": "same"},
        {"role": "user", "content": "other"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    got = extract_user_prompts_from_jsonl_tail(log, max_user_prompts=10)
    assert got == ["same", "other"]


def test_extract_user_prompts_flattens_multiline(tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    log.write_text(
        json.dumps({"role": "user", "content": "line one\nline two"}) + "\n",
        encoding="utf-8",
    )
    got = extract_user_prompts_from_jsonl_tail(log)
    assert got == ["line one line two"]


def test_extract_user_prompts_tail_only_large_file(tmp_path: Path) -> None:
    log = tmp_path / "big.jsonl"
    filler = "x" * 200
    # Old user message only in first chunk (will be cut off by small tail_bytes)
    head = json.dumps({"role": "user", "content": "old_hidden"}) + "\n"
    head += "\n".join(json.dumps({"role": "system", "content": filler}) for _ in range(2000)) + "\n"
    tail_user = json.dumps({"role": "user", "content": "recent"}) + "\n"
    log.write_text(head + tail_user, encoding="utf-8")

    got = extract_user_prompts_from_jsonl_tail(
        log, max_user_prompts=10, tail_bytes=8000
    )
    assert got == ["recent"]
    assert "old_hidden" not in got


def test_extract_missing_file(tmp_path: Path) -> None:
    got = extract_user_prompts_from_jsonl_tail(tmp_path / "nope.jsonl")
    assert got == []
