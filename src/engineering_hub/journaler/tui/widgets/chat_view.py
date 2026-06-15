"""Chat conversation view with markdown rendering."""

from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import RichLog


class ChatView(RichLog):
    """Scrollable conversation display with Rich markdown rendering."""

    DEFAULT_CSS = """
    ChatView {
        height: 1fr;
        border: solid $primary-background-lighten-2;
        padding: 0 1;
        scrollbar-gutter: stable;
    }
    """

    def on_key(self, event) -> None:
        if event.key == "left":
            self.app.action_focus_sidebar()
            event.stop()

    def on_mount(self) -> None:
        self.write(
            Text.from_markup(
                "[bold cyan]Engineering Hub Journaler[/bold cyan]\n"
                "[dim]Type a message or use slash commands. "
                "Press Ctrl+P for command palette, Ctrl+L for quick context.[/dim]\n"
            )
        )

    def add_user_message(self, text: str) -> None:
        label = Text.from_markup("\n[bold green]You:[/bold green] ")
        self.write(label)
        self.write(Text(text))

    def add_assistant_message(self, text: str) -> None:
        label = Text.from_markup("\n[bold blue]Journaler:[/bold blue]")
        self.write(label)
        try:
            self.write(Markdown(text))
        except Exception:
            self.write(Text(text))

    def add_system_message(self, text: str) -> None:
        label = Text.from_markup("\n[bold yellow]System:[/bold yellow] ")
        self.write(label)
        self.write(Text(text))
