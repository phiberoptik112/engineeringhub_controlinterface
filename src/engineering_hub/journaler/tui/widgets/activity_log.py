"""Activity log widget — compact strip showing command and file-load operation status."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import RichLog


class ActivityLog(RichLog):
    """Compact scrollable strip showing recent command and file-load activity.

    Each call to ``log_operation`` appends a single-line timestamped entry.
    The panel is always visible and auto-scrolls to the newest entry.
    """

    DEFAULT_CSS = """
    ActivityLog {
        height: 5;
        min-height: 3;
        background: $surface;
        padding: 0 1;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
    }
    """

    visible_log: reactive[bool] = reactive(True)

    _STATUS_STYLE: dict[str, tuple[str, str]] = {
        "ok":      ("green",   "✓"),
        "error":   ("red",     "✗"),
        "info":    ("yellow",  "ℹ"),
        "running": ("cyan",    "⟳"),
        "warn":    ("orange",  "⚠"),
    }

    _KIND_STYLE: dict[str, str] = {
        "cmd":   "cyan",
        "load":  "magenta",
        "chat":  "blue",
        "sys":   "yellow",
    }

    def on_mount(self) -> None:
        self.auto_scroll = True
        self._write_header()

    def _write_header(self) -> None:
        t = Text(no_wrap=True)
        t.append(" Activity Log", style="bold dim")
        t.append(
            " — commands, file loads, and agent responses",
            style="dim",
        )
        self.write(t)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_operation(
        self,
        kind: str,
        label: str,
        status: str = "info",
        detail: str = "",
    ) -> None:
        """Append a one-line status entry.

        Args:
            kind:   One of "cmd", "load", "chat", "sys".
            label:  Short identifying label (command name, filename, …).
            status: One of "ok", "error", "info", "running", "warn".
            detail: Optional trailing detail text (result summary, char count, …).
        """
        color, icon = self._STATUS_STYLE.get(status, ("white", "•"))
        kind_color = self._KIND_STYLE.get(kind, "white")
        ts = datetime.now().strftime("%H:%M:%S")

        t = Text(overflow="ellipsis", no_wrap=True)
        t.append(f" {ts} ", style="dim")
        t.append(f"{icon} ", style=f"bold {color}")
        t.append(f"[{kind}] ", style=f"bold {kind_color}")
        t.append(label, style="bold white")
        if detail:
            t.append("  ", style="dim")
            t.append(detail, style="dim")

        self.write(t)

    def log_command(self, command: str, status: str = "ok", detail: str = "") -> None:
        """Convenience wrapper for slash-command results."""
        label = command.split()[0] if command else command
        self.log_operation("cmd", label, status=status, detail=detail)

    def log_file_load(self, path: str, status: str = "ok", detail: str = "") -> None:
        """Convenience wrapper for /load and context-file operations."""
        import os
        name = os.path.basename(path) or path
        self.log_operation("load", name, status=status, detail=detail)

    def log_chat_response(self, status: str = "ok", response_text: str = "") -> None:
        """Log an AI chat response, showing the first line(s) as a preview.

        If ``response_text`` is provided the first meaningful sentence (up to
        120 chars) is displayed beneath the status line as a dim preview so the
        user can read the output without switching to the chat view.
        """
        if not response_text:
            self.log_operation("chat", "response", status=status)
            return

        words = len(response_text.split())
        self.log_operation("chat", "response", status=status, detail=f"{words} words")

        # Build a compact one-line preview from the first non-empty lines.
        preview_chars = 120
        lines = [ln.strip() for ln in response_text.splitlines() if ln.strip()]
        preview = " ".join(lines)[:preview_chars]
        if len(" ".join(lines)) > preview_chars:
            preview += "…"

        if preview:
            t = Text(no_wrap=True, overflow="ellipsis")
            t.append("         ", style="")  # indent beneath the status line
            t.append(preview, style="dim italic")
            self.write(t)
