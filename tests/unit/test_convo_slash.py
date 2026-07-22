"""Tests for /convo slash parsing."""

from __future__ import annotations

from engineering_hub.journaler.convo_slash import parse_convo_new_args


def test_parse_convo_new_minimal() -> None:
    pid, topic, title = parse_convo_new_args(["new", "My", "Chat"])
    assert pid is None
    assert topic is None
    assert title == "My Chat"


def test_parse_convo_new_with_flags() -> None:
    pid, topic, title = parse_convo_new_args(
        ["new", "--project", "42", "--topic", "standards_ASTM_E336", "Reverb", "review"]
    )
    assert pid == 42
    assert topic == "standards_ASTM_E336"
    assert title == "Reverb review"
