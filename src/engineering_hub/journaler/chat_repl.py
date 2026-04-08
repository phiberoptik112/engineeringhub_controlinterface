"""Readline-backed input for `journaler chat`: Up/Down history and persistence."""

from __future__ import annotations

import atexit
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CHAT_HISTORY_MAX = 1000
_JSONL_TAIL_BYTES = 512_000
_JSONL_MAX_USER_PROMPTS = 50

_history_path: Path | None = None
_atexit_registered = False


def _save_history() -> None:
    if _history_path is None:
        return
    try:
        import readline

        readline.write_history_file(str(_history_path))
    except OSError as exc:
        logger.debug("Could not save journaler chat input history: %s", exc)


def extract_user_prompts_from_jsonl_tail(
    path: Path,
    *,
    max_user_prompts: int = _JSONL_MAX_USER_PROMPTS,
    tail_bytes: int = _JSONL_TAIL_BYTES,
) -> list[str]:
    """Return up to *max_user_prompts* user message strings from the end of *path*.

    Reads only the last *tail_bytes* of the file for large logs. Consecutive
    duplicate user contents are collapsed. Multiline content is flattened to
    a single line for readline.
    """
    if not path.is_file():
        return []

    raw = _read_tail_text(path, tail_bytes)
    user_lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("role") != "user":
            continue
        content = obj.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        flattened = " ".join(content.split())
        user_lines.append(flattened)

    sel = user_lines[-max_user_prompts:]
    out: list[str] = []
    prev: str | None = None
    for s in sel:
        if s == prev:
            continue
        out.append(s)
        prev = s
    return out


def _read_tail_text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    if size <= max_bytes:
        return path.read_text(encoding="utf-8")
    with path.open("rb") as f:
        f.seek(max(0, size - max_bytes))
        chunk = f.read().decode("utf-8", errors="replace")
    first_nl = chunk.find("\n")
    if first_nl != -1:
        chunk = chunk[first_nl + 1 :]
    return chunk


def configure_chat_readline(
    state_dir: Path,
    *,
    conversation_jsonl: Path | None = None,
) -> None:
    """Load/save readline history under *state_dir*; optionally seed from JSONL.

    Seeds from *conversation_jsonl* only when there is no prior CLI history file
    (readline history length zero after load), so returning users are not mixed
    with an older transcript tail.
    """
    global _history_path, _atexit_registered

    try:
        import readline
    except ImportError:
        _history_path = None
        return

    state_dir.mkdir(parents=True, exist_ok=True)
    _history_path = state_dir / "chat_input_history"

    try:
        readline.read_history_file(str(_history_path))
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Could not read journaler chat input history: %s", exc)

    readline.set_history_length(_CHAT_HISTORY_MAX)

    seed = (
        conversation_jsonl is not None
        and conversation_jsonl.is_file()
        and readline.get_history_length() <= 0
    )
    if seed:
        for text in extract_user_prompts_from_jsonl_tail(conversation_jsonl):
            readline.add_history(text)

    if not _atexit_registered:
        atexit.register(_save_history)
        _atexit_registered = True


def prompt_line(prompt: str = "You: ") -> str:
    """Read one line; uses readline if ``configure_chat_readline`` was applied."""
    return input(prompt).strip()
