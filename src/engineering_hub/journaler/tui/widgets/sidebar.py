"""Category sidebar with tree navigation for slash command categories."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Static, Tree

from engineering_hub.journaler.chat_repl import COMMAND_CATALOG

CATEGORY_ICONS = {
    "Quick Context": "\u26a1",
    "Context Management": "\u2699",
    "File Ops": "\U0001f4c2",
    "Agent Delegation": "\U0001f916",
    "Zettelkasten": "\U0001f4dd",
    "Capture Templates": "\U0001f4cb",
    "Org-Roam Write": "\u270f",
    "Export": "\U0001f4e4",
    "Session": "\U0001f6aa",
}


def _get_categories() -> list[str]:
    """Return ordered unique categories from the command catalog."""
    seen: set[str] = set()
    categories: list[str] = []
    for entry in COMMAND_CATALOG:
        if entry.category not in seen:
            seen.add(entry.category)
            categories.append(entry.category)
    return categories


class BrowserNavBar(Static):
    """Shown at the top of the sidebar when an interactive browser is open.

    Displays the current browser mode and reminds the user how to cancel.
    Call ``set_mode(label)`` / ``clear_mode()`` from the app.
    """

    DEFAULT_CSS = """
    BrowserNavBar {
        height: auto;
        min-height: 4;
        padding: 1;
        background: $primary-background;
        color: $accent;
        border-bottom: solid $primary-background-lighten-2;
        text-style: bold;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        display = bool(kwargs.pop("display", False))
        super().__init__("", **kwargs)
        self.display = display

    def set_mode(self, label: str, path: str = "") -> None:
        lines = [
            "[bold cyan]← Browser Active[/bold cyan]",
            f"[dim]{label}[/dim]",
        ]
        if path:
            short = path if len(path) <= 22 else "…" + path[-20:]
            lines.append(f"[dim]{short}[/dim]")
        lines.append("")
        lines.append("[dim]Esc · cancel browser[/dim]")
        self.update("\n".join(lines))
        self.display = True

    def clear_mode(self) -> None:
        self.update("")
        self.display = False


class CategorySidebar(Tree):
    """Persistent left-hand navigation tree showing command categories."""

    class CategorySelected(Message):
        """Posted when a category is selected in the sidebar."""

        def __init__(self, category: str) -> None:
            super().__init__()
            self.category = category

    def __init__(self, **kwargs: object) -> None:
        super().__init__("Commands", **kwargs)  # type: ignore[arg-type]
        self.show_root = False

    def on_mount(self) -> None:
        self._build_tree()
        self.root.expand()

    def _build_tree(self) -> None:
        context_node = self.root.add(
            f"{CATEGORY_ICONS.get('Quick Context', '')} Quick Context",
            data="Quick Context",
        )
        context_node.allow_expand = False

        for category in _get_categories():
            icon = CATEGORY_ICONS.get(category, "\u2022")
            commands = [e for e in COMMAND_CATALOG if e.category == category]
            node = self.root.add(f"{icon} {category}", data=category)
            for cmd in commands:
                node.add_leaf(f"  {cmd.name}", data=cmd)

    def on_key(self, event) -> None:
        if event.key == "right":
            self.app.action_focus_chat()
            event.stop()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        if node.data is None:
            return

        if isinstance(node.data, str):
            self.post_message(self.CategorySelected(node.data))
        else:
            self.post_message(self.CategorySelected(node.data.category))
