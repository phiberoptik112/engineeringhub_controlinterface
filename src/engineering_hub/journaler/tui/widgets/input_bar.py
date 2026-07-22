"""Input bar widget for the chat interface."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Input


class InputBar(Input):
    """Text input bar at the bottom of the chat view."""

    class Submitted(Message):
        """Posted when the user presses Enter."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        margin: 0 0;
        padding: 0 1;
        height: 3;
        border: solid $accent;
    }
    InputBar:focus {
        border: double $accent;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(
            placeholder="Type a message or /command...",
            **kwargs,  # type: ignore[arg-type]
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.post_message(self.Submitted(event.value))
            self.value = ""
        event.stop()
