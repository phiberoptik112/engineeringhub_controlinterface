"""Quick Context panel for one-click loading of frequently accessed content."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static, TabbedContent, TabPane

if TYPE_CHECKING:
    from engineering_hub.journaler.engine import ConversationEngine
    from engineering_hub.journaler.tui.load_tracker import LoadTracker


class ContextFileItem(ListItem):
    """A single file entry in the context panel.

    Inherits from ListItem so it works with ListView's built-in
    arrow key navigation (Up/Down to move, Enter to select).
    """

    DEFAULT_CSS = """
    ContextFileItem {
        height: auto;
        padding: 0 1;
    }
    ContextFileItem:hover {
        background: $accent 15%;
    }
    ContextFileItem.--loaded {
        background: $success 10%;
    }
    """

    def __init__(self, file_path: Path, preview: str = "", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.file_path = file_path
        self.preview = preview

    def compose(self) -> ComposeResult:
        name = self.file_path.name
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", name)
        date_str = date_match.group(0) if date_match else ""

        label_text = f"[bold]{name}[/bold]"
        if date_str:
            label_text += f"  [dim]{date_str}[/dim]"
        yield Label(label_text)
        if self.preview:
            yield Static(f"[dim]{self.preview[:80]}[/dim]")


class QuickContextPanel(Vertical):
    """Tabbed panel for quick context loading from journals, briefings, projects, history.

    Each tab contains a ListView which provides built-in Up/Down arrow key
    navigation and Enter-to-select behavior. When the panel gains visibility,
    focus is sent to the active tab's ListView so arrow keys work immediately.
    """

    DEFAULT_CSS = """
    QuickContextPanel {
        height: 1fr;
        padding: 1;
    }
    QuickContextPanel ListView {
        height: 1fr;
    }
    """

    class FileLoadRequested(Message):
        """Posted after the user selects a file (Enter/click) and load is attempted."""

        def __init__(self, file_path: Path, ok: bool, message: str) -> None:
            super().__init__()
            self.file_path = file_path
            self.ok = ok
            self.message = message

    def __init__(
        self,
        engine: ConversationEngine | None = None,
        config: object | None = None,
        load_tracker: LoadTracker | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._engine = engine
        self._config = config
        self._load_tracker = load_tracker

    def compose(self) -> ComposeResult:
        with TabbedContent():
            with TabPane("Journals", id="tab-journals"):
                yield ListView(id="journal-list")
            with TabPane("Briefings", id="tab-briefings"):
                yield ListView(id="briefing-list")
            with TabPane("Projects", id="tab-projects"):
                yield ListView(id="project-list")
            with TabPane("History", id="tab-history"):
                yield ListView(id="history-list")

    def on_mount(self) -> None:
        self._populate_journals()
        self._populate_briefings()
        self._populate_projects()
        self._populate_history()
        self.call_after_refresh(self._initialize_list_indices)

    def _initialize_list_indices(self) -> None:
        """Set index=0 on all non-empty ListViews after items are mounted.

        ListView._on_mount sets the index from initial_index only if children
        exist at mount time.  Since we append items asynchronously after mount,
        the index stays None.  This deferred call fixes that.
        """
        for lv in self.query(ListView):
            if lv._nodes and lv.index is None:
                lv.index = 0

    def focus_active_list(self) -> None:
        """Focus the ListView in the currently active tab.

        Call this after making the panel visible so that arrow keys
        and Enter immediately work without requiring an extra click.
        """
        try:
            tabbed = self.query_one(TabbedContent)
            active_pane = tabbed.active_pane
            if active_pane is None:
                active_pane = tabbed.query(TabPane).first()
            if active_pane:
                lv = active_pane.query_one(ListView)
                if lv._nodes and lv.index is None:
                    lv.index = 0
                # Blur chat input so Enter selects a list item instead of submitting chat.
                try:
                    self.app.query_one("#input-bar").blur()
                except Exception:
                    pass
                lv.focus(scroll_visible=True)
        except Exception:
            pass

    def on_key(self, event) -> None:
        if event.key == "left":
            self.app.action_focus_sidebar()
            event.stop()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """When the user switches tabs, focus the new tab's ListView."""
        self.call_after_refresh(self.focus_active_list)

    def _get_journal_dir(self) -> Path | None:
        if self._config and hasattr(self._config, "journal_dir"):
            return Path(self._config.journal_dir)  # type: ignore[arg-type]
        return None

    def _get_state_dir(self) -> Path | None:
        if self._config and hasattr(self._config, "state_dir"):
            return Path(self._config.state_dir)  # type: ignore[arg-type]
        return None

    def _get_org_roam_dir(self) -> Path | None:
        if self._config and hasattr(self._config, "org_roam_dir"):
            return Path(self._config.org_roam_dir)  # type: ignore[arg-type]
        return None

    def _populate_journals(self) -> None:
        container = self.query_one("#journal-list", ListView)
        journal_dir = self._get_journal_dir()
        if not journal_dir or not journal_dir.is_dir():
            return

        files = sorted(
            journal_dir.glob("*.org"),
            key=lambda p: p.name,
            reverse=True,
        )[:14]

        if not files:
            return

        for f in files:
            preview = self._read_preview(f)
            item = ContextFileItem(f, preview=preview)
            if self._is_loaded(f):
                item.add_class("--loaded")
            container.append(item)

    def _populate_briefings(self) -> None:
        container = self.query_one("#briefing-list", ListView)
        state_dir = self._get_state_dir()
        if not state_dir:
            return

        briefing_dir = state_dir / "briefings"
        if not briefing_dir.is_dir():
            return

        files = sorted(
            briefing_dir.glob("*.md"),
            key=lambda p: p.name,
            reverse=True,
        )[:10]

        if not files:
            return

        for f in files:
            preview = self._read_preview(f)
            item = ContextFileItem(f, preview=preview)
            if self._is_loaded(f):
                item.add_class("--loaded")
            container.append(item)

    def _populate_projects(self) -> None:
        container = self.query_one("#project-list", ListView)
        org_roam_dir = self._get_org_roam_dir()
        journal_dir = self._get_journal_dir()

        if not org_roam_dir or not org_roam_dir.is_dir():
            return

        all_org = []
        for f in org_roam_dir.rglob("*.org"):
            if journal_dir and f.is_relative_to(journal_dir):
                continue
            all_org.append(f)

        files = sorted(all_org, key=lambda p: p.stat().st_mtime, reverse=True)[:15]

        if not files:
            return

        for f in files:
            preview = self._read_preview(f)
            item = ContextFileItem(f, preview=preview)
            if self._is_loaded(f):
                item.add_class("--loaded")
            container.append(item)

    def _populate_history(self) -> None:
        container = self.query_one("#history-list", ListView)
        state_dir = self._get_state_dir()
        if not state_dir:
            return

        summaries_dir = state_dir / "daily_summaries"
        if summaries_dir.is_dir():
            files = sorted(
                summaries_dir.glob("*.md"),
                key=lambda p: p.name,
                reverse=True,
            )[:10]
        else:
            files = []

        conversation_log = getattr(self._engine, "_log_file", state_dir / "conversation.jsonl")
        if conversation_log.exists():
            active = getattr(self._engine, "active_conversation", None)
            preview = "Full conversation log (current session)"
            if active is not None:
                preview = f"Active: {active.title} ({active.id})"
            item = ContextFileItem(
                conversation_log,
                preview=preview,
            )
            if self._is_loaded(conversation_log):
                item.add_class("--loaded")
            container.append(item)

        if not files:
            return

        for f in files:
            preview = self._read_preview(f)
            item = ContextFileItem(f, preview=preview)
            if self._is_loaded(f):
                item.add_class("--loaded")
            container.append(item)

    def _read_preview(self, path: Path) -> str:
        """Read first non-empty line as preview."""
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("---"):
                        return stripped
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    def _is_loaded(self, path: Path) -> bool:
        """Check if a file is currently loaded in the engine."""
        if not self._engine:
            return False
        loaded = self._engine.list_loaded_files()
        return any(path.name == label for label, _ in loaded)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle Enter key or click on a ListView item — loads the file."""
        item = event.item
        if isinstance(item, ContextFileItem):
            self._load_file(item.file_path)

    def _load_file(self, path: Path) -> None:
        """Load a file into engine context and update tracker."""
        if not self._engine:
            self.post_message(
                self.FileLoadRequested(path, False, "Journaler engine is not available.")
            )
            return

        try:
            ok, msg = self._engine.load_file(path)
        except Exception as exc:
            ok, msg = False, f"Could not load {path.name}: {exc}"

        if ok and self._load_tracker:
            category = self._categorize_path(path)
            self._load_tracker.record_load(path, category)

        if ok:
            self._mark_item_loaded(path)

        self.post_message(self.FileLoadRequested(path, ok, msg))

    def _mark_item_loaded(self, path: Path) -> None:
        """Add the loaded indicator to the matching list item."""
        for item in self.query(ContextFileItem):
            if item.file_path == path:
                item.add_class("--loaded")
                break

    def refresh_loaded_indicators(self) -> None:
        """Sync --loaded styling with engine.loaded files."""
        for item in self.query(ContextFileItem):
            if self._is_loaded(item.file_path):
                item.add_class("--loaded")
            else:
                item.remove_class("--loaded")

    def _categorize_path(self, path: Path) -> str:
        """Determine which category a file belongs to."""
        journal_dir = self._get_journal_dir()
        state_dir = self._get_state_dir()

        if journal_dir and path.is_relative_to(journal_dir):
            return "journal"
        if state_dir:
            if path.is_relative_to(state_dir / "briefings"):
                return "briefing"
            if path.is_relative_to(state_dir / "daily_summaries"):
                return "history"
        return "project"
