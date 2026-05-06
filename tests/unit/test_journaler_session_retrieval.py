"""Tests for prior Journaler session retrieval."""

from __future__ import annotations

import json
from pathlib import Path

from engineering_hub.journaler.session_retrieval import (
    format_past_session_block,
    references_past_session,
    retrieve_past_sessions,
)


def test_references_past_session_detects_prior_chat_language() -> None:
    assert references_past_session("What did we discuss in the previous chat?")
    assert references_past_session("last time we talked about ASTM E336")
    assert not references_past_session("Summarize ASTM E336 requirements")


def test_retrieve_past_sessions_searches_summaries_and_transcript(tmp_path: Path) -> None:
    summary_dir = tmp_path / "daily_summaries"
    summary_dir.mkdir()
    (summary_dir / "2026-04-20.md").write_text(
        "# Journaler Daily Summary -- 2026-04-20\n\n"
        "We discussed the LVT alert system, driver selection, and next steps.",
        encoding="utf-8",
    )
    transcript = tmp_path / "conversation.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({
                    "timestamp": "2026-04-19T10:00:00",
                    "role": "user",
                    "content": "Let's compare driver selection options for the LVT alert.",
                }),
                json.dumps({
                    "timestamp": "2026-04-19T10:01:00",
                    "role": "assistant",
                    "content": "We narrowed it to three driver paths and a trade-off matrix.",
                }),
            ]
        ),
        encoding="utf-8",
    )

    hits = retrieve_past_sessions(
        "What did we decide last time about LVT driver selection?",
        state_dir=tmp_path,
        max_results=4,
        excerpt_chars=500,
    )
    block = format_past_session_block(hits)

    assert hits
    assert any(hit.source == "daily summary" for hit in hits)
    assert any(hit.source == "raw transcript" for hit in hits)
    assert "Retrieved Past Journaler Conversations" in block
    assert "driver" in block.lower()
