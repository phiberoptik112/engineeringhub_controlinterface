"""Model-agnostic output-length management for the Journaler chat engine.

When a model's generation is capped (it hits ``max_tokens`` or runs out of
context headroom) the visible answer is often truncated -- sometimes before the
deliverable is even reached, while the model is still "thinking".  This module
provides:

* :class:`OutputLimitConfig` -- user-configurable policy and budgets.
* :class:`GenerationOutcome` -- a phase-aware description of one generation pass.
* Pure helpers for headroom math, truncation detection, thinking/answer
  splitting, and seam de-duplication.
* :class:`OutputLimitOrchestrator` -- the summarize / continue / stop loop that
  picks the thread back up naturally.

The orchestrator is intentionally decoupled from :class:`ConversationEngine`:
all model and context interaction is injected as callables so the logic can be
unit-tested without loading a model.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Policy vocabulary
# ---------------------------------------------------------------------------

Policy = Literal["prompt", "auto_continue", "auto_summarize", "stop"]
ContinueMode = Literal["ask", "same_turn", "follow_up"]
SummarizeScope = Literal["history", "history_and_partial"]
Phase = Literal["thinking", "answer", "unknown"]
TruncationReason = Literal["max_tokens", "headroom", "heuristic", "complete"]

# User-selectable actions (returned by an interactive choice provider).
Action = Literal[
    "summarize_history",
    "summarize_both",
    "continue_same",
    "continue_followup",
    "stop",
]

_VALID_POLICIES: frozenset[str] = frozenset(
    {"prompt", "auto_continue", "auto_summarize", "stop"}
)
_VALID_CONTINUE_MODES: frozenset[str] = frozenset({"ask", "same_turn", "follow_up"})
_VALID_SUMMARIZE_SCOPES: frozenset[str] = frozenset({"history", "history_and_partial"})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class OutputLimitConfig:
    """User-configurable output-length behavior (``journaler.output_limit``)."""

    policy: Policy = "prompt"

    # Generation budget (model agnostic; clamped by real headroom per pass).
    max_output_tokens: int = 8192
    max_continuation_passes: int = 3
    headroom_safety_tokens: int = 256

    # Continue sub-behavior.
    continue_mode: ContinueMode = "ask"

    # Summarize sub-behavior.
    summarize_scope_default: SummarizeScope = "history"

    # Pre-call gating.
    check_before_call: bool = True
    min_output_tokens: int = 512
    truncation_heuristics: bool = True

    # Continuation fidelity.
    continuation_summary_tokens: int = 400
    continuation_overlap_chars: int = 300
    force_answer_on_thinking_cut: bool = True
    summarize_before_continue: bool = True

    @classmethod
    def from_raw(cls, raw: dict | None) -> "OutputLimitConfig":
        """Build a config from a raw YAML mapping, ignoring unknown keys."""
        if not raw:
            return cls()
        allowed = set(cls.__dataclass_fields__)
        cleaned: dict = {}
        for key, value in raw.items():
            if key not in allowed or value is None:
                continue
            cleaned[key] = value
        cfg = cls(**cleaned)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Clamp/normalize fields to safe ranges (lenient: never raises)."""
        if self.policy not in _VALID_POLICIES:
            logger.warning("Invalid output_limit.policy %r; using 'prompt'", self.policy)
            self.policy = "prompt"
        if self.continue_mode not in _VALID_CONTINUE_MODES:
            logger.warning(
                "Invalid output_limit.continue_mode %r; using 'ask'", self.continue_mode
            )
            self.continue_mode = "ask"
        if self.summarize_scope_default not in _VALID_SUMMARIZE_SCOPES:
            self.summarize_scope_default = "history"
        self.max_output_tokens = max(256, int(self.max_output_tokens))
        self.max_continuation_passes = max(0, int(self.max_continuation_passes))
        self.headroom_safety_tokens = max(0, int(self.headroom_safety_tokens))
        self.min_output_tokens = max(64, int(self.min_output_tokens))
        self.continuation_summary_tokens = max(64, int(self.continuation_summary_tokens))
        self.continuation_overlap_chars = max(32, int(self.continuation_overlap_chars))


# ---------------------------------------------------------------------------
# Generation outcome
# ---------------------------------------------------------------------------


