"""Status bar footer widget showing model, utilization, topic, and turns."""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static


class StatusBar(Static):
    """Bottom status bar displaying session state at a glance."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $primary-background;
        color: $text;
        padding: 0 2;
    }
    """

    model_name: reactive[str] = reactive("")
    utilization_pct: reactive[int] = reactive(0)
    turn_count: reactive[int] = reactive(0)
    topic: reactive[str] = reactive("")
    loaded_files: reactive[int] = reactive(0)

    def __init__(self, model_label: str = "", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.model_name = model_label

    def update_status(self, status: dict, model_label: str = "") -> None:
        """Update from engine.get_status() dict."""
        if model_label:
            self.model_name = model_label

        raw_pct = status.get("utilization", "0%")
        self.utilization_pct = int(raw_pct.rstrip("%"))
        self.turn_count = status.get("history_turns", 0)
        self.topic = status.get("current_topic", "") or ""

    def render(self) -> Text:
        filled = round(self.utilization_pct / 10)
        gauge = "\u2593" * filled + "\u2591" * (10 - filled)

        if self.utilization_pct >= 80:
            gauge_color = "red"
        elif self.utilization_pct >= 60:
            gauge_color = "yellow"
        else:
            gauge_color = "green"

        t = Text(overflow="ellipsis", no_wrap=True)
        t.append(" Model: ", style="bold")
        t.append(self.model_name or "unknown", style="cyan")
        t.append("  |  ", style="dim")
        t.append(gauge, style=gauge_color)
        t.append(f" {self.utilization_pct}%", style=gauge_color)
        t.append("  |  ", style="dim")
        t.append(f"Turns: {self.turn_count}", style="white")

        if self.topic:
            t.append("  |  ", style="dim")
            t.append(self.topic, style="italic")

        return t
