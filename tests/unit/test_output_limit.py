"""Tests for model-agnostic output-length management (output_limit module)."""

from __future__ import annotations

from engineering_hub.journaler.context_manager import (
    ContextCompressor,
    ConversationHistory,
)
from engineering_hub.journaler.output_limit import (
    OutputLimitConfig,
    OutputLimitOrchestrator,
    build_continuation_prompt,
    compute_headroom,
    detect_truncation,
    has_open_code_fence,
    looks_truncated,
    make_outcome,
    resolve_per_pass,
    split_thinking_answer,
    stitch_with_overlap,
)


def _est(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_from_raw_ignores_unknown_and_validates() -> None:
    cfg = OutputLimitConfig.from_raw(
        {"policy": "auto_continue", "bogus": 1, "max_output_tokens": 10}
    )
    assert cfg.policy == "auto_continue"
    # Below the 256 floor, clamped up.
    assert cfg.max_output_tokens == 256


def test_config_invalid_policy_falls_back() -> None:
    cfg = OutputLimitConfig.from_raw({"policy": "nonsense"})
    assert cfg.policy == "prompt"


# ---------------------------------------------------------------------------
# Headroom math
# ---------------------------------------------------------------------------


def test_compute_headroom_and_clamp() -> None:
    # prompt 30k, window 32k, safety 256 -> ~1.7k headroom
    headroom = compute_headroom(window_size=32768, prompt_tokens=30452, safety_tokens=256)
    assert headroom == 32768 - 30452 - 256
    effective = resolve_per_pass(requested=16384, headroom=headroom)
    assert effective == headroom  # clamped down to real headroom
    assert effective < 16384


def test_resolve_per_pass_no_headroom() -> None:
    assert resolve_per_pass(requested=8192, headroom=0) == 0
    assert resolve_per_pass(requested=8192, headroom=-100) == 0


# ---------------------------------------------------------------------------
# Truncation detection
# ---------------------------------------------------------------------------


def test_open_code_fence_detection() -> None:
    assert has_open_code_fence("```python\nx = 1") == "python"
    assert has_open_code_fence("```python\nx=1\n```") is None


def test_looks_truncated_open_fence() -> None:
    assert looks_truncated("here is code:\n```python\nx = 1")


def test_detect_truncation_reasons() -> None:
    assert (
        detect_truncation(
            text="done.",
            tokens_generated=10,
            effective_max=100,
            headroom_exhausted=False,
            use_heuristics=True,
        )
        == "complete"
    )
    assert (
        detect_truncation(
            text="text",
            tokens_generated=100,
            effective_max=100,
            headroom_exhausted=False,
            use_heuristics=False,
        )
        == "max_tokens"
    )
    assert (
        detect_truncation(
            text="text",
            tokens_generated=5,
            effective_max=100,
            headroom_exhausted=True,
            use_heuristics=False,
        )
        == "headroom"
    )


# ---------------------------------------------------------------------------
# Thinking / answer splitting (the terminal-3.txt scenario)
# ---------------------------------------------------------------------------


def test_split_thinking_open_block_is_thinking_phase() -> None:
    text = "<think>Let me compute the table. Wait, is this accurate"
    thinking, answer, phase = split_thinking_answer(text)
    assert phase == "thinking"
    assert "Wait, is this accurate" in thinking
    assert answer == ""


def test_split_thinking_closed_block_is_answer_phase() -> None:
    text = "<think>reasoning here</think>\nHere is the answer."
    thinking, answer, phase = split_thinking_answer(text)
    assert phase == "answer"
    assert "reasoning here" in thinking
    assert "Here is the answer." in answer


def test_split_no_tags_is_unknown() -> None:
    thinking, answer, phase = split_thinking_answer("just an answer")
    assert phase == "unknown"
    assert answer == "just an answer"
    assert thinking == ""


def test_make_outcome_thinking_cut() -> None:
    outcome = make_outcome(
        text="<think>step 1, step 2, still reasoning",
        tokens_generated=100,
        effective_max=100,
        headroom_exhausted=False,
        use_heuristics=True,
        overlap_chars=50,
    )
    assert outcome.phase == "thinking"
    assert outcome.truncated
    assert outcome.reason == "max_tokens"
    assert outcome.anchor_tail  # anchored on reasoning tail


# ---------------------------------------------------------------------------
# Continuation prompt (natural seam + thinking-cut force-answer)
# ---------------------------------------------------------------------------


def test_continuation_prompt_thinking_forces_answer() -> None:
    prompt = build_continuation_prompt(
        phase="thinking",
        intent="parametric sweep table",
        brief="- f_c = 486 Hz\n- four length rows",
        anchor_tail="Wait, is this accurate",
        open_fence_lang=None,
        force_answer_on_thinking_cut=True,
    )
    assert "FINAL answer" in prompt
    assert "parametric sweep table" in prompt
    assert "486 Hz" in prompt
    # Must not ask it to keep reasoning.
    assert "continue your previous response" not in prompt.lower()


def test_continuation_prompt_answer_resumes_at_anchor() -> None:
    prompt = build_continuation_prompt(
        phase="answer",
        intent="x",
        brief="",
        anchor_tail="the last words",
        open_fence_lang="python",
        force_answer_on_thinking_cut=True,
    )
    assert "the last words" in prompt
    assert "code block" in prompt  # open fence noted


# ---------------------------------------------------------------------------
# Seam de-duplication
# ---------------------------------------------------------------------------


def test_stitch_removes_duplicated_anchor() -> None:
    prev = "The quick brown fox jumps over the lazy dog."
    nxt = "the lazy dog. Then it ran away."
    merged = stitch_with_overlap(prev, nxt, "the lazy dog.")
    assert merged.count("the lazy dog.") == 1
    assert "ran away" in merged


def test_stitch_overlap_prefix_suffix() -> None:
    prev = "alpha beta gamma"
    nxt = "beta gamma delta"
    merged = stitch_with_overlap(prev, nxt, "zzz")
    assert merged == "alpha beta gamma delta"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class _ScriptedGen:
    """Records calls and returns scripted (text, tokens) pairs."""

    def __init__(self, responses: list[str], tokens: int = 100) -> None:
        self._responses = list(responses)
        self._tokens = tokens
        self.calls: list[tuple[str | None, str, int]] = []

    def __call__(
        self, continuation_prompt: str | None, accumulated: str, max_tokens: int
    ) -> tuple[str, int]:
        self.calls.append((continuation_prompt, accumulated, max_tokens))
        text = self._responses.pop(0) if self._responses else "done."
        return text, self._tokens


def _make_orch(
    cfg: OutputLimitConfig,
    gen: _ScriptedGen,
    *,
    prompt_tokens: int = 1000,
    window: int = 32768,
    choice=None,
    summarize_history=lambda: "[compressed]",
    summarize_text=lambda t: "summary-of-partial",
    build_brief=lambda intent, partial: f"brief:{intent}",
) -> OutputLimitOrchestrator:
    return OutputLimitOrchestrator(
        cfg,
        window_size=window,
        requested_max_tokens=cfg.max_output_tokens,
        generate=gen,
        prompt_tokens=lambda: prompt_tokens,
        estimate_tokens=_est,
        summarize_history=summarize_history,
        summarize_text=summarize_text,
        build_brief=build_brief,
        choice_provider=choice,
    )


def test_policy_stop_returns_partial_without_retry() -> None:
    cfg = OutputLimitConfig(policy="stop")
    gen = _ScriptedGen(["partial text```python\nx=1"])  # truncated (open fence)
    orch = _make_orch(cfg, gen)
    result = orch.run("intent")
    assert result.passes == 1
    assert len(gen.calls) == 1
    assert any("incomplete" in a for a in result.actions)


def test_policy_auto_continue_concatenates() -> None:
    cfg = OutputLimitConfig(policy="auto_continue", max_continuation_passes=3)
    # First pass truncated, second completes.
    gen = _ScriptedGen(["Part one, ending mid", "Part two complete."])
    orch = _make_orch(cfg, gen)
    result = orch.run("intent")
    assert result.passes == 2
    assert "Part one" in result.text
    assert "Part two complete." in result.text


def test_policy_auto_continue_respects_pass_cap() -> None:
    cfg = OutputLimitConfig(policy="auto_continue", max_continuation_passes=1)
    # Always truncated (open fence each time).
    gen = _ScriptedGen(["a ```py\n1", "b ```py\n2", "c ```py\n3"])
    orch = _make_orch(cfg, gen)
    result = orch.run("intent")
    # 1 initial + 1 continuation = 2 generate calls; capped.
    assert len(gen.calls) == 2
    assert any("incomplete" in a for a in result.actions)


def test_policy_prompt_summarize_both_invokes_summaries() -> None:
    calls = {"history": 0, "text": 0}

    def _sum_hist() -> str:
        calls["history"] += 1
        return "[compressed]"

    def _sum_text(t: str) -> str:
        calls["text"] += 1
        return "partial-summary"

    cfg = OutputLimitConfig(policy="prompt")
    gen = _ScriptedGen(["truncated start```py\n1", "final answer."])
    orch = _make_orch(
        cfg,
        gen,
        choice=lambda outcome: "summarize_both",
        summarize_history=_sum_hist,
        summarize_text=_sum_text,
    )
    result = orch.run("intent")
    assert calls["history"] == 1
    assert calls["text"] == 1
    assert "final answer." in result.text


def test_prompt_policy_without_provider_defaults_to_stop() -> None:
    cfg = OutputLimitConfig(policy="prompt")
    gen = _ScriptedGen(["truncated```py\n1"])
    orch = _make_orch(cfg, gen, choice=None)
    result = orch.run("intent")
    assert result.passes == 1


def test_headroom_first_summarize_before_continue() -> None:
    """When headroom is below min_output_tokens, summary runs before continuing."""
    order: list[str] = []

    def _sum_hist() -> str:
        order.append("summarize")
        return "[compressed]"

    class _Gen(_ScriptedGen):
        def __call__(self, cont, acc, mx):  # type: ignore[override]
            order.append("generate")
            return super().__call__(cont, acc, mx)

    cfg = OutputLimitConfig(
        policy="auto_continue",
        max_continuation_passes=1,
        min_output_tokens=500,
        summarize_before_continue=True,
        check_before_call=False,
    )
    gen = _Gen(["first truncated", "second done."])
    # prompt_tokens high enough that headroom < min_output_tokens (window-prompt-safety).
    orch = _make_orch(cfg, gen, prompt_tokens=32200, summarize_history=_sum_hist)
    orch.run("intent")
    # First generate, then a summarize before the continuation generate.
    assert order[0] == "generate"
    assert "summarize" in order[1:]
    assert order.index("summarize") < order.index("generate", 1)


def test_no_truncation_single_pass() -> None:
    cfg = OutputLimitConfig(policy="auto_continue")
    gen = _ScriptedGen(["A complete sentence."])
    orch = _make_orch(cfg, gen)
    result = orch.run("intent")
    assert result.passes == 1
    assert result.text == "A complete sentence."


# ---------------------------------------------------------------------------
# Compression edge fix (history.turns == keep_recent)
# ---------------------------------------------------------------------------


def test_compression_forces_at_keep_recent_boundary() -> None:
    history = ConversationHistory(max_turns=40, max_tokens=1_000_000)
    for i in range(8):
        history.add("user", f"message number {i} " * 20)
        # add returns turn; we want 8 turns total -> use 4 user + 4 assistant
    # Rebuild as 8 turns explicitly.
    history.turns.clear()
    for i in range(8):
        role = "user" if i % 2 == 0 else "assistant"
        history.add(role, f"exchange {i} with some content " * 10)

    compressor = ContextCompressor(
        engine_call=lambda prompt, max_tokens: "SUMMARY",
        keep_recent=8,
    )
    # Without force: no-op at the boundary.
    assert not compressor.compress(history).compressed
    # With force: compresses the older half.
    result = compressor.compress(history, force=True)
    assert result.compressed
    assert result.turns_compressed >= 1
    assert len(history.turns) < 8
