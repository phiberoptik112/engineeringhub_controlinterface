"""Command input and sub-menu screens for commands that need arguments."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from engineering_hub.journaler.file_browser import CommandEntry


def _move_button_focus(screen: ModalScreen, direction: int) -> None:
    """Move focus between Button widgets in a modal screen via arrow keys."""
    buttons = list(screen.query(".submenu-option"))
    if not buttons:
        return
    focused = screen.focused
    if focused in buttons:
        idx = buttons.index(focused)
        new_idx = idx + direction
        if 0 <= new_idx < len(buttons):
            buttons[new_idx].focus()
    elif direction > 0 and buttons:
        buttons[0].focus()


class CommandInputScreen(ModalScreen[str | None]):
    """Modal for entering arguments for a slash command."""

    DEFAULT_CSS = """
    CommandInputScreen {
        align: center middle;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, entry: CommandEntry) -> None:
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        with Vertical(id="command-input-container"):
            yield Static(self.entry.name, id="command-input-title")
            yield Static(self.entry.description, id="command-input-desc")
            if self.entry.args_hint:
                yield Static(
                    f"[dim]Arguments: {self.entry.args_hint}[/dim]",
                    id="command-input-hint",
                )
            yield Input(
                placeholder=self.entry.args_hint or "Enter arguments...",
                id="command-input-field",
            )

    def on_mount(self) -> None:
        self.query_one("#command-input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        args = event.value.strip()
        command = f"{self.entry.name} {args}" if args else self.entry.name
        self.dismiss(command)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SubMenuScreen(ModalScreen[str | None]):
    """Modal with selectable sub-options for a command.

    Used for commands like /clear (--hard, --summarize, soft),
    /zettel (propose, apply, status), etc.

    Supports Up/Down arrow keys to navigate between option buttons.
    """

    DEFAULT_CSS = """
    SubMenuScreen {
        align: center middle;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: list[tuple[str, str]]) -> None:
        """Initialize with a title and list of (value, label) tuples."""
        super().__init__()
        self._title = title
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="submenu-container"):
            yield Static(f"[bold]{self._title}[/bold]", id="submenu-title")
            with VerticalScroll():
                for value, label in self._options:
                    yield Button(label, id=f"opt-{value}", classes="submenu-option")

    def on_mount(self) -> None:
        buttons = list(self.query(".submenu-option"))
        if buttons:
            buttons[0].focus()

    def on_key(self, event) -> None:
        if event.key == "down":
            _move_button_focus(self, 1)
            event.stop()
        elif event.key == "up":
            _move_button_focus(self, -1)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        value = event.button.id
        if value and value.startswith("opt-"):
            self.dismiss(value[4:])

    def action_cancel(self) -> None:
        self.dismiss(None)


class AgentPickerScreen(ModalScreen[str | None]):
    """Modal for picking an agent persona and entering a task description.

    Supports Up/Down arrow keys to navigate between agent buttons.
    """

    DEFAULT_CSS = """
    AgentPickerScreen {
        align: center middle;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, skills: list[object]) -> None:
        super().__init__()
        self._skills = skills
        self._selected_skill: object | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="submenu-container"):
            yield Static("[bold]Select Agent Persona[/bold]", id="submenu-title")
            with VerticalScroll(id="skill-list"):
                for skill in self._skills:
                    name = getattr(skill, "display_name", str(skill))
                    agent_type = getattr(skill, "agent_type", "")
                    yield Button(
                        f"{name}",
                        id=f"skill-{agent_type}",
                        classes="submenu-option",
                    )
            yield Static("", id="agent-task-section")

    def on_mount(self) -> None:
        buttons = list(self.query(".submenu-option"))
        if buttons:
            buttons[0].focus()

    def on_key(self, event) -> None:
        if self._selected_skill:
            return
        if event.key == "down":
            _move_button_focus(self, 1)
            event.stop()
        elif event.key == "up":
            _move_button_focus(self, -1)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("skill-"):
            agent_type = btn_id[6:]
            self._selected_skill = agent_type
            task_section = self.query_one("#agent-task-section")
            task_section.remove()
            container = self.query_one("#submenu-container")
            container.mount(
                Static(f"[cyan]Agent: {agent_type}[/cyan]")
            )
            container.mount(
                Input(placeholder="Describe the task...", id="agent-task-input")
            )
            self.query_one("#agent-task-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "agent-task-input" and self._selected_skill:
            task = event.value.strip()
            if task:
                self.dismiss(f"/agent {self._selected_skill} {task}")
            else:
                self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ClearOptionsScreen(ModalScreen[str | None]):
    """Modal for selecting /clear strategy.

    Supports Up/Down arrow keys to navigate between option buttons.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="submenu-container"):
            yield Static("[bold]Clear Conversation[/bold]", id="submenu-title")
            yield Static(
                "[dim]Choose how to clear conversation history:[/dim]"
            )
            yield Button(
                "Soft Clear — remove history, keep context snapshot",
                id="opt-soft",
                classes="submenu-option",
            )
            yield Button(
                "Summarize — compress history into summary, then clear",
                id="opt-summarize",
                classes="submenu-option",
            )
            yield Button(
                "Hard Reset — full reset including scan state",
                id="opt-hard",
                classes="submenu-option",
            )

    def on_mount(self) -> None:
        buttons = list(self.query(".submenu-option"))
        if buttons:
            buttons[0].focus()

    def on_key(self, event) -> None:
        if event.key == "down":
            _move_button_focus(self, 1)
            event.stop()
        elif event.key == "up":
            _move_button_focus(self, -1)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "opt-soft":
            self.dismiss("/clear")
        elif btn_id == "opt-summarize":
            self.dismiss("/clear --summarize")
        elif btn_id == "opt-hard":
            self.dismiss("/clear --hard")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ExportOptionsScreen(ModalScreen[str | None]):
    """Modal for /export command options.

    Supports Up/Down arrow keys to navigate between option buttons.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="submenu-container"):
            yield Static("[bold]Export Conversation[/bold]", id="submenu-title")
            yield Static(
                "[dim]Choose export format and destination:[/dim]"
            )
            yield Button(
                "Default — export to org-roam conversation_exports/",
                id="opt-default",
                classes="submenu-option",
            )
            yield Button(
                "Summarize — generate summary + TODO extraction",
                id="opt-summarize",
                classes="submenu-option",
            )
            yield Button(
                "Raw Org — deterministic org format",
                id="opt-raw",
                classes="submenu-option",
            )
            yield Static("\n[dim]Or enter custom flags:[/dim]")
            yield Input(
                placeholder="--format raw --note --find-title ...",
                id="export-custom-input",
            )

    def on_mount(self) -> None:
        buttons = list(self.query(".submenu-option"))
        if buttons:
            buttons[0].focus()

    def on_key(self, event) -> None:
        if event.key == "down":
            _move_button_focus(self, 1)
            event.stop()
        elif event.key == "up":
            _move_button_focus(self, -1)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "opt-default":
            self.dismiss("/export")
        elif btn_id == "opt-summarize":
            self.dismiss("/export --summarize")
        elif btn_id == "opt-raw":
            self.dismiss("/export --format raw")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "export-custom-input":
            flags = event.value.strip()
            self.dismiss(f"/export {flags}" if flags else "/export")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ZettelOptionsScreen(ModalScreen[str | None]):
    """Modal for /zettel sub-actions.

    Supports Up/Down arrow keys to navigate between option buttons.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="submenu-container"):
            yield Static("[bold]Zettelkasten Management[/bold]", id="submenu-title")
            yield Button(
                "Propose — create reviewable atomic-note proposals",
                id="opt-propose",
                classes="submenu-option",
            )
            yield Button(
                "Status — show processed spans and proposal batches",
                id="opt-status",
                classes="submenu-option",
            )
            yield Static("\n[dim]Apply requires a proposal JSON path:[/dim]")
            yield Input(
                placeholder="Path to proposal JSON file...",
                id="zettel-apply-input",
            )

    def on_mount(self) -> None:
        buttons = list(self.query(".submenu-option"))
        if buttons:
            buttons[0].focus()

    def on_key(self, event) -> None:
        if event.key == "down":
            _move_button_focus(self, 1)
            event.stop()
        elif event.key == "up":
            _move_button_focus(self, -1)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "opt-propose":
            self.dismiss("/zettel propose")
        elif btn_id == "opt-status":
            self.dismiss("/zettel status")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "zettel-apply-input":
            path = event.value.strip()
            if path:
                self.dismiss(f"/zettel apply {path}")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelPickerScreen(ModalScreen[object | None]):
    """Modal for /model: profiles, cached mlx-community models, and manual entry."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, catalog: list[object] | None = None) -> None:
        super().__init__()
        self._catalog = list(catalog or [])
        self._filter = ""
        self._visible_entries: list[object] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="submenu-container"):
            yield Static("[bold]Switch MLX Model[/bold]", id="submenu-title")
            yield Button(
                "Show Current — display active model info",
                id="opt-show",
                classes="submenu-option",
            )
            yield Button(
                "Browse All — open full model browser",
                id="opt-browse",
                classes="submenu-option",
            )
            yield Input(
                placeholder="Filter models…",
                id="model-filter-input",
            )
            yield VerticalScroll(id="model-entry-list")
            yield Static("\n[dim]Or enter HF repo id / local path:[/dim]")
            yield Input(
                placeholder="mlx-community/… or ~/.cache/…/snapshots/…",
                id="model-path-input",
            )

    def _mount_catalog_buttons(self) -> None:
        from engineering_hub.journaler.model_catalog import format_catalog_entry_line

        container = self.query_one("#model-entry-list", VerticalScroll)
        container.remove_children()
        query = self._filter.strip().lower()
        visible = []
        for entry in self._catalog:
            label = entry.label
            load_value = entry.load_value
            profile = entry.profile_name or ""
            if query and (
                query not in label.lower()
                and query not in load_value.lower()
                and query not in profile.lower()
            ):
                continue
            visible.append(entry)
        if not visible:
            container.mount(Static("[dim]No matching models[/dim]"))
            return
        for idx, entry in enumerate(visible):
            line = format_catalog_entry_line(entry, width=56)
            container.mount(
                Button(line, id=f"entry-{idx}", classes="submenu-option model-entry-btn")
            )
        self._visible_entries = visible

    def on_mount(self) -> None:
        self._visible_entries = []
        self._mount_catalog_buttons()
        buttons = list(self.query(".submenu-option"))
        if buttons:
            buttons[0].focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "model-filter-input":
            self._filter = event.value
            self._mount_catalog_buttons()

    def on_key(self, event) -> None:
        if event.key == "down":
            _move_button_focus(self, 1)
            event.stop()
        elif event.key == "up":
            _move_button_focus(self, -1)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "opt-show":
            self.dismiss("/model")
        elif btn_id == "opt-browse":
            self.dismiss("/model_browse")
        elif btn_id.startswith("entry-"):
            try:
                idx = int(btn_id[6:])
                self.dismiss(self._visible_entries[idx])
            except (ValueError, IndexError):
                pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "model-path-input":
            value = event.value.strip()
            if not value:
                return
            if "/" in value or value.startswith("models--") or value.startswith("~"):
                self.dismiss(f"/model path {value}")
            else:
                self.dismiss(f"/model {value}")

    def action_cancel(self) -> None:
        self.dismiss(None)
