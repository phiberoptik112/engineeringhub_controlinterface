"""Command palette modal screen with fuzzy-filter search."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from engineering_hub.journaler.chat_repl import COMMAND_CATALOG
from engineering_hub.journaler.file_browser import CommandEntry


def _fuzzy_match(query: str, text: str) -> bool:
    """Return True if all chars of query appear in text as a subsequence."""
    if not query:
        return True
    text_lower = text.lower()
    query_lower = query.lower()
    pos = 0
    for ch in query_lower:
        idx = text_lower.find(ch, pos)
        if idx == -1:
            return False
        pos = idx + 1
    return True


class PaletteItem(Static):
    """A single item in the palette results list."""

    DEFAULT_CSS = """
    PaletteItem {
        height: auto;
        padding: 0 2;
        margin: 0;
    }
    PaletteItem:hover {
        background: $accent 20%;
    }
    PaletteItem.--highlighted {
        background: $accent 30%;
        text-style: bold;
    }
    """

    can_focus = True

    def __init__(self, entry: CommandEntry, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.entry = entry

    def on_mount(self) -> None:
        args = f" {self.entry.args_hint}" if self.entry.args_hint else ""
        self.update(
            f"[bold $accent]{self.entry.name}[/bold $accent]{args}  "
            f"[dim]{self.entry.description}[/dim]  "
            f"[italic $text-muted]({self.entry.category})[/italic $text-muted]"
        )

    def on_click(self) -> None:
        self._select()

    def on_key(self, event) -> None:
        if event.key == "enter":
            self._select()
            event.stop()
        elif event.key == "down":
            screen = self.ancestors_with_type(PaletteScreen)
            if screen:
                next(iter(screen))._move_highlight(1)
            event.stop()
        elif event.key == "up":
            screen = self.ancestors_with_type(PaletteScreen)
            if screen:
                next(iter(screen))._move_highlight(-1)
            event.stop()

    def _select(self) -> None:
        screen = self.ancestors_with_type(PaletteScreen)
        if screen:
            next(iter(screen))._select_item(self)


class PaletteScreen(ModalScreen[str | None]):
    """Fuzzy-filter command palette overlay."""

    DEFAULT_CSS = """
    PaletteScreen {
        align: center middle;
    }
    #palette-container {
        width: 80;
        max-width: 90%;
        height: 24;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #palette-input {
        margin-bottom: 1;
    }
    #palette-results {
        height: 1fr;
    }
    """

    BINDINGS = [("escape", "dismiss_palette", "Close")]

    def __init__(self) -> None:
        super().__init__()
        self._highlight_index: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Input(
                placeholder="Type to filter commands...",
                id="palette-input",
            )
            yield VerticalScroll(id="palette-results")

    def on_mount(self) -> None:
        self._render_results("")
        self.query_one("#palette-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-input":
            self._highlight_index = 0
            self._render_results(event.value)

    def on_key(self, event) -> None:
        """Handle Up/Down arrows while the input field has focus."""
        if event.key == "down":
            self._move_highlight(1)
            event.stop()
        elif event.key == "up":
            self._move_highlight(-1)
            event.stop()

    def _move_highlight(self, direction: int) -> None:
        """Move the visual highlight by direction (+1 or -1) through results."""
        items = list(self.query(PaletteItem))
        if not items:
            return
        new_idx = self._highlight_index + direction
        new_idx = max(0, min(new_idx, len(items) - 1))
        self._highlight_index = new_idx
        self._apply_highlight(items)

    def _apply_highlight(self, items: list[PaletteItem] | None = None) -> None:
        """Apply the --highlighted class to the current item, remove from others."""
        if items is None:
            items = list(self.query(PaletteItem))
        for i, item in enumerate(items):
            if i == self._highlight_index:
                item.add_class("--highlighted")
                item.scroll_visible()
            else:
                item.remove_class("--highlighted")

    def _select_item(self, item: PaletteItem) -> None:
        """Dismiss the palette with the selected command."""
        args_suffix = f" {item.entry.args_hint}" if item.entry.args_hint else ""
        self.dismiss(f"{item.entry.name}{args_suffix}")

    def _render_results(self, query: str) -> None:
        results_container = self.query_one("#palette-results", VerticalScroll)
        results_container.remove_children()

        filtered = [
            entry
            for entry in COMMAND_CATALOG
            if _fuzzy_match(query, f"{entry.name} {entry.description} {entry.category}")
        ]

        if not filtered:
            results_container.mount(
                Static("[dim]No matching commands[/dim]")
            )
            return

        current_category = ""
        for entry in filtered:
            if not query and entry.category != current_category:
                current_category = entry.category
                results_container.mount(
                    Static(
                        f"\n[bold cyan]{current_category}[/bold cyan]",
                        classes="palette-category-header",
                    )
                )
            results_container.mount(PaletteItem(entry))

        self._apply_highlight()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        items = list(self.query(PaletteItem))
        if items and 0 <= self._highlight_index < len(items):
            self._select_item(items[self._highlight_index])
        event.stop()

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)
