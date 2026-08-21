"""ConversationEngine: manages a persistent conversation with a local MLX model.

The engine keeps the model loaded ("warm") between calls, maintains a rolling
conversation history with automatic token-pressure management, and refreshes
its context block on each scan cycle. Briefing generation uses a separate
prompt path to avoid polluting chat history.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from engineering_hub.journaler.org_writer import append_to_heading, read_section_body
from engineering_hub.memory.service import MemoryResult, MemoryService

if TYPE_CHECKING:
    from corpus.service import CorpusService
    from engineering_hub.journaler.conversation_store import Conversation, ConversationStore
    from engineering_hub.journaler.daemon import ConversationsConfig

from engineering_hub.actions.file_ingest import read_path_content_for_load
from engineering_hub.core.exceptions import LLMBackendError
from engineering_hub.journaler.context_manager import (
    ClearStrategy,
    ContextCompressor,
    ContextPressureManager,
    ConversationHistory,
    ConversationTurn,
    DomainShiftDetector,
    PressureConfig,
    TokenBudget,
    TopicTracker,
    estimate_tokens,
    execute_clear,
)
from engineering_hub.journaler.prompts import FOCUS_TECHNICAL_WRITING_PROMPT
from engineering_hub.journaler.output_limit import (
    Action,
    GenerationOutcome,
    OrchestratorResult,
    OutputLimitConfig,
    OutputLimitOrchestrator,
)
from engineering_hub.journaler.session_retrieval import (
    format_past_session_block,
    references_past_session,
    retrieve_past_sessions,
)
from engineering_hub.journaler.task_planner_models import TaskPlannerSession
from engineering_hub.journaler.thinking import (
    ThinkTagTracker,
    prompt_primes_thinking,
    strip_think_blocks,
)
from engineering_hub.search import (
    SearchProvider,
    format_search_results_for_context,
)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".txt",
        ".org",
        ".py",
        ".yaml",
        ".yml",
        ".json",
        ".tex",
        ".csv",
        ".toml",
        ".rst",
        ".docx",
        ".pdf",
    }
)

logger = logging.getLogger(__name__)

_DATE_TAG = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CROSS_REF_HEADING = "Journaler Cross-References"

# Prompts for output-limit continuation fidelity (kept compact on purpose).
_CONTINUATION_BRIEF_PROMPT = (
    "You are helping another assistant resume an interrupted response.\n"
    "The user's original request was:\n{intent}\n\n"
    "The response so far (it was cut off) is:\n{partial}\n\n"
    "Write a COMPACT brief (bullet points, no preamble) capturing only what is "
    "needed to finish the deliverable: the concrete facts, values, decisions, "
    "and structure already established, plus exactly what still needs to be "
    "written. Do not restate the full text."
)

_PARTIAL_SUMMARY_PROMPT = (
    "Summarize the key established facts, values, and decisions in the text "
    "below as compact bullet points (no preamble). This will be used to finish "
    "an interrupted answer, so preserve specifics:\n\n{text}"
)


@dataclass
class FocusDocument:
    """Document pinned as the exclusive context for focused writing."""

    path: Path
    label: str
    content: str
    mode: str = "technical-writing"
    output_path: Path | None = None
    truncated: bool = False
    max_chars: int | None = None


# ---------------------------------------------------------------------------
# Daily-summary relation helpers
# ---------------------------------------------------------------------------

def _relation_excerpt(content: str, *, excerpt_chars: int) -> str:
    excerpt = content[:excerpt_chars].replace("\n", " ").strip()
    if len(content) > excerpt_chars:
        excerpt += "..."
    return excerpt


def _related_date_from_hit(hit: MemoryResult) -> str:
    if hit.created_at:
        return hit.created_at[:10]
    for tag in hit.tags:
        if _DATE_TAG.match(tag):
            return tag
    return ""


def _resolve_relation_file_link(
    related_date: str,
    journal_dir: Path,
    state_dir: Path,
) -> str:
    if not related_date:
        return "Related conversation"

    journal_path = (journal_dir / f"{related_date}.org").expanduser().resolve()
    if journal_path.is_file():
        return f"[[file:{journal_path}][{related_date} daily journal]]"

    summary_path = (
        state_dir / "daily_summaries" / f"{related_date}.md"
    ).expanduser().resolve()
    if summary_path.is_file():
        return f"[[file:{summary_path}][Journaler summary {related_date}]]"

    return related_date


def _format_relation_block(
    hits: list[MemoryResult],
    *,
    excerpt_chars: int = 1000,
) -> str:
    """Format matched daily-summary hits as a context block for the model.

    The block header is phrased as an explicit instruction so the model
    calls out the relationship in its response rather than silently using
    the context.
    """
    lines = [
        "### Related Past Conversation",
        "_You have found prior Journaler sessions that are semantically related_"
        " _to the current question.  Begin your response by explicitly noting_"
        " _the connection — cite the date and shared topic in one sentence before answering._",
        "",
    ]
    for hit in hits:
        date_str = _related_date_from_hit(hit) or "unknown"
        excerpt = _relation_excerpt(hit.content, excerpt_chars=excerpt_chars)
        lines.append(f"**{date_str}** _{hit.similarity:.0%} match_")
        lines.append(f"> {excerpt}")
        lines.append("")
    return "\n".join(lines)


def _write_relation_link(
    journal_dir: Path,
    state_dir: Path,
    hit: MemoryResult,
    *,
    excerpt_chars: int = 400,
) -> None:
    """Append a cross-reference link to the current day's journal.

    Creates a ``* Journaler Cross-References`` heading if it does not
    exist, then appends a dated link so the org file accumulates a
    lightweight relation graph over time.
    """
    today_path = journal_dir / f"{date.today().isoformat()}.org"
    if not today_path.exists():
        return

    related_date = _related_date_from_hit(hit)
    if related_date:
        existing = read_section_body(today_path, _CROSS_REF_HEADING)
        if related_date in existing:
            return

    link_label = _resolve_relation_file_link(related_date, journal_dir, state_dir)
    excerpt = _relation_excerpt(hit.content, excerpt_chars=excerpt_chars)
    link_text = f"- {link_label} ({hit.similarity:.0%} match): {excerpt}"

    try:
        append_to_heading(
            today_path,
            _CROSS_REF_HEADING,
            link_text,
            create_heading_if_missing=True,
        )
    except Exception as exc:
        logger.warning("Failed to write relation link to journal (non-fatal): %s", exc)


@dataclass(frozen=True)
class LoadFileBudgetConfig:
    """How much of the remaining context window may be used per `/load` operation.

    Caps are expressed using the same ``len(text) // 3`` heuristic as
    :func:`~engineering_hub.journaler.context_manager.estimate_tokens`.
    """

    max_context_fraction: float = 0.65
    max_chars_absolute: int = 200_000
    min_chars: int = 1024
    slack_tokens: int = 128

    def __post_init__(self) -> None:
        f = self.max_context_fraction
        if not 0.0 < f <= 1.0:
            raise ValueError("max_context_fraction must be in (0, 1]")
        if self.max_chars_absolute < 1:
            raise ValueError("max_chars_absolute must be >= 1")
        if self.min_chars < 0:
            raise ValueError("min_chars must be >= 0")
        if self.slack_tokens < 0:
            raise ValueError("slack_tokens must be >= 0")


@dataclass(frozen=True)
class DelegateContextResult:
    """Context plus retrieval status for a delegated `/agent` task."""

    context: str
    web_search_attempted: bool = False
    web_search_succeeded: bool = False
    web_search_error: str | None = None


def _is_model_cached(model_id: str) -> bool:
    """Return True if the HF Hub snapshot for model_id is already on disk.

    Uses huggingface_hub.try_to_load_from_cache — no network call, no model
    load.  Returns True immediately for local directory paths.
    """
    resolved = Path(model_id).expanduser()
    if resolved.is_dir():
        return True
    try:
        from huggingface_hub import try_to_load_from_cache
        from huggingface_hub.file_download import _CACHED_NO_EXIST

        result = try_to_load_from_cache(model_id, "config.json")
        return result is not None and result is not _CACHED_NO_EXIST
    except Exception:
        return False


_VLM_MODEL_TYPES: frozenset[str] = frozenset(
    {
        "gemma4",
        "paligemma",
        "llava",
        "idefics",
        "blip",
        "flamingo",
        "internvl",
        "qwen2_vl",
        "qwen3_vl",
    }
)


def _detect_vlm(load_path: str) -> bool:
    """Return True when the checkpoint should load via mlx-vlm (not mlx-lm).

    Peeks at config.json without loading model weights — works for both local
    directories and HF Hub cache entries.  Falls back to False on any error so
    a mis-detection never hard-blocks startup.

    Some newer multimodal checkpoints (e.g. Qwen3.5/3.6 MoE) ship a
    ``vision_config`` but still load for text-only Journaler chat via mlx-lm.
    Only known mlx-vlm-only ``model_type`` values select the VLM backend here;
    users can still force ``mlx_backend: mlx-vlm`` in a profile when needed.
    """
    config_path: Path | None = None

    local = Path(load_path).expanduser()
    if local.is_dir():
        candidate = local / "config.json"
        if candidate.exists():
            config_path = candidate
    else:
        try:
            from huggingface_hub import try_to_load_from_cache

            cached = try_to_load_from_cache(load_path, "config.json")
            if cached and Path(cached).exists():
                config_path = Path(cached)
        except Exception:
            pass

    if config_path is None:
        return False

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        model_type = cfg.get("model_type", "").lower()
        # Route only known VLMs to mlx-vlm.  Do not treat every model with a
        # vision_config block as a VLM — e.g. Qwen3.6 (qwen3_5_moe) ships
        # multimodal config but mlx-community text weights load via mlx-lm.
        return model_type in _VLM_MODEL_TYPES
    except Exception:
        return False


class ConversationalMLXBackend:
    """MLX backend with multi-turn chat support.

    Supports both text-only models (via mlx-lm) and Vision-Language Models
    such as Gemma 4 (via mlx-vlm).  The appropriate library is selected
    automatically by inspecting the model's config.json, or can be forced
    with the *backend* parameter.

    The model is loaded once on init and stays resident in memory.
    """

    def __init__(
        self,
        model_path: str,
        temp: float = 0.7,
        top_p: float = 0.9,
        min_p: float = 0.05,
        repetition_penalty: float = 1.1,
        repetition_context_size: int = 20,
        backend: str = "auto",
        enable_thinking: bool | None = None,
    ) -> None:
        self._temp = temp
        self._top_p = top_p
        self._min_p = min_p
        self._repetition_penalty = repetition_penalty
        self._repetition_context_size = repetition_context_size
        self._enable_thinking = enable_thinking

        resolved = str(Path(model_path).expanduser())
        load_path = resolved if Path(resolved).is_dir() else model_path

        if backend == "mlx-vlm":
            self._is_vlm = True
        elif backend == "mlx-lm":
            self._is_vlm = False
        else:
            self._is_vlm = _detect_vlm(load_path)

        logger.info(
            f"Loading Journaler model from: {load_path} "
            f"(backend={'mlx-vlm' if self._is_vlm else 'mlx-lm'})"
        )

        if self._is_vlm:
            self._load_vlm(load_path, model_path)
        else:
            self._load_lm(load_path, model_path)

    # ------------------------------------------------------------------
    # Backend-specific loaders
    # ------------------------------------------------------------------

    def _load_lm(self, load_path: str, model_path: str) -> None:
        try:
            import mlx_lm
            from mlx_lm.sample_utils import make_logits_processors, make_sampler
        except ImportError as exc:
            raise LLMBackendError(
                "mlx-lm is not installed. Install with: pip install 'engineering-hub[mlx]'",
                provider="mlx",
            ) from exc

        self._mlx_lm = mlx_lm
        self._make_sampler = make_sampler
        self._make_logits_processors = make_logits_processors
        self._mlx_vlm = None
        self._processor = None

        try:
            self._model, self._tokenizer = mlx_lm.load(load_path)
        except Exception as exc:
            raise LLMBackendError(
                f"Failed to load Journaler model from '{load_path}': {exc}",
                provider="mlx",
            ) from exc
        logger.info(f"Journaler model loaded (mlx-lm): {model_path}")

    def _load_vlm(self, load_path: str, model_path: str) -> None:
        try:
            import mlx_vlm
        except ImportError as exc:
            raise LLMBackendError(
                "mlx-vlm is not installed. Install with: pip install 'engineering-hub[mlx]'",
                provider="mlx",
            ) from exc

        self._mlx_vlm = mlx_vlm
        self._mlx_lm = None
        self._make_sampler = None
        self._make_logits_processors = None

        try:
            self._model, self._processor = mlx_vlm.load(load_path)
            self._tokenizer = self._processor.tokenizer
        except Exception as exc:
            detail = str(exc)
            if "torchvision" in detail.lower():
                detail += (
                    " Install torchvision for mlx-vlm VL processors: "
                    "pip install torchvision"
                )
            raise LLMBackendError(
                f"Failed to load Journaler VLM from '{load_path}': {detail}",
                provider="mlx",
            ) from exc
        logger.info(f"Journaler model loaded (mlx-vlm): {model_path}")

    def _apply_chat_template_safe(self, messages: list[dict[str, str]]) -> str:
        """Build prompt via tokenizer chat template; pass enable_thinking when supported."""
        tokenizer = self._tokenizer
        kwargs: dict[str, Any] = {
            "tokenize": False,
            "add_generation_prompt": True,
        }
        if self._enable_thinking is not None:
            kwargs["enable_thinking"] = self._enable_thinking
        try:
            return tokenizer.apply_chat_template(messages, **kwargs)  # type: ignore[no-any-return]
        except TypeError:
            kwargs.pop("enable_thinking", None)
            return tokenizer.apply_chat_template(messages, **kwargs)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        max_thinking_tokens: int = 0,
    ) -> str:
        """Generate a response given a full message history.

        Args:
            messages: List of {role, content} dicts (system, user, assistant).
            max_tokens: Maximum tokens for the user-facing answer.
            max_thinking_tokens: Additional token budget granted to
                ``<think>...</think>`` reasoning blocks emitted by thinking
                models (Qwen3-style). Thinking tokens do not count against
                *max_tokens*, so a long reasoning phase can no longer starve
                the final answer.

        Returns:
            The model's response text.
        """
        prompt = self._apply_chat_template_safe(messages)

        if self._is_vlm:
            return self._chat_vlm(prompt, max_tokens)
        return self._chat_lm(prompt, max_tokens, max_thinking_tokens)

    def _chat_lm(
        self, prompt: str, max_tokens: int, max_thinking_tokens: int = 0
    ) -> str:
        """Stream tokens with separate thinking/answer budgets and detect truncation.

        Uses ``mlx_lm.stream_generate`` (instead of ``generate``) so the
        ``finish_reason`` is observable: "stop" means the model concluded
        naturally, "length" means it was cut off at the token limit. Tokens
        inside ``<think>`` blocks draw from *max_thinking_tokens*; everything
        else draws from *max_tokens*. When output is truncated anyway, an
        explicit notice is appended so truncation is never silent.
        """
        sampler = self._make_sampler(  # type: ignore[misc]
            temp=self._temp, top_p=self._top_p, min_p=self._min_p
        )
        logits_processors = self._make_logits_processors(  # type: ignore[misc]
            repetition_penalty=self._repetition_penalty,
            repetition_context_size=self._repetition_context_size,
        )
        think_budget = max(0, max_thinking_tokens)
        hard_cap = max_tokens + think_budget

        # Qwen3.5/3.6 templates prime the assistant turn with "<think>\n", so
        # generation starts inside a think block and only "</think>" appears
        # in the output stream.
        tracker = ThinkTagTracker(start_open=prompt_primes_thinking(prompt))
        chunks: list[str] = []
        answer_tokens = 0
        thinking_tokens = 0
        total_tokens = 0
        finish_reason: str | None = None
        cut_reason: str | None = None

        try:
            for response in self._mlx_lm.stream_generate(  # type: ignore[union-attr]
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=hard_cap,
                sampler=sampler,
                logits_processors=logits_processors,
            ):
                segment = response.text
                if segment:
                    chunks.append(segment)
                    tracker.feed(segment)
                total_tokens += 1
                if tracker.open:
                    thinking_tokens += 1
                else:
                    answer_tokens += 1
                finish_reason = response.finish_reason
                if finish_reason is not None:
                    break
                if tracker.open and thinking_tokens >= think_budget + max_tokens:
                    # Model never closed its think block; it has consumed the
                    # thinking budget plus the answer budget — stop here.
                    cut_reason = "thinking"
                    break
                if not tracker.open and answer_tokens >= max_tokens:
                    cut_reason = "answer"
                    break
        except Exception as exc:
            raise LLMBackendError(
                f"Journaler MLX generation failed: {exc}", provider="mlx"
            ) from exc

        text = "".join(chunks)
        truncated = cut_reason is not None or finish_reason == "length"
        if not truncated:
            return text

        if cut_reason is None:
            cut_reason = "thinking" if tracker.open else "answer"
        logger.warning(
            "Journaler generation truncated (%s budget exhausted): "
            "%d total tokens (%d thinking / %d answer; answer budget %d, "
            "thinking budget %d)",
            cut_reason,
            total_tokens,
            thinking_tokens,
            answer_tokens,
            max_tokens,
            think_budget,
        )
        if tracker.open:
            notice = (
                "\n\n---\n"
                f"⚠ Generation stopped mid-thinking after {total_tokens} tokens "
                "— no final answer was produced. Raise "
                "`journaler.max_thinking_tokens` (or free context with /clear) "
                "and try again."
            )
        else:
            notice = (
                "\n\n---\n"
                f"⚠ Response truncated at the {max_tokens}-token answer limit. "
                "Raise `journaler.max_tokens` or ask for a continuation."
            )
        return text + notice

    def _chat_vlm(self, prompt: str, max_tokens: int) -> str:
        try:
            raw = self._mlx_vlm.generate(  # type: ignore[union-attr]
                self._model,
                self._processor,
                prompt=prompt,
                image=None,
                max_tokens=max_tokens,
                temp=self._temp,
                top_p=self._top_p,
                verbose=False,
            )
            # mlx_vlm.generate returns GenerationResult (dataclass with .text), not str.
            if isinstance(raw, str):
                return raw
            text = getattr(raw, "text", None)
            if isinstance(text, str):
                return text
            return str(raw)
        except Exception as exc:
            raise LLMBackendError(
                f"Journaler MLX-VLM generation failed: {exc}", provider="mlx"
            ) from exc

    def is_loaded(self) -> bool:
        return self._model is not None

    # ------------------------------------------------------------------
    # Runtime setters (no model reload required)
    # ------------------------------------------------------------------

    def set_enable_thinking(self, value: bool | None) -> None:
        """Update the thinking-mode flag applied to the chat template."""
        self._enable_thinking = value

    def set_sampling_params(
        self,
        *,
        temp: float | None = None,
        top_p: float | None = None,
        min_p: float | None = None,
        repetition_penalty: float | None = None,
    ) -> None:
        """Update one or more sampling parameters in place."""
        if temp is not None:
            self._temp = temp
        if top_p is not None:
            self._top_p = top_p
        if min_p is not None:
            self._min_p = min_p
        if repetition_penalty is not None:
            self._repetition_penalty = repetition_penalty

    def stream_generate(
        self, messages: list[dict[str, str]], max_tokens: int
    ) -> Iterator[str]:
        """Yield tokens one-at-a-time via ``mlx_lm.stream_generate``.

        Only available for text-only (non-VLM) models.  VLM models fall back
        to a single blocking ``chat()`` call whose full response is yielded
        as one chunk.
        """
        prompt = self._apply_chat_template_safe(messages)
        if self._is_vlm:
            yield self._chat_vlm(prompt, max_tokens)
            return

        sampler = self._make_sampler(  # type: ignore[misc]
            temp=self._temp, top_p=self._top_p, min_p=self._min_p
        )
        logits_processors = self._make_logits_processors(  # type: ignore[misc]
            repetition_penalty=self._repetition_penalty,
            repetition_context_size=self._repetition_context_size,
        )
        try:
            for result in self._mlx_lm.stream_generate(  # type: ignore[union-attr]
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=sampler,
                logits_processors=logits_processors,
            ):
                # mlx_lm may yield strings or objects with a `.text` attribute.
                if isinstance(result, str):
                    yield result
                else:
                    text = getattr(result, "text", None)
                    if text is not None:
                        yield str(text)
        except Exception as exc:
            raise LLMBackendError(
                f"Journaler MLX stream generation failed: {exc}", provider="mlx"
            ) from exc


class ConversationEngine:
    """Manages a persistent conversation session with a local model.

    Context is refreshed every scan cycle; conversation history is maintained
    in memory with automatic token-pressure management and logged to
    conversation.jsonl. Supports soft/hard/summarize clears, status queries,
    and briefing generation.
    """

    def __init__(
        self,
        backend: ConversationalMLXBackend,
        system_prompt: str,
        log_dir: Path,
        max_history: int = 20,
        max_tokens: int = 4096,
        max_thinking_tokens: int = 8192,
        pressure_config: PressureConfig | None = None,
        output_limit: OutputLimitConfig | None = None,
        model_context_window: int = 32768,
        corpus_service: CorpusService | None = None,
        load_file_budget: LoadFileBudgetConfig | None = None,
        memory_service: MemoryService | None = None,
        journal_dir: Path | None = None,
        relation_threshold: float = 0.55,
        org_link_on_relation: bool = True,
        web_search_provider: SearchProvider | None = None,
        web_search_enabled: bool = False,
        web_search_max_results: int = 5,
        web_search_max_chars: int = 12_000,
        web_search_anthropic_backup_enabled: bool = False,
        web_search_anthropic_tool_version: str = "web_search_20250305",
        web_search_anthropic_max_uses: int = 3,
    ) -> None:
        self._backend = backend
        self._system_prompt = system_prompt
        self._context_block = ""
        self._corpus_service = corpus_service
        self._memory_service = memory_service
        self._journal_dir = journal_dir
        self._relation_threshold = relation_threshold
        self._org_link_on_relation = org_link_on_relation
        self._web_search_provider = web_search_provider
        self._web_search_enabled = web_search_enabled
        self._web_search_max_results = max(1, web_search_max_results)
        self._web_search_max_chars = max(1_000, web_search_max_chars)
        self._web_search_anthropic_backup_enabled = web_search_anthropic_backup_enabled
        self._web_search_anthropic_tool_version = web_search_anthropic_tool_version
        self._web_search_anthropic_max_uses = max(1, web_search_anthropic_max_uses)
        self._loaded_files: dict[str, str] = {}
        self._loaded_file_paths: dict[str, str] = {}
        self._max_tokens = max_tokens
        self._max_thinking_tokens = max(0, max_thinking_tokens)
        self._log_dir = log_dir
        self._log_file = log_dir / "conversation.jsonl"
        self._load_file_budget = load_file_budget or LoadFileBudgetConfig()

        cfg = pressure_config or PressureConfig(
            max_history_turns=max_history,
            model_context_window=model_context_window,
        )

        self.history = ConversationHistory(
            max_turns=cfg.max_history_turns,
            max_tokens=cfg.max_history_tokens,
        )
        reserved_gen = max(cfg.reserved_for_generation, max_tokens)
        self.budget = TokenBudget(
            window_size=cfg.model_context_window,
            system_prompt_tokens=estimate_tokens(system_prompt),
            context_snapshot_tokens=0,
            history_tokens=0,
            loaded_files_tokens=0,
            corpus_injection_tokens=0,
            reserved_for_generation=reserved_gen,
        )
        self.compressor = ContextCompressor(
            engine_call=self._raw_complete,
            pressure_threshold=cfg.compress_at,
            keep_recent=cfg.compression_keep_recent,
            target_summary_tokens=cfg.compression_summary_tokens,
        )
        self.topic_tracker = TopicTracker()
        self.pressure_manager = ContextPressureManager(
            budget=self.budget,
            history=self.history,
            compressor=self.compressor,
            topic_tracker=self.topic_tracker,
            config=cfg,
        )
        self._pressure_config = cfg
        self._output_limit = output_limit or OutputLimitConfig()
        # Optional interactive chooser for ``policy == "prompt"`` (set by the CLI).
        self._output_choice_provider: (
            Callable[[GenerationOutcome], Action | None] | None
        ) = None
        self._roam_edit_target: Path | None = None
        self._focus_document: FocusDocument | None = None

        self.session_id = str(uuid.uuid4())
        self.session_opened_at = datetime.now(timezone.utc)
        self.task_planner = TaskPlannerSession(self.session_id, self.session_opened_at)
        self.pinned_state: dict[str, Any] = {"task_planner": self.task_planner}

        # Persona state — populated by swap_persona() / /persona command.
        self.active_persona: str = ""
        self.base_system_prompt: str = system_prompt
        self.domain_shift_detector: DomainShiftDetector | None = None

        self.active_conversation: Conversation | None = None
        self._conversation_store: ConversationStore | None = None
        self._conversations_config: ConversationsConfig | None = None

    def attach_conversation_store(
        self,
        store: ConversationStore,
        config: ConversationsConfig,
    ) -> None:
        """Enable multi-conversation mode with a ConversationStore."""
        self._conversation_store = store
        self._conversations_config = config
        self.pressure_manager.suggest_split_on_topic_shift = (
            config.suggest_split_on_topic_shift
        )

    @property
    def conversation_store(self) -> ConversationStore | None:
        return self._conversation_store

    def save_session(self) -> None:
        """Persist outgoing conversation metadata and file manifest."""
        store = self._conversation_store
        conv = self.active_conversation
        if store is None or conv is None:
            return

        file_manifest = list(self._loaded_file_paths.values())
        topic = conv.topic
        if topic is None and self.topic_tracker.current_topic:
            topic = self.topic_tracker.current_topic

        turn_count = store.count_jsonl_turns(conv.id)
        if turn_count == 0:
            turn_count = len(self.history.turns)

        store.update_meta(
            conv.id,
            topic=topic,
            turn_count=turn_count,
            file_manifest=file_manifest,
        )
        if topic and conv.topic != topic:
            conv.topic = topic
        conv.file_manifest = file_manifest
        conv.turn_count = turn_count

    def switch_session(
        self,
        conv: Conversation,
        *,
        restore_files: bool = False,
    ) -> str:
        """Swap to another conversation; returns a user-facing status line."""
        store = self._conversation_store
        if store is None:
            return "Conversations are not enabled."

        self.save_session()

        self.history.clear()
        max_turns = 20
        if self._conversations_config is not None:
            max_turns = self._conversations_config.max_restore_history_turns
        jsonl_turns = store.read_turns(conv.id, limit=max_turns)
        self.history.rehydrate_from_jsonl_turns(jsonl_turns)

        self.clear_loaded_files()

        self._log_file = store.jsonl_path(conv.id)
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._log_file.exists():
            self._log_file.touch()

        self.session_id = conv.id
        try:
            opened = datetime.fromisoformat(conv.created_at.replace("Z", "+00:00"))
        except ValueError:
            opened = datetime.now(timezone.utc)
        self.session_opened_at = opened
        self.task_planner = TaskPlannerSession(self.session_id, self.session_opened_at)

        self.topic_tracker = TopicTracker()
        if conv.topic:
            self.topic_tracker.current_topic = conv.topic

        self.budget.history_tokens = self.history.total_tokens
        self._sync_loaded_files_budget()

        store.set_active(conv.id)
        refreshed = store.get(conv.id)
        self.active_conversation = refreshed if refreshed is not None else conv
        self.pressure_manager.named_conversation_active = True

        manifest_count = len(self.active_conversation.file_manifest)
        do_restore = restore_files or (
            self._conversations_config is not None
            and self._conversations_config.restore_files_on_switch
        )
        restore_msg = ""
        if do_restore and manifest_count:
            ok_count, restore_msg = self.restore_session_files()

        parts = [
            f"Switched to conversation '{self.active_conversation.title}' "
            f"({self.active_conversation.id})",
            f"{len(self.history.turns)} turns restored",
        ]
        if manifest_count and not do_restore:
            parts.append(
                f"{manifest_count} file(s) remembered — /convo restore-files to load"
            )
        elif restore_msg:
            parts.append(restore_msg)
        return ". ".join(parts) + "."

    def restore_session_files(self) -> tuple[int, str]:
        """Re-load the active conversation's remembered file paths."""
        conv = self.active_conversation
        if conv is None or not conv.file_manifest:
            return 0, "No remembered files for this conversation."

        loaded = 0
        skipped: list[str] = []
        for path_str in conv.file_manifest:
            path = Path(path_str).expanduser()
            ok, msg = self.load_file(path)
            if ok:
                loaded += 1
            else:
                skipped.append(f"{path.name}: {msg}")

        parts = [f"Restored {loaded}/{len(conv.file_manifest)} remembered file(s)"]
        if skipped:
            parts.append("Skipped: " + "; ".join(skipped))
        return loaded, ". ".join(parts)

    def get_roam_edit_target(self) -> Path | None:
        """Session target for ``/edit`` (set via ``/open`` in journaler chat)."""
        return self._roam_edit_target

    def set_roam_edit_target(self, path: Path | None) -> None:
        """Set or clear the org-roam file ``/edit`` appends to."""
        if path is None:
            self._roam_edit_target = None
        else:
            self._roam_edit_target = path.expanduser().resolve()

    def swap_persona(
        self,
        skill_name: str,
        display_name: str,
        description: str,
        *,
        personas_dir: Path | None = None,
    ) -> str:
        """Hot-swap the main Journaler system prompt to a new persona domain.

        Rebuilds ``_system_prompt`` by prepending a persona header derived from the
        skill's description.  If a file ``prompts/personas/{skill_name}.txt`` exists
        it is used as the full persona block instead.  A bracketed note is injected
        into the conversation history so the model is aware of the transition.

        Args:
            skill_name: Canonical skill/agent name (e.g. ``"career-coach"``).
            display_name: Human-readable persona label for the history note.
            description: Skill description used as the persona header when no
                ``personas/`` file is found.
            personas_dir: Optional directory to search for a ``{skill_name}.txt``
                persona file.  Falls back to a ``prompts/personas/`` sibling of the
                default prompts directory.

        Returns:
            Status message suitable for display in the chat UI.
        """
        persona_block = self._load_persona_block(
            skill_name, description, personas_dir=personas_dir
        )
        self._system_prompt = persona_block + "\n\n" + self.base_system_prompt
        self.budget.system_prompt_tokens = estimate_tokens(self._system_prompt)
        self.active_persona = skill_name

        note = (
            f"[Persona switched to **{display_name}**. "
            f"Previous conversation context retained.]"
        )
        now = datetime.now().isoformat(timespec="seconds")
        self.history.add("assistant", note)
        self._log_turn("assistant", note, now)

        if self.domain_shift_detector is not None:
            skill = self.domain_shift_detector._skills.get(skill_name)
            domain = (skill.domain if skill is not None else "") or skill_name
            self.domain_shift_detector.confirm_shift(skill_name)
            self.domain_shift_detector.set_active_domain(domain)

        logger.info("Persona swapped to '%s'", skill_name)
        return note

    def reset_persona(self) -> str:
        """Restore the base system prompt, clearing any active persona."""
        self._system_prompt = self.base_system_prompt
        self.budget.system_prompt_tokens = estimate_tokens(self._system_prompt)
        old = self.active_persona
        self.active_persona = ""
        if self.domain_shift_detector is not None:
            self.domain_shift_detector.set_active_domain("")

        note = "[Persona reset to default Journaler.]"
        now = datetime.now().isoformat(timespec="seconds")
        self.history.add("assistant", note)
        self._log_turn("assistant", note, now)

        logger.info("Persona reset from '%s' to default", old)
        return note

    def _load_persona_block(
        self,
        skill_name: str,
        description: str,
        *,
        personas_dir: Path | None = None,
    ) -> str:
        """Return the persona system-prompt block for *skill_name*.

        Checks for a ``prompts/personas/{skill_name}.txt`` file first; falls back
        to a concise header built from the skill ``description``.
        """
        if personas_dir is None:
            # Try to locate prompts/personas/ relative to known locations.
            for candidate in [
                Path(__file__).parent.parent.parent.parent / "prompts" / "personas",
                Path.cwd() / "prompts" / "personas",
            ]:
                if candidate.is_dir():
                    personas_dir = candidate
                    break

        if personas_dir is not None:
            persona_file = personas_dir / f"{skill_name}.txt"
            if persona_file.is_file():
                try:
                    return persona_file.read_text(encoding="utf-8").strip()
                except OSError as exc:
                    logger.warning(
                        "Could not read persona file %s: %s", persona_file, exc
                    )

        first_line = description.splitlines()[0] if description else skill_name
        return (
            f"## Active persona: {skill_name}\n\n"
            f"{first_line}\n\n"
            f"You are now operating in **{skill_name}** mode. "
            f"Apply the expertise and style appropriate to this domain."
        )

    def replace_backend(
        self,
        backend: Any,
        *,
        model_context_window: int | None = None,
        max_tokens: int | None = None,
        max_thinking_tokens: int | None = None,
    ) -> None:
        """Swap the LM backend (e.g. after ``/model``) while keeping conversation history."""
        self._backend = backend
        if max_thinking_tokens is not None:
            self._max_thinking_tokens = max(0, max_thinking_tokens)
        if max_tokens is not None:
            self._max_tokens = max_tokens
            self.budget.reserved_for_generation = max(
                self._pressure_config.reserved_for_generation, max_tokens
            )
        if model_context_window is not None:
            self._pressure_config.model_context_window = model_context_window
            self.budget.window_size = model_context_window

    def update_max_tokens(self, value: int) -> None:
        """Update the per-turn generation budget without reloading the model."""
        self._max_tokens = value
        self.budget.reserved_for_generation = max(
            self._pressure_config.reserved_for_generation, value
        )

    def update_sampling_params(self, **kwargs: Any) -> None:
        """Forward sampling-parameter changes to the active backend."""
        self._backend.set_sampling_params(**kwargs)

    def update_context(self, context_block: str) -> None:
        """Replace the rolling context section of the system prompt."""
        self._context_block = context_block
        self._sync_context_snapshot_budget()

    # ------------------------------------------------------------------
    # Output-limit policy plumbing
    # ------------------------------------------------------------------

    def set_output_choice_provider(
        self, provider: Callable[[GenerationOutcome], Action | None] | None
    ) -> None:
        """Register an interactive chooser used when ``policy == "prompt"``."""
        self._output_choice_provider = provider

    def get_output_limit(self) -> OutputLimitConfig:
        """Return the active output-limit configuration."""
        return self._output_limit

    def set_output_limit(self, config: OutputLimitConfig) -> None:
        """Replace the active output-limit configuration."""
        self._output_limit = config

    def _build_extra_suffix(self, message: str) -> str | None:
        """Assemble corpus RAG / past-session / relation context for a turn."""
        extra_suffix: str | None = None
        cs = self._corpus_service
        if (
            not self.focus_is_active()
            and cs is not None
            and cs.is_available()
            and message.strip()
        ):
            try:
                results = cs.search(message)
                if results:
                    extra_suffix = cs.format_for_context(results)
            except Exception as exc:
                logger.warning("Journaler corpus RAG failed (non-fatal): %s", exc)

        if not self.focus_is_active() and references_past_session(message):
            try:
                session_hits = retrieve_past_sessions(
                    message,
                    state_dir=self._log_dir,
                    max_results=self._pressure_config.past_session_search_k,
                    excerpt_chars=self._pressure_config.past_session_excerpt_chars,
                    store=self._conversation_store,
                )
                session_block = format_past_session_block(session_hits)
                if session_block:
                    extra_suffix = (
                        (extra_suffix + "\n\n" + session_block)
                        if extra_suffix
                        else session_block
                    )
            except Exception as exc:
                logger.warning("Journaler past-session retrieval failed: %s", exc)

        # Per-turn relation search: check daily summaries for related past conversations
        if not self.focus_is_active() and self._memory_service and message.strip():
            try:
                relation_hits = self._memory_service.search(
                    message,
                    source="journaler",
                    threshold=self._relation_threshold,
                    k=self._pressure_config.conversation_relation_k,
                )
                if relation_hits:
                    relation_block = _format_relation_block(
                        relation_hits,
                        excerpt_chars=(
                            self._pressure_config.conversation_relation_excerpt_chars
                        ),
                    )
                    extra_suffix = (
                        (extra_suffix + "\n\n" + relation_block)
                        if extra_suffix
                        else relation_block
                    )
                    if self._org_link_on_relation and self._journal_dir:
                        _write_relation_link(
                            self._journal_dir,
                            self._log_dir,
                            relation_hits[0],
                            excerpt_chars=self._pressure_config.org_link_excerpt_chars,
                        )
            except Exception as exc:
                logger.warning("Journaler relation search failed (non-fatal): %s", exc)

        return extra_suffix

    def _make_orchestrator(
        self,
        message: str,
        extra_suffix: str | None,
        on_token: Callable[[str], None] | None,
    ) -> OutputLimitOrchestrator:
        """Wire an :class:`OutputLimitOrchestrator` to this engine's backend."""

        def _generate(
            continuation_prompt: str | None, accumulated: str, max_tokens: int
        ) -> tuple[str, int]:
            messages = self._build_messages(extra_system_suffix=extra_suffix)
            messages.append({"role": "user", "content": message})
            if continuation_prompt is not None:
                if accumulated.strip():
                    messages.append({"role": "assistant", "content": accumulated})
                messages.append({"role": "user", "content": continuation_prompt})
            if on_token is not None:
                chunks: list[str] = []
                for token in self._backend.stream_generate(messages, max_tokens):
                    chunks.append(token)
                    on_token(token)
                text = "".join(chunks)
            else:
                text = self._backend.chat(
                    messages,
                    max_tokens,
                    max_thinking_tokens=self._effective_thinking_budget(),
                )
            # Strip <think> blocks so reasoning transcripts never leak into the
            # user-facing answer, history, or continuation-pass accumulation.
            text = strip_think_blocks(text) or text
            return text, estimate_tokens(text)

        def _prompt_tokens() -> int:
            messages = self._build_messages(extra_system_suffix=extra_suffix)
            total = sum(estimate_tokens(m["content"]) for m in messages)
            return total + estimate_tokens(message)

        def _summarize_history() -> str | None:
            result = self.compressor.compress(self.history, force=True)
            if result.compressed:
                self.budget.history_tokens = self.history.total_tokens
                return (
                    f"[Context compressed: freed {result.tokens_freed} tokens "
                    f"from {result.turns_compressed} earlier exchanges]"
                )
            return None

        def _summarize_text(text: str) -> str:
            return self._raw_complete(
                _PARTIAL_SUMMARY_PROMPT.format(text=text),
                self._output_limit.continuation_summary_tokens,
            )

        def _build_brief(intent: str, partial: str) -> str:
            return self._raw_complete(
                _CONTINUATION_BRIEF_PROMPT.format(intent=intent, partial=partial),
                self._output_limit.continuation_summary_tokens,
            )

        return OutputLimitOrchestrator(
            self._output_limit,
            window_size=self.budget.window_size,
            requested_max_tokens=self._max_tokens,
            generate=_generate,
            prompt_tokens=_prompt_tokens,
            estimate_tokens=estimate_tokens,
            summarize_history=_summarize_history,
            summarize_text=_summarize_text,
            build_brief=_build_brief,
            choice_provider=self._output_choice_provider,
        )

    def _run_output_policy(
        self, message: str, on_token: Callable[[str], None] | None = None
    ) -> OrchestratorResult:
        """Shared generation path for :meth:`chat` and :meth:`stream_chat`."""
        self.budget.corpus_injection_tokens = 0
        self.budget.history_tokens = self.history.total_tokens
        self._sync_loaded_files_budget()

        extra_suffix = self._build_extra_suffix(message)
        if extra_suffix:
            self.budget.corpus_injection_tokens = estimate_tokens(extra_suffix)
        else:
            self.budget.corpus_injection_tokens = 0

        # Pre-call: utilization-based compression/trim (separate from headroom gating).
        pre_actions = self.pressure_manager.pre_call_check()

        orchestrator = self._make_orchestrator(message, extra_suffix, on_token)
        result = orchestrator.run(message)
        final = result.text

        now = datetime.now().isoformat(timespec="seconds")
        resp_time = datetime.now().isoformat(timespec="seconds")
        self.history.add("user", message)
        # Thinking blocks are stripped from history so reasoning transcripts do
        # not bloat the rolling context or confuse subsequent turns.
        history_response = strip_think_blocks(final)
        if not history_response:
            history_response = "(thinking-only output; no final answer was produced)"
        self.history.add("assistant", history_response)
        self.budget.history_tokens = self.history.total_tokens

        post_actions = self.pressure_manager.post_call_check(message, history_response)

        self._log_turn("user", message, now)
        self._log_turn("assistant", final, resp_time)

        archived = self.history.flush_archive()
        if archived:
            self._log_archived_turns(archived)

        self.budget.corpus_injection_tokens = 0

        if self._pressure_config.notify_user_on_action:
            result.actions = pre_actions + result.actions + post_actions
        else:
            result.actions = []
        return result

    def chat(self, message: str) -> str:
        """Send a user message and get a (possibly multi-pass) response.

        Runs pre-call pressure checks, applies the output-limit policy
        (summarize / continue / stop on truncation), records history, and
        flushes archived turns to conversation.jsonl.
        """
        result = self._run_output_policy(message)
        response = result.text
        if result.actions:
            action_text = "\n".join(result.actions)
            response = f"{action_text}\n\n{response}"
        return response

    def _effective_thinking_budget(self) -> int:
        """Thinking-token budget for the next call, clamped to context headroom.

        ``reserved_for_generation`` already covers the answer budget
        (``max_tokens``); thinking tokens may additionally use whatever window
        headroom remains above that reservation, up to the configured
        ``max_thinking_tokens``.
        """
        headroom = max(0, self.budget.available)
        return min(self._max_thinking_tokens, headroom)

    def stream_chat(self, message: str) -> Iterator[str]:
        """Streaming counterpart to :meth:`chat` — yields token strings live.

        The output-limit policy runs in a worker thread so continuation passes
        keep streaming; bracketed action notices are flushed at the end.
        """
        result_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()

        def _on_token(token: str) -> None:
            result_queue.put(("tok", token))

        def _worker() -> None:
            try:
                result = self._run_output_policy(message, on_token=_on_token)
                result_queue.put(("done", result))
            except Exception as exc:  # surfaced to the consumer below
                result_queue.put(("err", exc))

        threading.Thread(target=_worker, daemon=True).start()

        while True:
            kind, payload = result_queue.get()
            if kind == "tok":
                yield payload
            elif kind == "err":
                raise payload
            else:  # "done"
                result = payload
                if result.actions:
                    yield "\n\n" + "\n".join(result.actions)
                return

    def inject_turn(self, user: str, assistant: str) -> None:
        """Inject a pre-computed (user, assistant) exchange into history and the log.

        Used to persist agent dispatch results that bypassed ``chat()`` (e.g.
        confirmed DISPATCH sentinel executions) so they appear in rolling context
        and ``conversation.jsonl``.
        """
        now = datetime.now().isoformat(timespec="seconds")
        self.history.add("user", user)
        self.history.add("assistant", assistant)
        self.budget.history_tokens = self.history.total_tokens
        self._log_turn("user", user, now)
        self._log_turn("assistant", assistant, now)
        archived = self.history.flush_archive()
        if archived:
            self._log_archived_turns(archived)

    def clear(self, strategy: ClearStrategy) -> str:
        """Execute a manual clear command. Returns a status message."""
        last_scan = ""
        self.budget.history_tokens = self.history.total_tokens
        self._sync_loaded_files_budget()
        msg = execute_clear(
            strategy=strategy,
            history=self.history,
            compressor=self.compressor,
            last_scan_time=last_scan,
            budget=self.budget,
        )
        self.budget.history_tokens = self.history.total_tokens
        return msg

    def get_status(self) -> dict:
        """Return current context management state for display / /status command."""
        self.budget.history_tokens = self.history.total_tokens
        self._sync_context_snapshot_budget()
        self._sync_loaded_files_budget()
        focus_doc = self.get_focus_document()
        gen_headroom = max(
            0,
            self.budget.window_size
            - self.budget.used
            - self._output_limit.headroom_safety_tokens,
        )
        return {
            "context_window": self.budget.window_size,
            "utilization": f"{self.budget.utilization:.0%}",
            "pressure": self.budget.pressure,
            "gen_headroom": gen_headroom,
            "history_turns": len(self.history.turns),
            "history_tokens": self.history.total_tokens,
            "context_snapshot_tokens": self.budget.context_snapshot_tokens,
            "system_prompt_tokens": self.budget.system_prompt_tokens,
            "loaded_files_tokens": self.budget.loaded_files_tokens,
            "corpus_injection_tokens": self.budget.corpus_injection_tokens,
            "available_tokens": self.budget.available,
            "compressions_today": self.compressor.compression_count,
            "current_topic": self.topic_tracker.current_topic,
            "focus_mode": focus_doc.mode if focus_doc else "",
            "focus_document": str(focus_doc.path) if focus_doc else "",
            "focus_output": (
                str(focus_doc.output_path)
                if focus_doc and focus_doc.output_path
                else ""
            ),
            "active_conversation_id": (
                self.active_conversation.id if self.active_conversation else ""
            ),
            "active_conversation_title": (
                self.active_conversation.title if self.active_conversation else ""
            ),
        }

    def generate_briefing(
        self,
        briefing_context: str,
        briefing_prompt: str,
        max_tokens: int | None = None,
    ) -> str:
        """Generate a morning briefing using a separate prompt.

        Uses a richer context and a dedicated prompt template.
        Does NOT pollute the chat history — briefing is a one-shot generation.

        Args:
            briefing_context: Unused legacy parameter (context is already
                embedded in *briefing_prompt* by ``format_briefing_prompt``).
            briefing_prompt: Fully-formatted prompt including context.
            max_tokens: Override generation budget.  Falls back to
                ``self._max_tokens`` when *None*.
        """
        gen_tokens = max_tokens if max_tokens is not None else self._max_tokens
        think_tokens = self._max_thinking_tokens
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Journaler — an always-on engineering assistant "
                    "embedded in Jake's acoustic engineering consulting workflow. "
                    "You have deep familiarity with his org-roam workspace, "
                    "ongoing projects (ASTM/ISO standards compliance, test "
                    "protocols, client reports), and the agent delegation system "
                    "(research, technical-writer, standards-checker agents).\n\n"
                    "Generate a thorough, actionable morning briefing. Your goal "
                    "is not just to summarize — it is to *reason about project "
                    "trajectories* and suggest concrete paths forward. When you "
                    "see recurring topics or stale tasks, diagnose likely causes "
                    "and recommend next actions. Distinguish quick wins from deep "
                    "work blocks and flag items that can be delegated to agents.\n\n"
                    "Format the briefing as clean markdown: use ## section headings, "
                    "blank lines between topics, and generous spacing between "
                    "sections for readability."
                ),
            },
            {
                "role": "user",
                "content": briefing_prompt,
            },
        ]
        raw = self._backend.chat(
            messages, gen_tokens, max_thinking_tokens=think_tokens
        )
        return strip_think_blocks(raw) or raw

    def get_history_summary(self) -> str:
        """Return a brief summary of recent conversation for status display."""
        if not self.history.turns:
            return "No conversation history."
        count = len(self.history.turns)
        last = self.history.turns[-1]
        return f"{count} turns, last at {last.timestamp} ({last.role})"

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def _loaded_files_section(self) -> str:
        """Markdown block for ``## Loaded Files`` (must match ``_build_messages``)."""
        if not self._loaded_files:
            return ""
        blocks = ["\n\n## Loaded Files\n"]
        for label, content in self._loaded_files.items():
            blocks.append(f"### {label}\n```\n{content}\n```")
        return "\n".join(blocks)

    def focus_is_active(self) -> bool:
        """Return True when focused document writing mode is active."""
        return self._focus_document is not None

    def get_focus_document(self) -> FocusDocument | None:
        """Return the current focus document, if any."""
        return self._focus_document

    def set_focus_document(self, document: FocusDocument | None) -> None:
        """Set or clear the focus document for strict writing context."""
        if document is None:
            self._focus_document = None
        else:
            document.path = document.path.expanduser().resolve()
            if document.output_path is not None:
                document.output_path = document.output_path.expanduser().resolve()
            self._focus_document = document
        self._sync_context_snapshot_budget()
        self._sync_loaded_files_budget()

    def clear_focus_document(self) -> None:
        """Disable focus writing mode."""
        self.set_focus_document(None)

    def set_focus_output_path(self, output_path: Path | None) -> None:
        """Set or clear the intended output path for the focus document."""
        if self._focus_document is None:
            return
        self._focus_document.output_path = (
            output_path.expanduser().resolve() if output_path is not None else None
        )

    def _focus_document_section(self) -> str:
        """Markdown block for the active focus document."""
        document = self._focus_document
        if document is None:
            return ""
        path = str(document.path)
        output = str(document.output_path) if document.output_path else "(not set)"
        truncated = "yes" if document.truncated else "no"
        return (
            "\n\n## Focus Document\n\n"
            f"- Path: `{path}`\n"
            f"- Label: `{document.label}`\n"
            f"- Mode: `{document.mode}`\n"
            f"- Intended output: `{output}`\n"
            f"- Truncated: `{truncated}`\n\n"
            "```text\n"
            f"{document.content}\n"
            "```"
        )

    def _dynamic_max_chars_for_focus_document(self) -> int:
        """Characters allowed for a focus document under strict-context budget."""
        self.budget.history_tokens = self.history.total_tokens
        bf = self._load_file_budget
        tokens_avail = (
            self.budget.window_size
            - self.budget.system_prompt_tokens
            - self.budget.history_tokens
            - self.budget.reserved_for_generation
            - bf.slack_tokens
        )
        if tokens_avail <= 0:
            return 0
        alloc_tokens = max(1, int(tokens_avail * bf.max_context_fraction))
        char_cap = min(bf.max_chars_absolute, alloc_tokens * 3)
        if char_cap > 0 and char_cap < bf.min_chars:
            char_cap = min(bf.min_chars, tokens_avail * 3, bf.max_chars_absolute)
        return max(0, char_cap)

    def load_focus_document(
        self,
        path: Path,
        *,
        output_path: Path | None = None,
        max_chars: int | None = None,
        extensions: frozenset[str] = SUPPORTED_EXTENSIONS,
    ) -> tuple[bool, str]:
        """Read a single file and make it the active focus document."""
        path = path.expanduser().resolve()

        if not path.exists():
            return False, f"File not found: {path}"

        if not path.is_file():
            return False, f"Path is not a file: {path}"

        if extensions and path.suffix.lower() not in extensions:
            return False, (
                f"Extension '{path.suffix}' is not supported. "
                f"Supported: {', '.join(sorted(extensions))}"
            )

        if max_chars is None:
            max_chars = self._dynamic_max_chars_for_focus_document()
            if max_chars <= 0:
                return (
                    False,
                    "No context budget remaining for a focus document "
                    f"(window {self.budget.window_size:,} tokens). "
                    "Try /clear or a larger journaler.model_context_window.",
                )

        try:
            content = read_path_content_for_load(path)
        except OSError as exc:
            return False, f"Could not read {path.name}: {exc}"
        except Exception as exc:
            return False, f"Could not load {path.name}: {exc}"

        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        self.set_focus_document(
            FocusDocument(
                path=path,
                label=path.name,
                content=content,
                output_path=output_path,
                truncated=truncated,
                max_chars=max_chars,
            )
        )

        size_kb = len(content) / 1024
        msg = f"Focus document loaded: '{path.name}' ({size_kb:.1f} KB)"
        if truncated:
            msg += f" [truncated to {max_chars:,} chars (context-aware cap)]"
        return True, msg

    def build_delegate_context(
        self,
        task_description: str,
        *,
        web_search_enabled: bool | None = None,
        web_search_required: bool = False,
    ) -> str:
        """Assemble reference material for delegated agent tasks.

        Includes files the user loaded via ``/load`` in this Journaler session,
        plus semantic-search excerpts from ``corpus.db`` when a corpus service
        is configured (same RAG path as chat turns).
        """
        return self.build_delegate_context_result(
            task_description,
            web_search_enabled=web_search_enabled,
            web_search_required=web_search_required,
        ).context

    def build_delegate_context_result(
        self,
        task_description: str,
        *,
        web_search_enabled: bool | None = None,
        web_search_required: bool = False,
    ) -> DelegateContextResult:
        """Assemble delegated task context and report web retrieval status."""
        parts: list[str] = []
        focused = self.get_focus_document()
        if focused is not None:
            parts.append(
                "## Focus document from Journaler chat\n\n"
                "The user is in focus technical-writing mode. Treat this "
                "document as the primary source and avoid unrelated workspace "
                "context unless the user explicitly requests it.\n\n"
            )
            parts.append(self._focus_document_section().strip())
            return DelegateContextResult(
                context="\n".join(parts).strip(),
                web_search_attempted=False,
                web_search_succeeded=False,
                web_search_error=None,
            )

        loaded = self._loaded_files_section().strip()
        if loaded:
            parts.append(
                "## Files loaded in Journaler chat\n\n"
                "The user loaded these into the Journaler session before delegating. "
                "Treat them as primary reference for the task unless they conflict "
                "with stated requirements.\n\n"
            )
            parts.append(loaded)

        cs = self._corpus_service
        query = (task_description or "").strip()
        if cs is not None and cs.is_available() and query:
            try:
                results = cs.search(query=query)
            except Exception as exc:
                logger.warning("Delegate corpus search failed (non-fatal): %s", exc)
                results = []
            if results:
                parts.append(
                    "\n## Reference corpus (corpus.db)\n\n"
                    "Excerpts from the vector-indexed PDF reference library, retrieved "
                    "for this task. Prefer these over generic knowledge when they apply; "
                    "cite ``source_file`` and page when quoting.\n\n"
                )
                parts.append(cs.format_for_context(results))

        attempted = False
        succeeded = False
        error: str | None = None
        use_web = self._web_search_enabled if web_search_enabled is None else web_search_enabled
        if use_web and query:
            attempted = True
            provider = self._web_search_provider
            if provider is None:
                error = "No local web search provider is configured."
            else:
                try:
                    web_results = provider.search(query, self._web_search_max_results)
                    if web_results:
                        parts.append(
                            "\n"
                            + format_search_results_for_context(
                                web_results,
                                query=query,
                                max_chars=self._web_search_max_chars,
                            )
                        )
                        succeeded = True
                    else:
                        error = "Local web search returned no results."
                except Exception as exc:
                    logger.warning("Delegate web search failed (non-fatal): %s", exc)
                    error = str(exc)

            if error and web_search_required:
                parts.append(
                    "\n## Web search unavailable\n\n"
                    f"The user explicitly requested web search, but local retrieval failed: {error}"
                )

        return DelegateContextResult(
            context="\n".join(parts).strip(),
            web_search_attempted=attempted,
            web_search_succeeded=succeeded,
            web_search_error=error,
        )

    @property
    def web_search_anthropic_backup_enabled(self) -> bool:
        """Whether Claude server-side web search may back up failed local search."""
        return self._web_search_anthropic_backup_enabled

    @property
    def web_search_anthropic_tool_version(self) -> str:
        """Anthropic web search tool version for fallback mode."""
        return self._web_search_anthropic_tool_version

    @property
    def web_search_anthropic_max_uses(self) -> int:
        """Maximum Anthropic server-side web search uses in fallback mode."""
        return self._web_search_anthropic_max_uses

    def _sync_loaded_files_budget(self) -> None:
        if self._focus_document is not None:
            self.budget.loaded_files_tokens = estimate_tokens(
                self._focus_document_section()
            )
        else:
            self.budget.loaded_files_tokens = estimate_tokens(self._loaded_files_section())

    def _sync_context_snapshot_budget(self) -> None:
        if self._focus_document is not None:
            self.budget.context_snapshot_tokens = 0
        else:
            self.budget.context_snapshot_tokens = estimate_tokens(self._context_block)

    def _dynamic_max_chars_for_next_load(self) -> int:
        """Characters allowed for the next file chunk from remaining context budget."""
        self.budget.history_tokens = self.history.total_tokens
        self._sync_loaded_files_budget()
        bf = self._load_file_budget
        tokens_avail = (
            self.budget.window_size
            - self.budget.system_prompt_tokens
            - self.budget.context_snapshot_tokens
            - self.budget.history_tokens
            - self.budget.loaded_files_tokens
            - self.budget.corpus_injection_tokens
            - self.budget.reserved_for_generation
            - bf.slack_tokens
        )
        if tokens_avail <= 0:
            return 0
        alloc_tokens = max(1, int(tokens_avail * bf.max_context_fraction))
        char_cap = min(bf.max_chars_absolute, alloc_tokens * 3)
        if char_cap > 0 and char_cap < bf.min_chars:
            char_cap = min(bf.min_chars, tokens_avail * 3, bf.max_chars_absolute)
        return max(0, char_cap)

    def load_file(
        self,
        path: Path,
        max_chars: int | None = None,
        extensions: frozenset[str] = SUPPORTED_EXTENSIONS,
    ) -> tuple[bool, str]:
        """Read a single file and add its content to the loaded-files block.

        Args:
            path: Absolute or relative path to the file.
            max_chars: Maximum characters to read; ``None`` uses a cap derived from
                ``model_context_window`` and current loaded/history usage.
            extensions: Allowed file extensions. Pass ``frozenset()`` to skip the check.

        Returns:
            ``(ok, message)`` where message describes what happened.
        """
        path = path.expanduser().resolve()

        if not path.exists():
            return False, f"File not found: {path}"

        if not path.is_file():
            return False, f"Path is not a file: {path}"

        if extensions and path.suffix.lower() not in extensions:
            return False, (
                f"Extension '{path.suffix}' is not supported. "
                f"Supported: {', '.join(sorted(extensions))}"
            )

        if max_chars is None:
            max_chars = self._dynamic_max_chars_for_next_load()
            if max_chars <= 0:
                return (
                    False,
                    "No context budget remaining for loaded files "
                    f"(window {self.budget.window_size:,} tokens). "
                    "Try /files clear, /clear, or a larger journaler.model_context_window.",
                )

        try:
            content = read_path_content_for_load(path)
        except OSError as exc:
            return False, f"Could not read {path.name}: {exc}"
        except Exception as exc:
            return False, f"Could not load {path.name}: {exc}"

        if not content.strip() or content.strip() == "(No text extracted)":
            if path.suffix.lower() == ".pdf":
                return False, (
                    f"No extractable text in '{path.name}'. It may be a scanned/"
                    "image-only PDF; install the optional Docling OCR extra "
                    "(pip install '.[docling-ocrmac]') and retry."
                )
            return False, f"No readable text content in '{path.name}'."

        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        label = path.name
        self._loaded_files[label] = content
        self._loaded_file_paths[label] = str(path)
        self._sync_loaded_files_budget()

        size_kb = len(content) / 1024
        msg = f"Loaded '{label}' ({size_kb:.1f} KB)"
        if truncated:
            msg += f" [truncated to {max_chars:,} chars (context-aware cap)]"
        return True, msg

    def load_directory(
        self,
        path: Path,
        extensions: frozenset[str] = SUPPORTED_EXTENSIONS,
        recursive: bool = False,
        max_chars_per_file: int | None = None,
    ) -> tuple[bool, str]:
        """Load all supported files from a directory into the loaded-files block.

        Args:
            path: Directory to scan.
            extensions: File extensions to include.
            recursive: If True, scan subdirectories as well.
            max_chars_per_file: Per-file character cap; ``None`` uses a shared
                dynamic budget recomputed after each file.

        Returns:
            ``(ok, summary_message)``.
        """
        path = path.expanduser().resolve()

        if not path.exists():
            return False, f"Directory not found: {path}"

        if not path.is_dir():
            return False, f"Path is not a directory: {path}"

        pattern = "**/*" if recursive else "*"
        candidates = [
            p for p in path.glob(pattern)
            if p.is_file() and p.suffix.lower() in extensions
        ]

        if not candidates:
            ext_list = ", ".join(sorted(extensions))
            return False, f"No supported files found in {path} (extensions: {ext_list})"

        loaded: list[str] = []
        skipped: list[str] = []
        for file_path in sorted(candidates):
            cap = (
                max_chars_per_file
                if max_chars_per_file is not None
                else self._dynamic_max_chars_for_next_load()
            )
            if cap <= 0:
                skipped.append(
                    f"{file_path.name}: no context budget remaining for further loads"
                )
                continue
            ok, msg = self.load_file(
                file_path,
                max_chars=cap,
                extensions=frozenset(),
            )
            if ok:
                loaded.append(file_path.name)
            else:
                skipped.append(f"{file_path.name}: {msg}")

        parts = [f"Loaded {len(loaded)}/{len(candidates)} files from '{path.name}'"]
        if loaded:
            parts.append("  Loaded: " + ", ".join(loaded))
        if skipped:
            parts.append("  Skipped: " + "; ".join(skipped))
        return bool(loaded), "\n".join(parts)

    def clear_loaded_files(self) -> None:
        """Remove all loaded files from the context."""
        self._loaded_files.clear()
        self._loaded_file_paths.clear()
        self._sync_loaded_files_budget()

    def list_loaded_files(self) -> list[tuple[str, int]]:
        """Return a list of (filename, char_count) tuples for all loaded files."""
        return [(label, len(content)) for label, content in self._loaded_files.items()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raw_complete(self, prompt: str, max_tokens: int = 500) -> str:
        """Single-turn model call used by the compressor and briefing generator."""
        messages = [{"role": "user", "content": prompt}]
        raw = self._backend.chat(
            messages, max_tokens, max_thinking_tokens=self._max_thinking_tokens
        )
        return strip_think_blocks(raw) or raw

    def _build_messages(
        self, extra_system_suffix: str | None = None
    ) -> list[dict[str, str]]:
        """Build the system + history message list for the model (without current user turn)."""
        if self._focus_document is not None:
            system_content = FOCUS_TECHNICAL_WRITING_PROMPT
            system_content += self._focus_document_section()
        else:
            system_content = self._system_prompt
            if self._context_block:
                system_content += f"\n\n{self._context_block}"
            system_content += self._loaded_files_section()

        if extra_system_suffix:
            system_content += f"\n\n{extra_system_suffix}"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content}
        ]
        messages.extend(self.history.as_messages())
        return messages

    def _log_turn(self, role: str, content: str, timestamp: str) -> None:
        """Append a turn to the conversation JSONL log."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": timestamp, "role": role, "content": content}
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            logger.warning(f"Failed to log conversation turn: {exc}")

    def _log_archived_turns(self, turns: list) -> None:
        """Append archived/evicted turns to conversation.jsonl."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                for turn in turns:
                    entry = {
                        "timestamp": turn.timestamp,
                        "role": turn.role,
                        "content": turn.content,
                        "archived": True,
                    }
                    f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            logger.warning(f"Failed to log archived turns: {exc}")
