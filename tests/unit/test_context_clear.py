from __future__ import annotations

from engineering_hub.journaler.context_manager import (
    ClearStrategy,
    ContextCompressor,
    ConversationHistory,
    TokenBudget,
    execute_clear,
)


def _fill_history(turns: int) -> ConversationHistory:
    history = ConversationHistory(max_turns=40, max_tokens=1_000_000)
    for i in range(turns):
        role = "user" if i % 2 == 0 else "assistant"
        history.add(role, f"exchange {i} with enough content to compress " * 8)
    return history


def test_clear_summarize_uses_force_at_keep_recent_boundary() -> None:
    history = _fill_history(4)
    compressor = ContextCompressor(
        engine_call=lambda prompt, max_tokens: "SUMMARY TEXT",
        keep_recent=8,
    )

    msg = execute_clear(ClearStrategy.SUMMARIZE, history, compressor)

    assert "Compressed" in msg
    assert "summary retained" in msg
    assert len(history.turns) == 1
    assert history.turns[0].role == "system"
    assert history.turns[0].preserved
    assert history.turns[0].content.startswith("[Conversation summary")


def test_clear_summarize_falls_back_to_soft_clear_under_pressure() -> None:
    history = _fill_history(2)
    compressor = ContextCompressor(
        engine_call=lambda prompt, max_tokens: "SUMMARY TEXT",
        keep_recent=8,
    )
    budget = TokenBudget(
        window_size=10_000,
        system_prompt_tokens=2_000,
        context_snapshot_tokens=2_000,
        history_tokens=history.total_tokens,
        loaded_files_tokens=4_500,
        reserved_for_generation=500,
    )

    msg = execute_clear(
        ClearStrategy.SUMMARIZE,
        history,
        compressor,
        budget=budget,
    )

    assert "Could not summarize" in msg
    assert "Cleared 2 turn(s) instead" in msg
    assert "/files clear" in msg
    assert len(history.turns) == 0


def test_clear_summarize_low_pressure_two_turns_unchanged() -> None:
    history = _fill_history(2)
    compressor = ContextCompressor(
        engine_call=lambda prompt, max_tokens: "SUMMARY TEXT",
        keep_recent=8,
    )
    budget = TokenBudget(
        window_size=10_000,
        system_prompt_tokens=500,
        context_snapshot_tokens=500,
        history_tokens=history.total_tokens,
        reserved_for_generation=500,
    )

    msg = execute_clear(
        ClearStrategy.SUMMARIZE,
        history,
        compressor,
        budget=budget,
    )

    assert "Nothing to summarize" in msg
    assert len(history.turns) == 2
