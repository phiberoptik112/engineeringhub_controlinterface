"""Tests for Journaler /history command handling."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from engineering_hub.journaler.chat_server import _handle_history_command


class _Delegator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def delegate(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "agent reviewed history"


def _write_transcript(state_dir: Path) -> None:
    (state_dir / "conversation.jsonl").write_text(
        "\n".join(
            [
                json.dumps({
                    "timestamp": "2026-05-01T09:00:00",
                    "role": "user",
                    "content": "Let's assemble the LVT April timesheet.",
                }),
                json.dumps({
                    "timestamp": "2026-05-01T09:01:00",
                    "role": "assistant",
                    "content": "The complete timesheet totals should align with the invoice.",
                }),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_history_command_returns_matching_excerpts(tmp_path: Path) -> None:
    _write_transcript(tmp_path)
    context = SimpleNamespace(state_dir=tmp_path)

    result = _handle_history_command(
        "/history previous LVT April timesheet",
        delegator=None,
        context=context,  # type: ignore[arg-type]
    )

    assert "Retrieved Past Journaler Conversations" in result
    assert "timesheet" in result.lower()
    assert "/history --agent" in result


def test_history_command_can_dispatch_agent_review(tmp_path: Path) -> None:
    _write_transcript(tmp_path)
    context = SimpleNamespace(state_dir=tmp_path)
    delegator = _Delegator()

    result = _handle_history_command(
        "/history --agent panning-for-gold --backend mlx previous LVT timesheet",
        delegator=delegator,  # type: ignore[arg-type]
        context=context,  # type: ignore[arg-type]
    )

    assert result == "agent reviewed history"
    assert delegator.calls[0]["agent_type"] == "panning-for-gold"
    assert delegator.calls[0]["backend"] == "mlx"
    assert "Retrieved Past Journaler Conversations" in delegator.calls[0]["journaler_context"]