@dataclass
class GenerationOutcome:
    """Phase-aware description of a single generation pass."""

    text: str
    answer_text: str
    thinking_text: str
    phase: Phase
    truncated: bool
    reason: TruncationReason
    tokens_generated: int
    effective_max: int
    anchor_tail: str = ""


@dataclass
class OrchestratorResult:
    """Final result of an orchestrated chat turn."""

    text: str
    outcome: GenerationOutcome
    actions: list[str] = field(default_factory=list)
    passes: int = 1


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

# Match a closed thinking block, plus an optional trailing unclosed one.
_THINK_OPEN_RE = re.compile(r"<think>|<thinking>|<reasoning>", re.IGNORECASE)
_THINK_BLOCK_RE = re.compile(
    r"<(think|thinking|reasoning)>(.*?)</\1>", re.IGNORECASE | re.DOTALL
)
_OPEN_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)\s*$")


def split_thinking_answer(text: str) -> tuple[str, str, Phase]:
    """Split *text* into ``(thinking, answer, phase)``.

    ``phase`` reflects where the stream currently sits:

    * ``"thinking"`` -- an unterminated thinking block is open at the end
      (the model was still reasoning when generation stopped).
    * ``"answer"`` -- a thinking block opened and closed, leaving answer text.
    * ``"unknown"`` -- no thinking tags were present at all.
    """
    if not _THINK_OPEN_RE.search(text):
        return "", text, "unknown"

    thinking_parts: list[str] = []

    def _collect(m: re.Match) -> str:
        thinking_parts.append(m.group(2))
        return ""

    answer = _THINK_BLOCK_RE.sub(_collect, text)

    # Detect an unterminated trailing thinking block.
    last_open = max(
        text.rfind("<think>"),
        text.rfind("<thinking>"),
        text.rfind("<reasoning>"),
    )
    last_close = max(
        text.rfind("</think>"),
        text.rfind("</thinking>"),
        text.rfind("</reasoning>"),
    )
    if last_open > last_close:
        # Everything after the dangling open tag is in-progress reasoning.
        tag_end = text.find(">", last_open)
        if tag_end != -1:
            thinking_parts.append(text[tag_end + 1 :])
        # Strip the dangling open tag (and trailing reasoning) from the answer.
        answer = answer[:last_open] if last_open < len(answer) else answer
        phase: Phase = "thinking"
    else:
        phase = "answer"

    return "\n".join(p.strip() for p in thinking_parts if p.strip()), answer.strip(), phase


def has_open_code_fence(text: str) -> str | None:
    """Return the language of an unclosed trailing code fence, or ``None``.

    Counts ```` ``` ```` markers; an odd count means a fence is still open.
    """
    fences = re.findall(r"```([A-Za-z0-9_+-]*)", text)
    if len(fences) % 2 == 1:
        return fences[-1] or ""
    return None


def looks_truncated(text: str) -> bool:
    """Heuristic: does *text* look like it was cut off mid-stream?"""
    stripped = text.rstrip()
    if not stripped:
        return False
    if has_open_code_fence(text) is not None:
        return True
    if stripped.endswith(("...", "—", "-", ",", ":", ";", "(", "[", "{")):
        return True
    last = stripped[-1]
    # Ended without sentence/word terminator and not on a list/heading marker.
    if last.isalnum():
        last_line = stripped.splitlines()[-1].strip()
        if not last_line.startswith(("#", "-", "*", "|", ">")):
            return True
    return False


def detect_truncation(
    *,
    text: str,
    tokens_generated: int,
    effective_max: int,
    headroom_exhausted: bool,
    use_heuristics: bool,
) -> TruncationReason:
    """Classify why generation stopped (model agnostic)."""
    if headroom_exhausted:
        return "headroom"
    if effective_max > 0 and tokens_generated >= effective_max:
        return "max_tokens"
    if use_heuristics and looks_truncated(text):
        return "heuristic"
    return "complete"


