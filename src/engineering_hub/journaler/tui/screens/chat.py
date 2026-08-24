"""Chat screen — placeholder for when chat is a full screen (e.g. on push)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen

from engineering_hub.journaler.tui.widgets.chat_view import ChatView
from engineering_hub.journaler.tui.widgets.input_bar import InputBar


class ChatScreen(Screen):
    """Standalone chat screen (used if pushed onto the screen stack)."""

    def compose(self) -> ComposeResult:
        yield ChatView(id="chat-view-screen")
        yield InputBar(id="input-bar-screen")

    def on_mount(self) -> None:
        self.query_one(InputBar).focus()
