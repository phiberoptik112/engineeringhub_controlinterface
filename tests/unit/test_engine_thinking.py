"""Tests for thinking-model budgets, truncation detection, and history stripping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from engineering_hub.journaler.engine import ConversationalMLXBackend, ConversationEngine
from engineering_hub.journaler.thinking import (
    ThinkTagTracker,
    prompt_primes_thinking,
    strip_think_blocks,
)

# ---------------------------------------------------------------------------
# strip_think_blocks
# ---------------------------------------------------------------------------


def test_strip_closed_think_block() -> None:
    assert strip_think_blocks("<think>reasoning here</think>Final answer.") == "Final answer."


def test_strip_multiple_think_blocks() -> None:
    text = "<think>a</think>One. <think>b</think>Two."
    assert strip_think_blocks(text) == "One. Two."


def test_strip_unclosed_think_block_removes_tail() -> None:
    assert strip_think_blocks("Intro. <think>truncated mid-thought") == "Intro."


def test_strip_no_think_block_is_identity() -> None:
    assert strip_think_blocks("Plain answer.") == "Plain answer."


def test_strip_thinking_only_output_is_empty() -> None:
    assert strip_think_blocks("<think>never closed") == ""


def test_strip_orphan_close_tag_from_primed_template() -> None:
    """Qwen3.5/3.6 templates prime the prompt with <think>, so the output
    contains only the closing tag."""
    text = "deliberation goes here\n</think>\n\nFinal answer."
    assert strip_think_blocks(text) == "Final answer."


# ---------------------------------------------------------------------------
# prompt_primes_thinking
# ---------------------------------------------------------------------------


def test_prompt_primes_thinking_qwen_thinking_mode() -> None:
    prompt = "<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\n<think>\n"
    assert prompt_primes_thinking(prompt) is True


def test_prompt_primes_thinking_disabled_mode_is_closed() -> None:
    prompt = (
        "<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\n"
        "<think>\n\n</think>\n\n"
    )
    assert prompt_primes_thinking(prompt) is False


def test_prompt_primes_thinking_no_tags() -> None:
    assert prompt_primes_thinking("<|im_start|>assistant\n") is False


def test_prompt_primes_thinking_prior_turn_blocks_dont_confuse() -> None:
    # A previous assistant turn kept a closed think block; only the trailing
    # unclosed one counts.
    prompt = (
        "<|im_start|>assistant\n<think>\nold\n</think>\n\nanswer<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n"
    )
    assert prompt_primes_thinking(prompt) is True


# ---------------------------------------------------------------------------
# ThinkTagTracker
# ---------------------------------------------------------------------------


def test_tracker_open_and_close() -> None:
    t = ThinkTagTracker()
    t.feed("<think>")
    assert t.open is True
    assert t.saw_think is True
    t.feed("reasoning")
    assert t.open is True
    t.feed("</think>answer")
    assert t.open is False


def test_tracker_tag_split_across_segments() -> None:
    t = ThinkTagTracker()
    for seg in ("<thi", "nk>", "x", "</thi", "nk>"):
        t.feed(seg)
    assert t.open is False
    assert t.saw_think is True


def test_tracker_no_tags() -> None:
    t = ThinkTagTracker()
    t.feed("just a normal answer")
    assert t.open is False
    assert t.saw_think is False


# ---------------------------------------------------------------------------
# _chat_lm streaming budgets (fake mlx_lm)
# ---------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    text: str
    finish_reason: str | None = None


def _make_fake_mlx(tokens: list[str]) -> SimpleNamespace:
    """Fake mlx_lm module: yields one response per token, mirroring
    stream_generate semantics (finish_reason on the last yield only:
    "stop" when the token list is exhausted, "length" at max_tokens)."""

    def stream_generate(model, tokenizer, prompt, max_tokens, **kwargs):
        for i, tok in enumerate(tokens):
            n = i + 1
            if n >= max_tokens and n < len(tokens):
                yield _FakeResponse(text=tok, finish_reason="length")
                return
            if n == len(tokens):
                yield _FakeResponse(text=tok, finish_reason="stop")
                return
            yield _FakeResponse(text=tok, finish_reason=None)

    return SimpleNamespace(stream_generate=stream_generate)


def _make_backend(tokens: list[str]) -> ConversationalMLXBackend:
    backend = object.__new__(ConversationalMLXBackend)
    backend._is_vlm = False
    backend._temp = 0.7
    backend._top_p = 0.9
    backend._min_p = 0.05
    backend._repetition_penalty = 1.1
    backend._repetition_context_size = 20
    backend._enable_thinking = None
    backend._model = object()
    backend._tokenizer = object()
    backend._mlx_lm = _make_fake_mlx(tokens)
    backend._make_sampler = lambda **kw: None
    backend._make_logits_processors = lambda **kw: None
    return backend


def _thinking_tokens(n_think: int, answer: list[str]) -> list[str]:
    return ["<think>"] + ["t"] * n_think + ["</think>"] + answer


def test_thinking_budget_allows_long_reasoning() -> None:
    """Thinking longer than max_tokens completes when a thinking budget exists."""
    tokens = _thinking_tokens(60, ["Final", " answer", "."])
    backend = _make_backend(tokens)
    out = backend._chat_lm("prompt", max_tokens=10, max_thinking_tokens=100)
    assert out.endswith("Final answer.")
    assert "⚠" not in out


def test_no_thinking_budget_reproduces_truncation_with_notice() -> None:
    """Without a thinking budget, long reasoning hits max_tokens — but the
    truncation is now flagged instead of silent."""
    tokens = _thinking_tokens(60, ["Final answer."])
    backend = _make_backend(tokens)
    out = backend._chat_lm("prompt", max_tokens=10, max_thinking_tokens=0)
    assert "stopped mid-thinking" in out
    assert "max_thinking_tokens" in out


def test_thinking_budget_exhausted_mid_think_gets_notice() -> None:
    tokens = _thinking_tokens(500, ["Answer."])
    backend = _make_backend(tokens)
    out = backend._chat_lm("prompt", max_tokens=10, max_thinking_tokens=50)
    assert "stopped mid-thinking" in out


def test_answer_budget_truncation_gets_notice() -> None:
    tokens = ["word "] * 100
    backend = _make_backend(tokens)
    out = backend._chat_lm("prompt", max_tokens=20, max_thinking_tokens=0)
    assert "truncated at the 20-token answer limit" in out
    assert "journaler.max_tokens" in out


def test_natural_stop_returns_clean_text() -> None:
    tokens = ["Hello", " world", "."]
    backend = _make_backend(tokens)
    out = backend._chat_lm("prompt", max_tokens=100, max_thinking_tokens=0)
    assert out == "Hello world."


def test_primed_prompt_counts_untagged_reasoning_as_thinking() -> None:
    """Qwen3.5/3.6: the prompt ends with <think>, so streamed reasoning has no
    opening tag — it must still draw from the thinking budget, not the answer
    budget."""
    tokens = ["t"] * 60 + ["</think>", "Final", " answer", "."]
    backend = _make_backend(tokens)
    primed_prompt = "<|im_start|>assistant\n<think>\n"
    out = backend._chat_lm(primed_prompt, max_tokens=10, max_thinking_tokens=100)
    assert out.endswith("Final answer.")
    assert "⚠" not in out


def test_primed_prompt_truncation_mid_think_gets_thinking_notice() -> None:
    tokens = ["t"] * 500 + ["</think>", "Answer."]
    backend = _make_backend(tokens)
    primed_prompt = "<|im_start|>assistant\n<think>\n"
    out = backend._chat_lm(primed_prompt, max_tokens=10, max_thinking_tokens=50)
    assert "stopped mid-thinking" in out


# ---------------------------------------------------------------------------
# ConversationEngine history stripping
# ---------------------------------------------------------------------------


class _FakeChatBackend:
    def __init__(self, response: str) -> None:
        self._response = response
        self.last_max_thinking_tokens: int | None = None

    def chat(self, messages, max_tokens, max_thinking_tokens=0):
        self.last_max_thinking_tokens = max_thinking_tokens
        return self._response

    def is_loaded(self) -> bool:
        return True


def _make_engine(tmp_path: Path, response: str) -> tuple[ConversationEngine, _FakeChatBackend]:
    backend = _FakeChatBackend(response)
    engine = ConversationEngine(
        backend=backend,  # type: ignore[arg-type]
        system_prompt="You are the Journaler.",
        log_dir=tmp_path,
        max_tokens=1000,
        max_thinking_tokens=5000,
    )
    return engine, backend


def test_engine_strips_thinking_from_history(tmp_path: Path) -> None:
    engine, _ = _make_engine(tmp_path, "<think>secret reasoning</think>Final answer.")
    out = engine.chat("hello")
    assert "Final answer." in out
    assistant_turns = [t for t in engine.history.turns if t.role == "assistant"]
    assert assistant_turns[-1].content == "Final answer."
    assert "secret reasoning" not in assistant_turns[-1].content


def test_engine_thinking_only_response_gets_placeholder_in_history(tmp_path: Path) -> None:
    engine, _ = _make_engine(tmp_path, "<think>ran out of tokens mid-thought")
    engine.chat("hello")
    assistant_turns = [t for t in engine.history.turns if t.role == "assistant"]
    assert "no final answer" in assistant_turns[-1].content


def test_engine_passes_clamped_thinking_budget(tmp_path: Path) -> None:
    engine, backend = _make_engine(tmp_path, "ok")
    engine.chat("hello")
    assert backend.last_max_thinking_tokens is not None
    assert 0 < backend.last_max_thinking_tokens <= 5000
    # Clamped to remaining headroom above the reserved answer budget
    assert backend.last_max_thinking_tokens <= engine.budget.window_size
