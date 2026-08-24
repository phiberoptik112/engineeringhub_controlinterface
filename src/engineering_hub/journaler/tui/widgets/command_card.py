"""Command card and grid widgets for displaying slash commands by category."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from engineering_hub.journaler.chat_repl import COMMAND_CATALOG
from engineering_hub.journaler.file_browser import CommandEntry

NO_ARG_COMMANDS = frozenset({
    "/status", "/budget", "/topic", "/summarize", "/skills",
    "/help", "/exit", "/quit", "/capture_list", "/load_browse",
    "/edit_browse", "/agent_browse", "/capture_browse", "/model_browse", "/files",
})

MODEL_PICKER_COMMANDS = frozenset({"/model"})


class CommandCard(Widget):
    """A clickable card representing a single slash command."""

    DEFAULT_CSS = """
    CommandCard {
        height: auto;
        min-height: 3;
        padding: 1 2;
        margin: 0 1 1 0;
        border: solid $primary-background-lighten-2;
        background: $surface;
    }
    CommandCard:hover {
        background: $primary-background-lighten-1;
        border: solid $accent;
    }
    CommandCard:focus {
        background: $primary-background-lighten-2;
        border: double $accent;
    }
    CommandCard .card-name {
        text-style: bold;
        color: $accent;
    }
    CommandCard .card-args {
        color: $text-muted;
    }
    CommandCard .card-desc {
        color: $text;
        margin-top: 1;
    }
    """

    can_focus = True

    def __init__(self, entry: CommandEntry, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.entry = entry

    def compose(self):
        yield Static(self.entry.name, classes="card-name")
        if self.entry.args_hint:
            yield Static(self.entry.args_hint, classes="card-args")
        yield Static(self.entry.description, classes="card-desc")

    def on_click(self) -> None:
        self._select()

    def on_key(self, event) -> None:
        if event.key == "enter":
            self._select()
            event.stop()
        elif event.key in ("down", "right"):
            self._move_focus(1)
            event.stop()
        elif event.key in ("up", "left"):
            self._move_focus(-1)
            event.stop()

    def _move_focus(self, direction: int) -> None:
        """Move focus to the next/previous CommandCard in the grid."""
        try:
            grid = self.query_ancestor(CommandGrid)
        except NoMatches:
            return
        cards = list(grid.query(CommandCard))
        if not cards:
            return
        try:
            idx = cards.index(self)
        except ValueError:
            return
        new_idx = idx + direction
        if 0 <= new_idx < len(cards):
            cards[new_idx].focus()
            cards[new_idx].scroll_visible()
        elif new_idx < 0:
            self.app.action_focus_sidebar()

    def _select(self) -> None:
        try:
            grid = self.query_ancestor(CommandGrid)
        except NoMatches:
            return
        needs_input = self.entry.name not in NO_ARG_COMMANDS
        grid.post_message(CommandGrid.CommandSelected(self.entry, needs_input))


class CommandGrid(VerticalScroll):
    """Scrollable grid of command cards, filtered by category."""

    can_focus = True

    class CommandSelected(Message):
        """Posted when a command card is activated."""

        def __init__(self, command_entry: CommandEntry, needs_input: bool) -> None:
            super().__init__()
            self.command_entry = command_entry
            self.needs_input = needs_input

    current_category: reactive[str] = reactive("Context Management")

    def compose(self):
        yield Static("Select a category from the sidebar", id="grid-placeholder")

    def on_focus(self) -> None:
        """When the grid receives focus, pass it to the first card."""
        cards = list(self.query(CommandCard))
        if cards:
            cards[0].focus()

    def show_category(self, category: str) -> None:
        self.current_category = category
        self.call_after_refresh(self._rebuild)

    async def _rebuild(self) -> None:
        await self.remove_children()
        commands = [e for e in COMMAND_CATALOG if e.category == self.current_category]
        if not commands:
            await self.mount(Static(f"No commands in '{self.current_category}'"))
            return

        await self.mount(
            Static(
                f"[bold]{self.current_category}[/bold]  "
                f"({len(commands)} command{'s' if len(commands) != 1 else ''})",
                id="category-header",
            )
        )
        for entry in commands:
            await self.mount(CommandCard(entry))
        cards = list(self.query(CommandCard))
        if cards:
            cards[0].focus()
