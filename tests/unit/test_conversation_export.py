"""Tests for journaler conversation export to org."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering_hub.journaler.conversation_export import (
    build_summarize_prompt,
    load_transcript,
    postprocess_model_org,
    render_raw_org,
    transcript_to_plain_text,
)


def test_load_transcript_skips_bad_lines(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    p.write_text(
        "\n".join(
            [
                "not json",
                json.dumps(
                    {"timestamp": "t1", "role": "user", "content": "hi"}
                ),
                json.dumps({"role": "assistant"}),
                json.dumps(
                    {"timestamp": "t2", "role": "assistant", "content": "yo", "archived": True}
                ),
            ]
        ),
        encoding="utf-8",
    )
    turns = load_transcript(p)
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[1].get("archived") is True


def test_load_transcript_missing_file(tmp_path: Path) -> None:
    assert load_transcript(tmp_path / "nope.jsonl") == []


def test_render_raw_org_structure() -> None:
    turns = [
        {"timestamp": "2026-04-06T12:00:00", "role": "user", "content": "Hello"},
        {
            "timestamp": "2026-04-06T12:00:01",
            "role": "assistant",
            "content": "Line with * org star",
        },
        {
            "timestamp": "2026-04-06T12:00:02",
            "role": "system",
            "content": "summary",
        },
    ]
    org = render_raw_org(turns, title="Test export")
    assert "* Test export" in org
    assert "** Turn 1: USER" in org
    assert "** Turn 2: ASSISTANT" in org
    assert "** Turn 3: SYSTEM" in org
    assert "#+begin_src text" in org
    assert "#+end_src" in org
    assert "Line with * org star" in org


def test_render_raw_org_escapes_src_directive() -> None:
    turns = [
        {
            "timestamp": "t",
            "role": "user",
            "content": "#+end_src\nbody",
        }
    ]
    org = render_raw_org(turns)
    assert " #+end_src" in org


def test_transcript_to_plain_text() -> None:
    turns = [
        {"timestamp": "t", "role": "user", "content": "a"},
        {"timestamp": "t2", "role": "assistant", "content": "b", "archived": True},
    ]
    text = transcript_to_plain_text(turns)
    assert "USER [t]:" in text
    assert "ASSISTANT (archived) [t2]:" in text
    assert "a" in text and "b" in text


def test_build_summarize_prompt_contains_transcript() -> None:
    p = build_summarize_prompt("USER: ping")
    assert "USER: ping" in p
    assert "* Summary" in p
    assert "Open TODOs" in p


@pytest.mark.parametrize(
    "raw,expected_substr",
    [
        ("* Summary\n\nHi\n\n* Open TODOs\n\n- [ ] (none)\n", "* Summary"),
        (
            "```\n* Summary\n\nx\n```\n",
            "* Summary",
        ),
    ],
)
def test_postprocess_model_org(raw: str, expected_substr: str) -> None:
    out = postprocess_model_org(raw)
    assert expected_substr in out
    assert not out.strip().startswith("```")
