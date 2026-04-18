"""ConversationEngine: manages a persistent conversation with a local MLX model.

The engine keeps the model loaded ("warm") between calls, maintains a rolling
conversation history with automatic token-pressure management, and refreshes
its context block on each scan cycle. Briefing generation uses a separate
prompt path to avoid polluting chat history.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from corpus.service import CorpusService

from engineering_hub.core.exceptions import LLMBackendError
from engineering_hub.journaler.context_manager import (
    ClearStrategy,
    ContextCompressor,
    ContextPressureManager,
    ConversationHistory,
    PressureConfig,
    TokenBudget,
    TopicTracker,
    estimate_tokens,
    execute_clear,
)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".txt", ".org", ".py", ".yaml", ".yml", ".json", ".tex", ".csv", ".toml", ".rst"}
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadFileBudgetConfig:
    """How much of the remaining context window may be used per `/load` operation.

    Caps are expressed using the same ``len(text) // 3`` heuristic as
    :func:`~engineering_hub.journaler.context_manager.estimate_tokens`.
    """

    max_context_fraction: float = 0.40
    max_chars_absolute: int = 200_000
    min_chars: int = 1024
    slack_tokens: int = 256

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
    {"gemma4", "paligemma", "llava", "idefics", "blip", "flamingo", "internvl", "qwen2_vl"}
)


def _detect_vlm(load_path: str) -> bool:
    """Return True if the model at *load_path* is a Vision-Language Model.

    Peeks at config.json without loading model weights — works for both local
    directories and HF Hub cache entries.  Falls back to False on any error so
    a mis-detection never hard-blocks startup.
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
        if "vision_config" in cfg:
            return True
        model_type = cfg.get("model_type", "").lower()
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
            raise LLMBackendError(
                f"Failed to load Journaler VLM from '{load_path}': {exc}",
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

    def chat(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        """Generate a response given a full message history.

        Args:
            messages: List of {role, content} dicts (system, user, assistant).
            max_tokens: Maximum tokens to generate.

        Returns:
            The model's response text.
        """
        prompt = self._apply_chat_template_safe(messages)

        if self._is_vlm:
            return self._chat_vlm(prompt, max_tokens)
        return self._chat_lm(prompt, max_tokens)

    def _chat_lm(self, prompt: str, max_tokens: int) -> str:
        sampler = self._make_sampler(  # type: ignore[misc]
            temp=self._temp, top_p=self._top_p, min_p=self._min_p
        )
        logits_processors = self._make_logits_processors(  # type: ignore[misc]
            repetition_penalty=self._repetition_penalty,
            repetition_context_size=self._repetition_context_size,
        )
        try:
            return self._mlx_lm.generate(  # type: ignore[union-attr]
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=sampler,
                logits_processors=logits_processors,
            )
        except Exception as exc:
            raise LLMBackendError(
                f"Journaler MLX generation failed: {exc}", provider="mlx"
            ) from exc

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
        pressure_config: PressureConfig | None = None,
        model_context_window: int = 32768,
        corpus_service: CorpusService | None = None,
        load_file_budget: LoadFileBudgetConfig | None = None,
    ) -> None:
        self._backend = backend
        self._system_prompt = system_prompt
        self._context_block = ""
        self._corpus_service = corpus_service
        self._loaded_files: dict[str, str] = {}
        self._max_tokens = max_tokens
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
        self._roam_edit_target: Path | None = None

    def get_roam_edit_target(self) -> Path | None:
        """Session target for ``/edit`` (set via ``/open`` in journaler chat)."""
        return self._roam_edit_target

    def set_roam_edit_target(self, path: Path | None) -> None:
        """Set or clear the org-roam file ``/edit`` appends to."""
        if path is None:
            self._roam_edit_target = None
        else:
            self._roam_edit_target = path.expanduser().resolve()

    def replace_backend(
        self,
        backend: ConversationalMLXBackend,
        *,
        model_context_window: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Swap the MLX backend (e.g. after ``/model``) while keeping conversation history."""
        self._backend = backend
        if max_tokens is not None:
            self._max_tokens = max_tokens
            self.budget.reserved_for_generation = max(
                self._pressure_config.reserved_for_generation, max_tokens
            )
        if model_context_window is not None:
            self._pressure_config.model_context_window = model_context_window
            self.budget.window_size = model_context_window

    def update_context(self, context_block: str) -> None:
        """Replace the rolling context section of the system prompt."""
        self._context_block = context_block
        self.budget.context_snapshot_tokens = estimate_tokens(context_block)

    def chat(self, message: str) -> str:
        """Send a user message and get a response.

        Runs pre-call pressure checks (compression / trim if needed), builds
        the message array, calls the model, runs post-call topic tracking,
        and flushes archived turns to conversation.jsonl.
        """
        self.budget.corpus_injection_tokens = 0
        self.budget.history_tokens = self.history.total_tokens
        self._sync_loaded_files_budget()

        extra_suffix: str | None = None
        cs = self._corpus_service
        if cs is not None and cs.is_available() and message.strip():
            try:
                results = cs.search(message)
                if results:
                    extra_suffix = cs.format_for_context(results)
            except Exception as exc:
                logger.warning("Journaler corpus RAG failed (non-fatal): %s", exc)

        if extra_suffix:
            self.budget.corpus_injection_tokens = estimate_tokens(extra_suffix)
        else:
            self.budget.corpus_injection_tokens = 0

        # Pre-call: check pressure, compress/trim if necessary
        pre_actions = self.pressure_manager.pre_call_check()

        messages = self._build_messages(extra_system_suffix=extra_suffix)
        messages.append({"role": "user", "content": message})

        response = self._backend.chat(messages, self._max_tokens)

        # Record in history (post-generation so history doesn't include this turn in the call)
        now = datetime.now().isoformat(timespec="seconds")
        resp_time = datetime.now().isoformat(timespec="seconds")
        self.history.add("user", message)
        self.history.add("assistant", response)
        self.budget.history_tokens = self.history.total_tokens

        # Post-call: topic tracking
        post_actions = self.pressure_manager.post_call_check(message, response)

        # Log raw turns to JSONL
        self._log_turn("user", message, now)
        self._log_turn("assistant", response, resp_time)

        # Flush evicted/archived turns to JSONL
        archived = self.history.flush_archive()
        if archived:
            self._log_archived_turns(archived)

        # Prepend action notifications when configured
        all_actions = pre_actions + post_actions
        if all_actions and self._pressure_config.notify_user_on_action:
            action_text = "\n".join(all_actions)
            response = f"{action_text}\n\n{response}"

        self.budget.corpus_injection_tokens = 0
        return response

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
        return execute_clear(
            strategy=strategy,
            history=self.history,
            compressor=self.compressor,
            last_scan_time=last_scan,
        )

    def get_status(self) -> dict:
        """Return current context management state for display / /status command."""
        self.budget.history_tokens = self.history.total_tokens
        self._sync_loaded_files_budget()
        return {
            "context_window": self.budget.window_size,
            "utilization": f"{self.budget.utilization:.0%}",
            "pressure": self.budget.pressure,
            "history_turns": len(self.history.turns),
            "history_tokens": self.history.total_tokens,
            "context_snapshot_tokens": self.budget.context_snapshot_tokens,
            "system_prompt_tokens": self.budget.system_prompt_tokens,
            "loaded_files_tokens": self.budget.loaded_files_tokens,
            "corpus_injection_tokens": self.budget.corpus_injection_tokens,
            "available_tokens": self.budget.available,
            "compressions_today": self.compressor.compression_count,
            "current_topic": self.topic_tracker.current_topic,
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
                    "work blocks and flag items that can be delegated to agents."
                ),
            },
            {
                "role": "user",
                "content": briefing_prompt,
            },
        ]
        return self._backend.chat(messages, gen_tokens)

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

    def build_delegate_context(self, task_description: str) -> str:
        """Assemble reference material for delegated agent tasks.

        Includes files the user loaded via ``/load`` in this Journaler session,
        plus semantic-search excerpts from ``corpus.db`` when a corpus service
        is configured (same RAG path as chat turns).
        """
        parts: list[str] = []
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

        return "\n".join(parts).strip()

    def _sync_loaded_files_budget(self) -> None:
        self.budget.loaded_files_tokens = estimate_tokens(self._loaded_files_section())

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
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return False, f"Could not read {path.name}: {exc}"

        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        label = path.name
        self._loaded_files[label] = content
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
        return self._backend.chat(messages, max_tokens)

    def _build_messages(
        self, extra_system_suffix: str | None = None
    ) -> list[dict[str, str]]:
        """Build the system + history message list for the model (without current user turn)."""
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
