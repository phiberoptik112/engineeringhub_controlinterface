"""Context management strategies for the Journaler daemon.

Implements six layered strategies for managing a finite model context window
across an always-on daemon lifecycle:

  1. Rolling Window       — deque-backed history with turn + token limits
  2. Compression          — summarize-and-replace when pressure is high
  3. Topic-Aware Clear    — archive old topic on detected topic shift
  4. Scheduled Clear      — end-of-day reset (handled in daemon.py)
  5. Hard Clear           — manual nuclear reset via /clear command
  6. Pressure Orchestration — ties all strategies together before each call
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Estimate token count without loading a tokenizer.

    Uses a 1:3 char-to-token ratio (slightly aggressive to leave margin).
    Accurate within ~15% for English prose — sufficient for threshold-based
    budget management.
    """
    return len(text) // 3


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------

@dataclass
class TokenBudget:
    """Tracks token allocation across context components.

    Updated before every model call so pressure decisions reflect the
    current state of history and the rolling context snapshot.
    """

    window_size: int
    system_prompt_tokens: int
    context_snapshot_tokens: int
    history_tokens: int
    loaded_files_tokens: int = 0
    corpus_injection_tokens: int = 0
    reserved_for_generation: int = 2000

    @property
    def used(self) -> int:
        return (
            self.system_prompt_tokens
            + self.context_snapshot_tokens
            + self.history_tokens
            + self.loaded_files_tokens
            + self.corpus_injection_tokens
        )

    @property
    def available(self) -> int:
        return self.window_size - self.used - self.reserved_for_generation

    @property
    def utilization(self) -> float:
        """0.0–1.0 fraction of the context window that is occupied."""
        return self.used / self.window_size if self.window_size else 0.0

    @property
    def pressure(self) -> str:
        """Human-readable pressure level: low / moderate / high / critical."""
        u = self.utilization
        if u < 0.50:
            return "low"
        elif u < 0.70:
            return "moderate"
        elif u < 0.85:
            return "high"
        else:
            return "critical"


# ---------------------------------------------------------------------------
# Conversation history (Strategy 1: Rolling Window)
# ---------------------------------------------------------------------------

@dataclass
class ConversationTurn:
    """A single conversation turn with token and topic metadata."""

    role: str
    content: str
    timestamp: str
    tokens: int
    topic: str | None = None
    preserved: bool = False


class ConversationHistory:
    """Manages conversation turns with token-aware eviction.

    Keeps the last ``max_turns`` exchanges. When a new turn is added and the
    deque exceeds the limit (by count or tokens), the oldest non-preserved
    turn is evicted to the archive. The archive is flushed to JSONL by the
    engine on each call cycle.
    """

    def __init__(self, max_turns: int = 40, max_tokens: int = 20_000) -> None:
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.turns: deque[ConversationTurn] = deque()
        self._archive: list[ConversationTurn] = []

    def add(
        self,
        role: str,
        content: str,
        topic: str | None = None,
        preserved: bool = False,
    ) -> ConversationTurn:
        turn = ConversationTurn(
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tokens=estimate_tokens(content),
            topic=topic,
            preserved=preserved,
        )
        self.turns.append(turn)
        self._enforce_limits()
        return turn

    def _enforce_limits(self) -> None:
        while len(self.turns) > self.max_turns:
            self._evict_oldest()
        while self.total_tokens > self.max_tokens:
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        for i, turn in enumerate(self.turns):
            if not turn.preserved:
                evicted = self.turns[i]
                del self.turns[i]
                self._archive.append(evicted)
                return
        # All turns are preserved — force-evict the absolute oldest
        if self.turns:
            self._archive.append(self.turns.popleft())

    @property
    def total_tokens(self) -> int:
        return sum(t.tokens for t in self.turns)

    def as_messages(self) -> list[dict[str, str]]:
        """Format turns for the model API (Ollama / MLX chat format)."""
        return [{"role": t.role, "content": t.content} for t in self.turns]

    def flush_archive(self) -> list[ConversationTurn]:
        """Return and clear evicted/archived turns (for JSONL logging)."""
        archived = self._archive.copy()
        self._archive.clear()
        return archived

    def clear(self) -> None:
        """Drop all in-memory turns and pending archive."""
        self.turns.clear()
        self._archive.clear()

    def rehydrate_from_jsonl_turns(self, turns: list[dict]) -> None:
        """Load turns from JSONL dicts (preserving timestamps)."""
        self.clear()
        for raw in turns:
            role = raw.get("role")
            content = raw.get("content")
            if not isinstance(role, str) or not isinstance(content, str):
                continue
            if not content.strip():
                continue
            ts = raw.get("timestamp")
            if not isinstance(ts, str) or not ts:
                ts = datetime.now(timezone.utc).isoformat()
            turn = ConversationTurn(
                role=role,
                content=content,
                timestamp=ts,
                tokens=estimate_tokens(content),
            )
            self.turns.append(turn)


