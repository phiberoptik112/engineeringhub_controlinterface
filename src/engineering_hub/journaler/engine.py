"""ConversationEngine: manages a persistent conversation with a local MLX model.

The engine keeps the model loaded ("warm") between calls, maintains a rolling
conversation history, and refreshes its context block on each scan cycle.
Briefing generation uses a separate prompt path to avoid polluting chat history.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from engineering_hub.core.exceptions import LLMBackendError

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".txt", ".org", ".py", ".yaml", ".yml", ".json", ".tex", ".csv", ".toml", ".rst"}
)

logger = logging.getLogger(__name__)


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


@dataclass
class _Turn:
    """A single conversation turn (user or assistant)."""

    role: str
    content: str
    timestamp: str


class ConversationalMLXBackend:
    """MLX backend with multi-turn chat support.

    Reuses the same loaded model/tokenizer from mlx-lm but accepts a full
    message history instead of a single system+user pair.  The model is
    loaded once on init and stays resident in memory.
    """

    def __init__(
        self,
        model_path: str,
        temp: float = 0.7,
        top_p: float = 0.9,
        min_p: float = 0.05,
        repetition_penalty: float = 1.1,
        repetition_context_size: int = 20,
    ) -> None:
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

        self._temp = temp
        self._top_p = top_p
        self._min_p = min_p
        self._repetition_penalty = repetition_penalty
        self._repetition_context_size = repetition_context_size

        resolved = str(Path(model_path).expanduser())
        load_path = resolved if Path(resolved).is_dir() else model_path

        logger.info(f"Loading Journaler MLX model from: {load_path}")
        try:
            self._model, self._tokenizer = mlx_lm.load(load_path)
        except Exception as exc:
            raise LLMBackendError(
                f"Failed to load Journaler model from '{load_path}': {exc}",
                provider="mlx",
            ) from exc
        logger.info(f"Journaler model loaded: {model_path}")

    def chat(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        """Generate a response given a full message history.

        Args:
            messages: List of {role, content} dicts (system, user, assistant).
            max_tokens: Maximum tokens to generate.

        Returns:
            The model's response text.
        """
        prompt = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        sampler = self._make_sampler(
            temp=self._temp, top_p=self._top_p, min_p=self._min_p
        )
        logits_processors = self._make_logits_processors(
            repetition_penalty=self._repetition_penalty,
            repetition_context_size=self._repetition_context_size,
        )

        try:
            return self._mlx_lm.generate(
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

    def is_loaded(self) -> bool:
        return self._model is not None


class ConversationEngine:
    """Manages a persistent conversation session with a local model.

    Context is refreshed every scan cycle; conversation history is maintained
    in memory and logged to conversation.jsonl.
    """

    def __init__(
        self,
        backend: ConversationalMLXBackend,
        system_prompt: str,
        log_dir: Path,
        max_history: int = 20,
        max_tokens: int = 4000,
    ) -> None:
        self._backend = backend
        self._system_prompt = system_prompt
        self._context_block = ""
        self._loaded_files: dict[str, str] = {}  # label -> content
        self._history: list[_Turn] = []
        self._max_history = max_history
        self._max_tokens = max_tokens
        self._log_dir = log_dir
        self._log_file = log_dir / "conversation.jsonl"

    def update_context(self, context_block: str) -> None:
        """Replace the rolling context section of the system prompt."""
        self._context_block = context_block

    def chat(self, message: str) -> str:
        """Send a user message and get a response.

        Appends both to conversation history and logs to conversation.jsonl.
        """
        now = datetime.now().isoformat(timespec="seconds")

        self._history.append(_Turn(role="user", content=message, timestamp=now))

        messages = self._build_messages()
        response = self._backend.chat(messages, self._max_tokens)

        resp_time = datetime.now().isoformat(timespec="seconds")
        self._history.append(_Turn(role="assistant", content=response, timestamp=resp_time))

        self._trim_history()
        self._log_turn("user", message, now)
        self._log_turn("assistant", response, resp_time)

        return response

    def generate_briefing(self, briefing_context: str, briefing_prompt: str) -> str:
        """Generate a morning briefing using a separate prompt.

        Uses a richer context and a dedicated prompt template.
        Does NOT pollute the chat history — briefing is a one-shot generation.
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Journaler — an always-on engineering assistant. "
                    "Generate a concise, actionable morning briefing."
                ),
            },
            {
                "role": "user",
                "content": f"{briefing_prompt}\n\n{briefing_context}",
            },
        ]
        return self._backend.chat(messages, self._max_tokens)

    def get_history_summary(self) -> str:
        """Return a brief summary of recent conversation for status display."""
        if not self._history:
            return "No conversation history."
        count = len(self._history)
        last = self._history[-1]
        return f"{count} turns, last at {last.timestamp} ({last.role})"

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def load_file(
        self,
        path: Path,
        max_chars: int = 50_000,
        extensions: frozenset[str] = SUPPORTED_EXTENSIONS,
    ) -> tuple[bool, str]:
        """Read a single file and add its content to the loaded-files block.

        Args:
            path: Absolute or relative path to the file.
            max_chars: Maximum characters to read (content is truncated with a notice).
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

        size_kb = len(content) / 1024
        msg = f"Loaded '{label}' ({size_kb:.1f} KB)"
        if truncated:
            msg += f" [truncated to {max_chars:,} chars]"
        return True, msg

    def load_directory(
        self,
        path: Path,
        extensions: frozenset[str] = SUPPORTED_EXTENSIONS,
        recursive: bool = False,
        max_chars_per_file: int = 50_000,
    ) -> tuple[bool, str]:
        """Load all supported files from a directory into the loaded-files block.

        Args:
            path: Directory to scan.
            extensions: File extensions to include.
            recursive: If True, scan subdirectories as well.
            max_chars_per_file: Per-file character cap.

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
            ok, msg = self.load_file(file_path, max_chars=max_chars_per_file, extensions=frozenset())
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

    def list_loaded_files(self) -> list[tuple[str, int]]:
        """Return a list of (filename, char_count) tuples for all loaded files."""
        return [(label, len(content)) for label, content in self._loaded_files.items()]

    def _build_messages(self) -> list[dict[str, str]]:
        """Build the full message list for the model."""
        system_content = self._system_prompt
        if self._context_block:
            system_content += f"\n\n{self._context_block}"

        if self._loaded_files:
            blocks = ["\n\n## Loaded Files\n"]
            for label, content in self._loaded_files.items():
                blocks.append(f"### {label}\n```\n{content}\n```")
            system_content += "\n".join(blocks)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content}
        ]

        for turn in self._history:
            messages.append({"role": turn.role, "content": turn.content})

        return messages

    def _trim_history(self) -> None:
        """Keep only the last max_history turns (pairs of user+assistant)."""
        max_turns = self._max_history * 2
        if len(self._history) > max_turns:
            self._history = self._history[-max_turns:]

    def _log_turn(self, role: str, content: str, timestamp: str) -> None:
        """Append a turn to the conversation JSONL log."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": timestamp,
            "role": role,
            "content": content,
        }
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            logger.warning(f"Failed to log conversation turn: {exc}")
