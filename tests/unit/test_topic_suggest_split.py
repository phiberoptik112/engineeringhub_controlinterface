"""Topic-shift suggest-split when a named conversation is active."""

from __future__ import annotations

from engineering_hub.journaler.context_manager import (
    ContextCompressor,
    ContextPressureManager,
    ConversationHistory,
    PressureConfig,
    TopicTracker,
    TokenBudget,
)


def test_suggest_split_instead_of_auto_compress() -> None:
    history = ConversationHistory(max_turns=20)
    for i in range(6):
        history.add("user", f"message {i}")
        history.add("assistant", f"reply {i}")

    compressor = ContextCompressor(engine_call=lambda p, t: "summary")
    tracker = TopicTracker(shift_threshold=1)
    tracker.current_topic = "topic_a"

    budget = TokenBudget(
        window_size=32768,
        system_prompt_tokens=100,
        context_snapshot_tokens=0,
        history_tokens=history.total_tokens,
    )
    mgr = ContextPressureManager(
        budget=budget,
        history=history,
        compressor=compressor,
        topic_tracker=tracker,
        config=PressureConfig(auto_clear_on_topic_shift=True),
    )
    mgr.named_conversation_active = True
    mgr.suggest_split_on_topic_shift = True

    actions = mgr.post_call_check("new subject", "TOPIC: topic_b")
    assert any("/convo new" in a for a in actions)
    assert len(history.turns) == 12