# ---------------------------------------------------------------------------
# Compression (Strategy 2)
# ---------------------------------------------------------------------------

_COMPRESSION_PROMPT = """\
Summarize the following conversation concisely.
Preserve: key decisions, specific numbers/references mentioned, any
open questions, and the current topic being discussed. Discard:
pleasantries, repeated information, tangential remarks.

Format as a brief paragraph, not bullet points. Stay under 200 words.

Conversation to compress:
{conversation_text}
"""


@dataclass
class CompressionResult:
    compressed: bool
    turns_compressed: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_freed: int = 0
    summary_text: str = ""


class ContextCompressor:
    """Compresses conversation history into a summary when pressure is high.

    The summary replaces old turns as a single preserved system message so
    the model retains the gist without carrying the full token weight.
    """

    def __init__(
        self,
        engine_call: Callable[[str, int], str],
        pressure_threshold: float = 0.85,
        keep_recent: int = 3,
        target_summary_tokens: int = 500,
    ) -> None:
        self.engine_call = engine_call
        self.pressure_threshold = pressure_threshold
        self.keep_recent = keep_recent
        self.target_summary_tokens = target_summary_tokens
        self.compression_count: int = 0

    def should_compress(self, budget: TokenBudget) -> bool:
        return budget.utilization >= self.pressure_threshold

    def compress(
        self, history: ConversationHistory, *, force: bool = False
    ) -> CompressionResult:
        """Compress older turns into a summary, keeping the most recent turns warm.

        The summary is injected as a preserved system-role message so the
        model treats it as background context rather than a chat message to
        reply to.

        When *force* is True (used under critical pressure), compression still
        runs even if the turn count is at or below ``keep_recent`` by keeping a
        smaller emergency floor. This fixes the edge case where a session sits
        at exactly ``keep_recent`` turns and would otherwise never free space.
        """
        keep = self.keep_recent
        if len(history.turns) <= keep:
            if not force or len(history.turns) <= 2:
                return CompressionResult(compressed=False)
            # Emergency: keep a smaller floor so the oldest turns can compress.
            keep = max(2, len(history.turns) // 2)

        all_turns = list(history.turns)
        to_compress = all_turns[:-keep]
        to_keep = all_turns[-keep:]
        if not to_compress:
            return CompressionResult(compressed=False)

        text = "\n".join(
            f"{t.role.upper()}: {t.content}"
            for t in to_compress
            if t.role != "system"
        )
        tokens_before = sum(t.tokens for t in to_compress)

        try:
            summary = self.engine_call(
                _COMPRESSION_PROMPT.format(conversation_text=text),
                self.target_summary_tokens,
            )
        except Exception as exc:
            logger.warning(f"Compression model call failed: {exc}")
            return CompressionResult(compressed=False)

        summary_content = (
            f"[Conversation summary — {len(to_compress)} earlier exchanges]\n{summary}"
        )
        summary_turn = ConversationTurn(
            role="system",
            content=summary_content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tokens=estimate_tokens(summary_content),
            topic=None,
            preserved=True,
        )

        history._archive.extend(to_compress)
        history.turns.clear()
        history.turns.append(summary_turn)
        history.turns.extend(to_keep)

        self.compression_count += 1
        tokens_after = summary_turn.tokens

        logger.info(
            f"Compressed {len(to_compress)} turns: "
            f"{tokens_before} → {tokens_after} tokens "
            f"(freed {tokens_before - tokens_after})"
        )

        return CompressionResult(
            compressed=True,
            turns_compressed=len(to_compress),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_freed=tokens_before - tokens_after,
            summary_text=summary,
        )


# ---------------------------------------------------------------------------
# Topic tracking (Strategy 3)
# ---------------------------------------------------------------------------

_PROJECT_PATTERNS: dict[str, Callable[[re.Match], str]] = {
    r"project\s+(\d+)": lambda m: f"project_{m.group(1)}",
    r"(?:ASTM|ISO|IBC|ASHRAE)\s+\w+": lambda m: f"standards_{m.group(0).replace(' ', '_')}",
    r"(?:proposal|SOW|scope|fee)": lambda _: "business_writing",
    r"(?:email|slack|message|draft)": lambda _: "communications",
    r"(?:briefing|summary|status|update)": lambda _: "status_review",
}

_MODEL_TOPIC_TAG_RE = re.compile(r"\bTOPIC:\s*(.+)$", re.MULTILINE | re.IGNORECASE)


def detect_topic_heuristic(message: str) -> str | None:
    """Fast keyword-based topic detection. Returns None if ambiguous."""
    for pattern, labeler in _PROJECT_PATTERNS.items():
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return labeler(match)
    return None


def parse_model_topic_tag(response: str) -> str | None:
    """Extract a TOPIC: <label> tag the model appended to its response."""
    m = _MODEL_TOPIC_TAG_RE.search(response)
    return m.group(1).strip().lower().replace(" ", "_") if m else None


@dataclass
class TopicShift:
    old_topic: str
    new_topic: str


class TopicTracker:
    """Tracks conversation topics and detects meaningful topic shifts.

    Uses heuristic detection first; falls back to parsing a ``TOPIC:`` tag
    from the model response. A shift is only declared after
    ``shift_threshold`` consecutive exchanges on the new topic, to avoid
    reacting to one-off tangents.
    """

    def __init__(self, shift_threshold: int = 3) -> None:
        self.current_topic: str | None = None
        self.topic_history: list[tuple[str, str]] = []  # (timestamp, topic)
        self.shift_threshold = shift_threshold
        self._consecutive_new: int = 0

    def observe(self, message: str, response: str) -> TopicShift | None:
        """Observe a new exchange. Returns a TopicShift if the topic changed."""
        detected = detect_topic_heuristic(message)
        if detected is None:
            detected = parse_model_topic_tag(response)
        if detected is None:
            return None

        if self.current_topic is None:
            self.current_topic = detected
            self.topic_history.append((datetime.now(timezone.utc).isoformat(), detected))
            return None

        if detected != self.current_topic:
            self._consecutive_new += 1
            if self._consecutive_new >= self.shift_threshold:
                old = self.current_topic
                self.current_topic = detected
                self._consecutive_new = 0
                self.topic_history.append(
                    (datetime.now(timezone.utc).isoformat(), detected)
                )
                return TopicShift(old_topic=old, new_topic=detected)
        else:
            self._consecutive_new = 0

        return None


# ---------------------------------------------------------------------------
# Domain shift detection (Phase 0.5: Dynamic Prompt Routing)
# ---------------------------------------------------------------------------

@dataclass
class DomainShift:
    """Signals that the conversation has moved to a new domain with a matching skill."""

    old_domain: str
    new_domain: str
    skill_name: str
    display_name: str
    confidence: float


class DomainShiftDetector:
    """Detects when the conversation crosses domain boundaries and maps to a loaded skill.

    Uses ``domain_triggers`` from each :class:`SkillDef` to score incoming user messages.
    A shift is declared after ``shift_threshold`` consecutive turns scoring above
    ``confidence_threshold`` for the same domain — lower than :class:`TopicTracker`'s
    3-turn default because domain pivots are higher-signal than topic oscillations.

    Only raises a shift when the detected domain differs from the *active* persona's
    domain, so already-correct sessions stay quiet.

    Skills are passed in as a plain dict to avoid a circular import with ``delegator``;
    the caller resolves the dict from ``AgentDelegator.list_skills()``.
    """

    def __init__(
        self,
        skills: dict[str, object],
        shift_threshold: int = 2,
        confidence_threshold: float = 0.15,
        suppress_turns: int = 5,
    ) -> None:
        self._skills = skills
        self.shift_threshold = shift_threshold
        self.confidence_threshold = confidence_threshold
        self.suppress_turns = suppress_turns

        self.active_domain: str = ""
        self.pending_shift: DomainShift | None = None

        self._consecutive_count: int = 0
        self._consecutive_candidate: str = ""
        self._suppressed_domain: str = ""
        self._suppressed_remaining: int = 0

    def observe(self, message: str) -> DomainShift | None:
        """Score *message* against all skill triggers and return a :class:`DomainShift` if warranted.

        Returns ``None`` when no shift has been confirmed.  Sets ``pending_shift`` when
        a shift is ready so the chat loop can display the confirmation prompt without
        calling this method again.
        """
        if self._suppressed_remaining > 0:
            self._suppressed_remaining -= 1

        best_skill, confidence = self._score(message)
        if best_skill is None or confidence < self.confidence_threshold:
            self._consecutive_count = 0
            self._consecutive_candidate = ""
            return None

        detected_domain = best_skill.domain or best_skill.name

        # Already in this domain — no shift needed.
        if detected_domain == self.active_domain:
            self._consecutive_count = 0
            self._consecutive_candidate = ""
            return None

        # Suppressed after a user rejection.
        if (
            self._suppressed_remaining > 0
            and detected_domain == self._suppressed_domain
        ):
            return None

        if detected_domain == self._consecutive_candidate:
            self._consecutive_count += 1
        else:
            self._consecutive_candidate = detected_domain
            self._consecutive_count = 1

        if self._consecutive_count >= self.shift_threshold:
            shift = DomainShift(
                old_domain=self.active_domain,
                new_domain=detected_domain,
                skill_name=best_skill.name,
                display_name=best_skill.display_name,
                confidence=confidence,
            )
            self.pending_shift = shift
            self._consecutive_count = 0
            self._consecutive_candidate = ""
            return shift

        return None

    def confirm_shift(self, skill_name: str) -> None:
        """Call after the user accepts the persona swap."""
        skill = self._skills.get(skill_name)
        if skill is not None:
            self.active_domain = skill.domain or skill.name
        self.pending_shift = None
        self._suppressed_remaining = 0
        self._suppressed_domain = ""

    def reject_shift(self, suppress_turns: int | None = None) -> None:
        """Call after the user declines the persona swap."""
        if self.pending_shift:
            self._suppressed_domain = self.pending_shift.new_domain
            self._suppressed_remaining = (
                suppress_turns if suppress_turns is not None else self.suppress_turns
            )
        self.pending_shift = None

    def set_active_domain(self, domain: str) -> None:
        """Explicitly set the active domain (e.g. when the user runs /persona directly)."""
        self.active_domain = domain
        self.pending_shift = None
        self._consecutive_count = 0
        self._consecutive_candidate = ""

    def _score(self, message: str) -> tuple[object | None, float]:
        """Return the best-matching skill and a confidence score.

        Scoring: ``hit_count / min(len(triggers), 5)`` so that 1 strong trigger
        match on a focused list scores ≥ 0.20, which clears the default threshold
        of 0.15.  Skills without ``domain_triggers`` are skipped.  In case of a tie
        the skill with the higher raw hit count wins.
        """
        msg_lower = message.lower()
        best_skill = None
        best_score = 0.0
        best_hits = 0

        for skill in self._skills.values():
            triggers = getattr(skill, "domain_triggers", [])
            if not triggers:
                continue

            hit_count = sum(
                1
                for t in triggers
                if re.search(r"\b" + re.escape(t.lower()) + r"\b", msg_lower)
            )
            if hit_count == 0:
                continue

            # Normalise against a cap of 5 so long trigger lists don't need many
            # simultaneous hits to register as a confident match.
            score = hit_count / min(len(triggers), 5)
            if score > best_score or (score == best_score and hit_count > best_hits):
                best_score = score
                best_hits = hit_count
                best_skill = skill

        return best_skill, best_score


# ---------------------------------------------------------------------------
# Clear strategies (Strategy 5: Hard Clear)
# ---------------------------------------------------------------------------

class ClearStrategy(Enum):
    SOFT = "soft"
    HARD = "hard"
    SUMMARIZE = "summarize"


def _is_conversation_summary_turn(turn: ConversationTurn) -> bool:
    return (
        turn.preserved
        and turn.role == "system"
        and turn.content.startswith("[Conversation summary")
    )


def _clear_recent_turns_keep_summary(history: ConversationHistory) -> int:
    """Archive non-summary turns and keep preserved summary system message(s)."""
    kept: list[ConversationTurn] = []
    cleared = 0
    for turn in list(history.turns):
        if _is_conversation_summary_turn(turn):
            kept.append(turn)
        else:
            history._archive.append(turn)
            cleared += 1
    history.turns.clear()
    history.turns.extend(kept)
    return cleared


def execute_clear(
    strategy: ClearStrategy,
    history: ConversationHistory,
    compressor: ContextCompressor,
    last_scan_time: str = "",
    reset_state_fn: Callable[[], None] | None = None,
    budget: TokenBudget | None = None,
) -> str:
    """Execute a clear command. Returns a human-readable status message."""

    if strategy == ClearStrategy.SUMMARIZE:
        turn_count = len(history.turns)
        result = compressor.compress(history, force=True)
        if result.compressed:
            cleared_recent = _clear_recent_turns_keep_summary(history)
            return (
                f"Compressed {result.turns_compressed} turns "
                f"({result.tokens_before} → {result.tokens_after} tokens, "
                f"freed {result.tokens_freed}). "
                f"Cleared {cleared_recent} recent turn(s); summary retained."
            )

        high_pressure = budget is not None and budget.utilization >= compressor.pressure_threshold
        if high_pressure and turn_count > 0:
            history_tokens_before = sum(t.tokens for t in history.turns)
            history._archive.extend(list(history.turns))
            history.turns.clear()
            hint = ""
            if budget is not None and budget.loaded_files_tokens > history_tokens_before:
                hint = " Loaded files still dominate the window; try /files clear."
            return (
                f"Could not summarize ({turn_count} turn(s) — need more than 2 to merge). "
                f"Cleared {turn_count} turn(s) instead.{hint}"
            )

        return (
            f"Nothing to summarize ({turn_count} turn(s)). "
            f"Use /clear for a soft reset or /files clear if a loaded file fills the window."
        )

    elif strategy == ClearStrategy.SOFT:
        turn_count = len(history.turns)
        history._archive.extend(list(history.turns))
        history.turns.clear()
        scan_note = (
            f" Context snapshot retained (last scan: {last_scan_time})."
            if last_scan_time
            else ""
        )
        return f"Cleared {turn_count} conversation turns.{scan_note}"

    elif strategy == ClearStrategy.HARD:
        turn_count = len(history.turns)
        history._archive.extend(list(history.turns))
        history.turns.clear()
        if reset_state_fn is not None:
            reset_state_fn()
        return (
            f"Full reset: {turn_count} turns cleared, "
            f"scan state wiped. Next scan will rebuild from scratch."
        )

    return "Unknown clear strategy."


# ---------------------------------------------------------------------------
# Pressure orchestration (Strategy 6)
# ---------------------------------------------------------------------------

@dataclass
class PressureConfig:
    """Thresholds and settings for automatic context management."""

    compress_at: float = 0.85
    emergency_trim_at: float = 0.95
    auto_clear_on_topic_shift: bool = False
    notify_user_on_action: bool = True
    end_of_day_time: str = "00:00"
    inactivity_clear_minutes: int = 120
    capture_daily_to_memory: bool = True
    conversation_relation_threshold: float = 0.55
    conversation_relation_k: int = 5
    conversation_relation_excerpt_chars: int = 1000
    org_link_excerpt_chars: int = 400
    past_session_search_k: int = 5
    past_session_excerpt_chars: int = 1200
    reserved_for_generation: int = 2000
    model_context_window: int = 32768
    max_history_turns: int = 40
    max_history_tokens: int = 20_000
    compression_keep_recent: int = 8
    compression_summary_tokens: int = 800


class ContextPressureManager:
    """Monitors token budget and triggers context management strategies.

    Runs automatically before and after each model call. Gentle strategies
    (log only) handle common cases; aggressive ones (compress, trim) kick
    in as pressure escalates. All actions are optionally surfaced to the
    user as bracketed notes prepended to the model response.
    """

    def __init__(
        self,
        budget: TokenBudget,
        history: ConversationHistory,
        compressor: ContextCompressor,
        topic_tracker: TopicTracker,
        config: PressureConfig,
    ) -> None:
        self.budget = budget
        self.history = history
        self.compressor = compressor
        self.topic_tracker = topic_tracker
        self.config = config
        self.named_conversation_active: bool = False
        self.suggest_split_on_topic_shift: bool = True

    def pre_call_check(self) -> list[str]:
        """Run before every model call. Returns action descriptions (empty if none)."""
        actions: list[str] = []

        self.budget.history_tokens = self.history.total_tokens
        utilization = self.budget.utilization

        if 0.50 <= utilization < self.config.compress_at:
            logger.debug(
                f"Context pressure: moderate ({self.budget.utilization:.0%})"
            )

        elif utilization < self.config.emergency_trim_at:
            if self.compressor.should_compress(self.budget):
                result = self.compressor.compress(self.history, force=True)
                if result.compressed:
                    actions.append(
                        f"[Context compressed: freed {result.tokens_freed} tokens "
                        f"from {result.turns_compressed} earlier exchanges]"
                    )
                    self.budget.history_tokens = self.history.total_tokens

        else:
            result = self.compressor.compress(self.history, force=True)
            if result.compressed:
                actions.append(
                    f"[Emergency compression: freed {result.tokens_freed} tokens]"
                )
                self.budget.history_tokens = self.history.total_tokens

            if self.budget.utilization >= self.config.emergency_trim_at:
                dropped = self._emergency_trim(
                    keep=self.config.compression_keep_recent
                )
                if dropped:
                    actions.append(
                        f"[Emergency trim: dropped {dropped} old turns to recover space]"
                    )
                    self.budget.history_tokens = self.history.total_tokens

        return actions

    def post_call_check(self, user_message: str, response: str) -> list[str]:
        """Run after each model call. Handles topic tracking and auto-archive."""
        actions: list[str] = []

        shift = self.topic_tracker.observe(user_message, response)
        if shift:
            if self.named_conversation_active and self.suggest_split_on_topic_shift:
                actions.append(
                    f"[Topic drifted to {shift.new_topic} — "
                    f"/convo new to split it out?]"
                )
            elif self.config.auto_clear_on_topic_shift:
                result = self.compressor.compress(self.history)
                if result.compressed:
                    actions.append(
                        f"[Topic shifted: {shift.old_topic} → {shift.new_topic}, "
                        f"archived {result.turns_compressed} earlier turns]"
                    )
                    self.budget.history_tokens = self.history.total_tokens

        return actions

    def _emergency_trim(self, keep: int = 3) -> int:
        """Force-keep only the last ``keep`` turns, archiving the rest."""
        if len(self.history.turns) <= keep:
            return 0
        all_turns = list(self.history.turns)
        to_drop = all_turns[:-keep]
        to_keep = all_turns[-keep:]
        self.history._archive.extend(to_drop)
        self.history.turns.clear()
        self.history.turns.extend(to_keep)
        return len(to_drop)