def make_outcome(
    *,
    text: str,
    tokens_generated: int,
    effective_max: int,
    headroom_exhausted: bool,
    use_heuristics: bool,
    overlap_chars: int,
) -> GenerationOutcome:
    """Assemble a :class:`GenerationOutcome` from a raw generation pass."""
    thinking, answer, phase = split_thinking_answer(text)
    reason = detect_truncation(
        text=text,
        tokens_generated=tokens_generated,
        effective_max=effective_max,
        headroom_exhausted=headroom_exhausted,
        use_heuristics=use_heuristics,
    )
    truncated = reason != "complete"
    # Anchor on the usable content: answer if present, else the reasoning tail.
    anchor_source = answer if answer.strip() else thinking
    anchor_tail = anchor_source[-overlap_chars:] if anchor_source else ""
    return GenerationOutcome(
        text=text,
        answer_text=answer,
        thinking_text=thinking,
        phase=phase,
        truncated=truncated,
        reason=reason,
        tokens_generated=tokens_generated,
        effective_max=effective_max,
        anchor_tail=anchor_tail,
    )


def compute_headroom(
    *, window_size: int, prompt_tokens: int, safety_tokens: int
) -> int:
    """Tokens available for generation given the current prompt size."""
    return window_size - prompt_tokens - safety_tokens


def resolve_per_pass(
    *, requested: int, headroom: int, floor: int = 256
) -> int:
    """Clamp the requested generation budget to real headroom (>= ``floor``)."""
    if headroom <= 0:
        return 0
    return max(floor, min(requested, headroom))


def stitch_with_overlap(prev: str, nxt: str, anchor_tail: str) -> str:
    """Join *prev* and *nxt*, removing duplicated seam content.

    Detects the longest overlap between the end of *prev* and the start of
    *nxt* (anchored by *anchor_tail*) so a continuation that repeats the last
    sentence does not produce a doubled passage.
    """
    if not prev:
        return nxt
    if not nxt:
        return prev

    # Try the explicit anchor first: if the continuation restated it, drop it.
    # Preserve the original whitespace that followed the anchor so the seam keeps
    # its natural word/sentence boundary.
    anchor = anchor_tail.strip()
    if anchor and anchor in nxt[: len(anchor) + 80]:
        idx = nxt.find(anchor)
        rest = nxt[idx + len(anchor) :]
        if rest.strip():
            return prev + rest

    # General longest-suffix/prefix overlap (cap the window for performance).
    # The anchor path above handles the primary case; this is a safety net, so
    # require a reasonably long match (>= 8 chars) to avoid coincidental joins.
    max_window = min(len(prev), len(nxt), 600)
    for size in range(max_window, 7, -1):
        if prev[-size:] == nxt[:size]:
            return prev + nxt[size:]

    return _join_seam(prev, nxt)


def _join_seam(prev: str, nxt: str) -> str:
    """Join two fragments, inserting a separator only when needed."""
    if not nxt:
        return prev
    if prev.endswith((" ", "\n")) or nxt.startswith((" ", "\n")):
        return prev + nxt
    # If we cut mid-word (both sides alphanumeric) join directly; else add space.
    if prev[-1].isalnum() and nxt[0].isalnum():
        return prev + nxt
    return prev + nxt


def build_continuation_prompt(
    *,
    phase: Phase,
    intent: str,
    brief: str,
    anchor_tail: str,
    open_fence_lang: str | None,
    force_answer_on_thinking_cut: bool,
) -> str:
    """Build a phase-aware continuation instruction for a fresh user turn."""
    fence_note = ""
    if open_fence_lang is not None:
        lang = open_fence_lang or "the same language"
        fence_note = (
            f"\n\nNote: a code block ({lang}) is still open. Resume inside it and "
            "close it with ``` when finished. Do not start a new code block."
        )

    if phase == "thinking" and force_answer_on_thinking_cut:
        established = f"\n\nYou have already established:\n{brief}" if brief.strip() else ""
        return (
            f"You were preparing this deliverable: {intent}.{established}\n\n"
            "Stop deliberating and write the FINAL answer now -- the complete "
            "deliverable -- using the values above. Do not restate your reasoning "
            f"or your thinking process.{fence_note}"
        )

    context = f"\n\nContext so far:\n{brief}" if brief.strip() else ""
    tail = f'\n\nResume immediately after:\n"...{anchor_tail}"' if anchor_tail else ""
    return (
        "Continue your previous response from exactly where it stopped, "
        "mid-sentence if necessary. Do NOT repeat any earlier content and do "
        f"NOT start over.{context}{tail}{fence_note}"
    )


