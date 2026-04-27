"""Readline-backed input for `journaler chat`: Up/Down history and persistence."""

from __future__ import annotations

import atexit
import json
import logging
from pathlib import Path

from engineering_hub.journaler.file_browser import CommandEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Palette sentinel — emitted by the Ctrl+P readline macro
# ---------------------------------------------------------------------------

_PALETTE_SENTINEL = "///cmdpalette"

# ---------------------------------------------------------------------------
# Command catalog — single source of truth for the palette and Tab completer
# ---------------------------------------------------------------------------

COMMAND_CATALOG: list[CommandEntry] = [
    # Context Management
    CommandEntry("/model", "[profile|path]", "Show or switch model profile", "Context Management"),
    CommandEntry("/status", "", "Show context pressure and turn count", "Context Management"),
    CommandEntry("/budget", "", "Token budget breakdown", "Context Management"),
    CommandEntry("/topic", "", "Show the currently detected conversation topic", "Context Management"),
    CommandEntry("/clear", "[--hard|--summarize]", "Clear conversation history", "Context Management"),
    CommandEntry("/files", "[clear]", "List or clear loaded files", "Context Management"),
    # File Ops
    CommandEntry("/load", "<path> [-r]", "Load a file or directory into context", "File Ops"),
    CommandEntry("/load_browse", "", "Interactive browser for org-roam files", "File Ops"),
    CommandEntry("/edit_browse", "", "Interactive browser to set /edit target", "File Ops"),
    CommandEntry("/find", "<title fragment>", "Search org-roam files by title", "File Ops"),
    # Agent Delegation
    CommandEntry("/agent", "<type> <description>", "Delegate to an agent persona", "Agent Delegation"),
    CommandEntry(
        "/pipeline",
        'draft-section --section "<section>" [--project <id>] [--backend mlx|claude] [--loop-limit <n>]',
        "Run multi-stage report drafting pipeline (writer→checker→reviewer→latex)",
        "Agent Delegation",
    ),
    CommandEntry("/tasks", "[confirm|commit|rollback|…]", "Overnight task queue (pending-tasks.org)", "Agent Delegation"),
    CommandEntry("/queue", "<description>", "Propose a task for the overnight queue", "Agent Delegation"),
    CommandEntry("/skills", "", "List available agent personas", "Agent Delegation"),
    CommandEntry("/agent_browse", "", "Interactive skill picker for agent delegation", "Agent Delegation"),
    CommandEntry("/validate-latex", "<path>", "Compile a .tex file and report errors", "Agent Delegation"),
    # Capture Templates
    CommandEntry("/capture", "<name> [field=value ...]", "Apply a capture template", "Capture Templates"),
    CommandEntry("/capture_list", "", "List available capture templates", "Capture Templates"),
    CommandEntry("/capture_browse", "", "Interactive capture template picker", "Capture Templates"),
    # Org-Roam Write
    CommandEntry("/open", "[today|clear|<path>|<title>]", "Set session org-roam target for /edit", "Org-Roam Write"),
    CommandEntry("/edit", "<heading> :: <text>", "Append text under a heading in the open target", "Org-Roam Write"),
    CommandEntry("/task", "<description>", "Add a TODO to today's journal", "Org-Roam Write"),
    CommandEntry("/done", "<fragment>", "Mark a matching TODO as done", "Org-Roam Write"),
    CommandEntry("/note", "<heading> :: <text>", "Append text under a heading in today's journal", "Org-Roam Write"),
    # Export
    CommandEntry(
        "/export",
        "[--format raw|--summarize] [-o <path>] …",
        "Export transcript to org-roam (default: conversation_exports/)",
        "Export",
    ),
    # Session
    CommandEntry("/help", "", "Show available slash commands", "Session"),
    CommandEntry("/exit", "", "Leave the chat session", "Session"),
    CommandEntry("/quit", "", "Leave the chat session", "Session"),
]

# ---------------------------------------------------------------------------
# Pending readline insertion (set after palette selection)
# ---------------------------------------------------------------------------

_pending_insertion: str | None = None


def set_pending_insertion(text: str) -> None:
    """Queue *text* to be pre-filled into the next readline prompt."""
    global _pending_insertion
    _pending_insertion = text


def _pre_input_hook() -> None:
    global _pending_insertion
    if _pending_insertion is not None:
        try:
            import readline  # noqa: PLC0415
            readline.insert_text(_pending_insertion)
            readline.redisplay()
        except Exception:
            pass
        _pending_insertion = None


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


def _slash_completer(text: str, state: int) -> str | None:
    """Tab-complete slash commands against the command catalog."""
    if not text.startswith("/"):
        return None
    matches = [e.name for e in COMMAND_CATALOG if e.name.startswith(text)]
    return matches[state] if state < len(matches) else None


def configure_chat_readline(
    state_dir: Path,
    *,
    conversation_jsonl: Path | None = None,
) -> None:
    """Load/save readline history under *state_dir*; optionally seed from JSONL.

    Seeds from *conversation_jsonl* only when there is no prior CLI history file
    (readline history length zero after load), so returning users are not mixed
    with an older transcript tail.

    Also registers:
    - Tab completion for slash commands via ``_slash_completer``
    - Ctrl+P keybinding that emits ``_PALETTE_SENTINEL`` to trigger the palette
    - ``_pre_input_hook`` for pre-filling the readline buffer after palette selection
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

    # Tab completion for slash commands
    readline.set_completer(_slash_completer)
    readline.parse_and_bind("tab: complete")

    # Ctrl+P emits the palette sentinel as a submitted line
    readline.parse_and_bind(rf'"\C-p": "{_PALETTE_SENTINEL}\n"')

    # Pre-input hook populates the buffer after palette selection
    readline.set_pre_input_hook(_pre_input_hook)

    if not _atexit_registered:
        atexit.register(_save_history)
        _atexit_registered = True


def prompt_line(prompt: str = "You: ") -> str:
    """Read one line; uses readline if ``configure_chat_readline`` was applied."""
    return input(prompt).strip()
