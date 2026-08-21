"""Tests for the interactive output-limit recovery menu."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from rich.console import Console

from engineering_hub.cli import _interactive_output_choice
from engineering_hub.journaler.output_limit import GenerationOutcome


def _outcome(*, phase: str = "answer") -> GenerationOutcome:
    return GenerationOutcome(
        text="partial answer that was cut",
        answer_text="partial answer that was cut",
        thinking_text="",
        phase=phase,  # type: ignore[arg-type]
        truncated=True,
        reason="heuristic",
        tokens_generated=100,
        effective_max=8192,
    )


def test_interactive_output_choice_renders_numbered_keys() -> None:
    """Rich must not strip choice labels (regression: [s]/[c]/[f] were styles)."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=100, markup=True)
    provider = _interactive_output_choice(console)

    with patch("builtins.input", return_value="3"):
        action = provider(_outcome())

    rendered = buf.getvalue()
    assert action == "continue_same"
    assert "[1] Summarize history and retry" in rendered
    assert "[2] Summarize history + partial answer, then retry" in rendered
    assert "[3] Continue (same turn)" in rendered
    assert "[4] Continue (new follow-up turn)" in rendered
    assert "[Enter] Stop and keep partial" in rendered
    # Legacy letter-key form must not appear as bare Rich style tags.
    assert "[s] Summarize" not in rendered
    assert "Choice [s/S/c/f" not in rendered
    assert "1=summarize history" in rendered


def test_interactive_output_choice_accepts_numbers_and_legacy_letters() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=100)
    provider = _interactive_output_choice(console)

    cases = [
        ("1", "summarize_history"),
        ("2", "summarize_both"),
        ("3", "continue_same"),
        ("4", "continue_followup"),
        ("s", "summarize_history"),
        ("S", "summarize_both"),
        ("c", "continue_same"),
        ("f", "continue_followup"),
        ("", "stop"),
        ("x", "stop"),
    ]
    for typed, expected in cases:
        with patch("builtins.input", return_value=typed):
            assert provider(_outcome()) == expected