def make_truncation_notice(outcome: GenerationOutcome, headroom: int) -> str:
    """One-line banner describing a capped/truncated generation."""
    return (
        f"[Output incomplete - reason: {outcome.reason}; "
        f"generated ~{outcome.tokens_generated} tokens; "
        f"headroom {headroom}. Use /output to change behavior.]"
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class _PassRequest:
    """Internal: parameters for one generation pass."""

    continuation_prompt: str | None
    effective_max: int


class OutputLimitOrchestrator:
    """Runs the summarize / continue / stop loop around model generation.

    All engine interaction is injected so this class is unit-testable:

    * ``generate(continuation_prompt, accumulated, max_tokens)`` -> ``(text, tokens)``
      Runs one model pass.  ``continuation_prompt`` is ``None`` for the first
      pass (the engine already has the user message queued) or a continuation
      instruction for subsequent passes; ``accumulated`` is the answer text so
      far (appended as an assistant turn for continuation passes).
    * ``prompt_tokens()`` -> base prompt size in tokens (system + history + user).
    * ``estimate_tokens(text)`` -> token estimate for arbitrary text.
    * ``summarize_history()`` -> optional status note (reclaims headroom).
    * ``summarize_text(text)`` -> compact summary of partial output.
    * ``build_brief(intent, partial)`` -> compact continuation brief.
    * ``choice_provider(outcome)`` -> :data:`Action` for ``policy == "prompt"``
      (``None`` means non-interactive: default to ``stop``).
    """

    def __init__(
        self,
        config: OutputLimitConfig,
        *,
        window_size: int,
        requested_max_tokens: int,
        generate: Callable[[str | None, str, int], tuple[str, int]],
        prompt_tokens: Callable[[], int],
        estimate_tokens: Callable[[str], int],
        summarize_history: Callable[[], str | None],
        summarize_text: Callable[[str], str],
        build_brief: Callable[[str, str], str],
        choice_provider: Callable[[GenerationOutcome], Action | None] | None = None,
    ) -> None:
        self.cfg = config
        self.window_size = window_size
        self.requested_max_tokens = requested_max_tokens
        self._generate = generate
        self._prompt_tokens = prompt_tokens
        self._estimate_tokens = estimate_tokens
        self._summarize_history = summarize_history
        self._summarize_text = summarize_text
        self._build_brief = build_brief
        self._choice_provider = choice_provider

    # -- headroom -------------------------------------------------------

    def _headroom(self, accumulated: str = "") -> int:
        prompt = self._prompt_tokens()
        if accumulated:
            prompt += self._estimate_tokens(accumulated)
        return compute_headroom(
            window_size=self.window_size,
            prompt_tokens=prompt,
            safety_tokens=self.cfg.headroom_safety_tokens,
        )

    def _effective_max(self, accumulated: str = "") -> int:
        return resolve_per_pass(
            requested=min(self.requested_max_tokens, self.cfg.max_output_tokens),
            headroom=self._headroom(accumulated),
        )

    # -- one pass -------------------------------------------------------

    def _run_pass(
        self, continuation_prompt: str | None, accumulated: str
    ) -> GenerationOutcome:
        effective = self._effective_max(accumulated)
        text, tokens = self._generate(continuation_prompt, accumulated, max(1, effective))
        headroom_exhausted = self._headroom(accumulated) <= self.cfg.headroom_safety_tokens
        return make_outcome(
            text=text,
            tokens_generated=tokens,
            effective_max=effective,
            headroom_exhausted=headroom_exhausted,
            use_heuristics=self.cfg.truncation_heuristics,
            overlap_chars=self.cfg.continuation_overlap_chars,
        )

    # -- public API -----------------------------------------------------

    def run(self, intent: str) -> OrchestratorResult:
        """Generate a response for *intent*, applying the output-limit policy."""
        actions: list[str] = []

        # Pre-call gating: reclaim space before the first token if too tight.
        if self.cfg.check_before_call and self._headroom() < self.cfg.min_output_tokens:
            note = self._summarize_history()
            if note:
                actions.append(note)

        outcome = self._run_pass(None, "")
        accumulated = outcome.answer_text or outcome.text
        passes = 1

        if not outcome.truncated:
            return OrchestratorResult(accumulated, outcome, actions, passes)

        # Decide what to do about the truncation.
        action = self._decide(outcome)

        if action == "stop":
            actions.append(make_truncation_notice(outcome, self._headroom()))
            return OrchestratorResult(accumulated, outcome, actions, passes)

        if action in ("summarize_history", "summarize_both"):
            accumulated, outcome, passes, sum_actions = self._summarize_and_retry(
                intent, outcome, accumulated, passes, include_partial=action == "summarize_both"
            )
            actions.extend(sum_actions)
            return OrchestratorResult(accumulated, outcome, actions, passes)

        # Continue (same-turn loop).
        accumulated, outcome, passes, cont_actions = self._continue_loop(
            intent, outcome, accumulated, passes
        )
        actions.extend(cont_actions)
        return OrchestratorResult(accumulated, outcome, actions, passes)

    # -- decision -------------------------------------------------------

    def _decide(self, outcome: GenerationOutcome) -> Action:
        policy = self.cfg.policy
        if policy == "stop":
            return "stop"
        if policy == "auto_continue":
            return "continue_same"
        if policy == "auto_summarize":
            scope = self.cfg.summarize_scope_default
            return "summarize_both" if scope == "history_and_partial" else "summarize_history"
        # policy == "prompt"
        if self._choice_provider is None:
            return "stop"
        chosen = self._choice_provider(outcome)
        return chosen or "stop"

    # -- summarize + retry ---------------------------------------------

    def _summarize_and_retry(
        self,
        intent: str,
        outcome: GenerationOutcome,
        accumulated: str,
        passes: int,
        *,
        include_partial: bool,
    ) -> tuple[str, GenerationOutcome, int, list[str]]:
        actions: list[str] = []
        note = self._summarize_history()
        if note:
            actions.append(note)

        brief = ""
        if include_partial and accumulated.strip():
            try:
                brief = self._summarize_text(accumulated)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Partial-output summary failed: %s", exc)

        open_fence = has_open_code_fence(accumulated)
        cont_prompt = build_continuation_prompt(
            phase=outcome.phase,
            intent=intent,
            brief=brief,
            anchor_tail=outcome.anchor_tail,
            open_fence_lang=open_fence,
            force_answer_on_thinking_cut=self.cfg.force_answer_on_thinking_cut,
        )
        new_outcome = self._run_pass(cont_prompt, accumulated)
        passes += 1
        merged = stitch_with_overlap(
            accumulated, new_outcome.answer_text or new_outcome.text, outcome.anchor_tail
        )
        return merged, new_outcome, passes, actions

    # -- continue loop --------------------------------------------------

    def _continue_loop(
        self,
        intent: str,
        outcome: GenerationOutcome,
        accumulated: str,
        passes: int,
    ) -> tuple[str, GenerationOutcome, int, list[str]]:
        actions: list[str] = []
        current = outcome

        while current.truncated and (passes - 1) < self.cfg.max_continuation_passes:
            # Reclaim headroom before continuing if needed.
            if (
                self.cfg.summarize_before_continue
                and self._headroom(accumulated) < self.cfg.min_output_tokens
            ):
                note = self._summarize_history()
                if note:
                    actions.append(note)

            brief = ""
            try:
                brief = self._build_brief(intent, accumulated)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Continuation brief failed: %s", exc)

            open_fence = has_open_code_fence(accumulated)
            cont_prompt = build_continuation_prompt(
                phase=current.phase,
                intent=intent,
                brief=brief,
                anchor_tail=current.anchor_tail,
                open_fence_lang=open_fence,
                force_answer_on_thinking_cut=self.cfg.force_answer_on_thinking_cut,
            )

            if self._effective_max(accumulated) <= 0:
                actions.append(
                    "[Continuation stopped: no context headroom remaining; "
                    "run /clear --summarize.]"
                )
                break

            current = self._run_pass(cont_prompt, accumulated)
            passes += 1
            accumulated = stitch_with_overlap(
                accumulated,
                current.answer_text or current.text,
                # Use the prior anchor for de-dup against what we already have.
                accumulated[-self.cfg.continuation_overlap_chars :],
            )

        if current.truncated:
            actions.append(make_truncation_notice(current, self._headroom()))

        return accumulated, current, passes, actions
