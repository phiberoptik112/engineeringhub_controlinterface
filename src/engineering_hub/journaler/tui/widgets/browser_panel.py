"""Native Textual browser panel replacing the curses overlays for browse commands.

Supports four modes driven by the same widget:
  "load"    — multi-select file browser for /load_browse
  "edit"    — single-select file browser for /edit_browse
  "skills"  — agent-skill picker for /agent_browse
  "capture" — capture template picker for /capture_browse
  "models"  — MLX model picker for /model_browse
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, Label, ListItem, ListView, Static

if TYPE_CHECKING:
    pass

BrowserMode = Literal["load", "edit", "skills", "capture", "models"]

SUPPORTED_EXTENSIONS = frozenset({
    ".md", ".txt", ".org", ".py", ".yaml", ".yml",
    ".json", ".tex", ".csv", ".toml", ".rst", ".docx", ".pdf",
})

_EDIT_EXTENSIONS = frozenset({".org"})


# ---------------------------------------------------------------------------
# Internal helpers (stand-alone to avoid importing private file_browser.py names)
# ---------------------------------------------------------------------------

def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1_048_576:
        return f"{size / 1024:.1f}K"
    return f"{size / 1_048_576:.1f}M"


def _fmt_date(ts: float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _birthtime(stat: os.stat_result) -> float:
    return float(getattr(stat, "st_birthtime", stat.st_ctime))


class _Entry:
    """Minimal file-system entry for the browser."""

    __slots__ = ("name", "path", "is_dir", "size", "created_at")

    def __init__(
        self,
        name: str,
        path: Path,
        is_dir: bool,
        size: int = 0,
        created_at: float | None = None,
    ) -> None:
        self.name = name
        self.path = path
        self.is_dir = is_dir
        self.size = size
        self.created_at = created_at


def _scan_dir(path: Path, root: Path, exts: frozenset[str]) -> list[_Entry]:
    entries: list[_Entry] = []
    if path.resolve() != root.resolve():
        try:
            entries.append(_Entry("../", path.parent, is_dir=True,
                                  created_at=_birthtime(path.parent.stat())))
        except OSError:
            entries.append(_Entry("../", path.parent, is_dir=True))

    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return entries

    for child in children:
        try:
            if child.is_dir():
                entries.append(_Entry(
                    child.name + "/", child, is_dir=True,
                    created_at=_birthtime(child.stat()),
                ))
            elif child.is_file():
                if exts and child.suffix.lower() not in exts:
                    continue
                st = child.stat()
                entries.append(_Entry(
                    child.name, child, is_dir=False,
                    size=st.st_size, created_at=_birthtime(st),
                ))
        except OSError:
            continue
    return entries


def _search_files(root: Path, query: str, exts: frozenset[str], limit: int = 200) -> list[_Entry]:
    terms = [t for t in query.lower().split() if t]
    if not terms or not root.is_dir():
        return []

    results: list[_Entry] = []
    stack = [root]
    while stack and len(results) < limit:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                children = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        dirs: list[Path] = []
        for child in children:
            cp = Path(child.path)
            try:
                if child.is_dir(follow_symlinks=False):
                    dirs.append(cp)
                elif child.is_file(follow_symlinks=False):
                    if exts and cp.suffix.lower() not in exts:
                        continue
                    try:
                        label = str(cp.relative_to(root))
                    except ValueError:
                        label = str(cp)
                    haystack = f"{cp.name} {label}".lower()
                    if all(t in haystack for t in terms):
                        try:
                            st = cp.stat()
                            results.append(_Entry(
                                label, cp, is_dir=False,
                                size=st.st_size, created_at=_birthtime(st),
                            ))
                        except OSError:
                            results.append(_Entry(label, cp, is_dir=False))
                        if len(results) >= limit:
                            break
            except OSError:
                continue
        stack.extend(reversed(dirs))
    return results


# ---------------------------------------------------------------------------
# ListItem subclass
# ---------------------------------------------------------------------------

class BrowserItem(ListItem):
    """A single row in the browser list."""

    DEFAULT_CSS = """
    BrowserItem {
        height: 1;
        padding: 0 1;
    }
    BrowserItem:hover {
        background: $accent 12%;
    }
    BrowserItem.--selected {
        background: $success 15%;
    }
    BrowserItem.--dir {
        color: $accent;
        text-style: bold;
    }
    """

    def __init__(self, display_text: str, entry: Any, *, is_dir: bool = False) -> None:
        super().__init__()
        self._display_text = display_text
        self.entry = entry
        self.is_dir = is_dir
        if is_dir:
            self.add_class("--dir")

    def compose(self) -> ComposeResult:
        yield Static(self._display_text)

    def mark_selected(self, flag: bool) -> None:
        if flag:
            self.add_class("--selected")
        else:
            self.remove_class("--selected")

    def update_text(self, text: str) -> None:
        self._display_text = text
        try:
            self.query_one(Static).update(text)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class BrowserPanel(Vertical):
    """Right-hand interactive browser for /load_browse, /edit_browse,
    /agent_browse, and /capture_browse.

    Call ``configure(mode, root, items)`` before or after mounting to set
    the browser up for a specific operation.
    """

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class Confirmed(Message):
        """Posted when the user confirms a selection."""

        def __init__(
            self,
            mode: BrowserMode,
            files: list[Path] | None = None,
            skill: Any = None,
            template: Any = None,
            model_entry: Any = None,
        ) -> None:
            super().__init__()
            self.mode = mode
            self.files: list[Path] = files or []
            self.skill = skill
            self.template = template
            self.model_entry = model_entry

    class Cancelled(Message):
        """Posted when the user presses Escape or explicitly cancels."""

    # ------------------------------------------------------------------
    # Reactive state
    # ------------------------------------------------------------------

    filter_text: reactive[str] = reactive("", layout=False)

    DEFAULT_CSS = """
    BrowserPanel {
        height: 1fr;
        background: $background;
    }
    BrowserPanel #bp-header {
        height: 2;
        background: $primary-background;
        color: $accent;
        text-style: bold;
        padding: 0 1;
        border-bottom: solid $primary-background-lighten-2;
    }
    BrowserPanel #bp-filter {
        height: 3;
        border: solid $primary-background-lighten-2;
        margin: 0;
    }
    BrowserPanel #bp-filter:focus {
        border: solid $accent;
    }
    BrowserPanel #bp-list {
        height: 1fr;
        background: $background;
        scrollbar-gutter: stable;
    }
    BrowserPanel #bp-footer {
        height: 2;
        background: $primary-background;
        color: $text-muted;
        padding: 0 1;
        border-top: solid $primary-background-lighten-2;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._mode: BrowserMode = "load"
        self._root: Path | None = None
        self._items: list[Any] = []
        self._current_dir: Path = Path.home()
        self._entries: list[_Entry] = []
        self._selected: set[Path] = set()
        self._is_search: bool = False

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="bp-header")
        yield Input(placeholder="Type to filter / search  (Tab to jump to list)", id="bp-filter")
        yield ListView(id="bp-list")
        yield Static("", id="bp-footer")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#bp-list", ListView).focus()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def configure(
        self,
        mode: BrowserMode,
        root: Path | None = None,
        items: list[Any] | None = None,
    ) -> None:
        """(Re-)configure the browser for a new operation."""
        self._mode = mode
        self._root = root
        self._items = items or []
        self._current_dir = root or Path.home()
        self._selected.clear()
        self._is_search = False

        try:
            self.query_one("#bp-filter", Input).value = ""
        except Exception:
            pass

        self._refresh()

    # ------------------------------------------------------------------
    # Rebuild logic
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self._rebuild_list()
        self._update_header()
        self._update_footer()

    def _rebuild_list(self) -> None:
        lv = self.query_one("#bp-list", ListView)
        lv.clear()

        if self._mode in ("load", "edit"):
            self._build_file_list(lv)
        elif self._mode == "skills":
            self._build_skills_list(lv)
        elif self._mode == "capture":
            self._build_capture_list(lv)
        elif self._mode == "models":
            self._build_models_list(lv)

    def _build_file_list(self, lv: ListView) -> None:
        root = self._root
        if not root or not root.is_dir():
            lv.append(BrowserItem(" (no directory configured)", None))
            return

        exts = _EDIT_EXTENSIONS if self._mode == "edit" else SUPPORTED_EXTENSIONS
        query = self.query_one("#bp-filter", Input).value.strip()

        if query:
            self._entries = _search_files(root, query, exts)
            self._is_search = True
        else:
            self._entries = _scan_dir(self._current_dir, root, exts)
            self._is_search = False

        if not self._entries:
            lv.append(BrowserItem(" (no matching files)", None))
            return

        for entry in self._entries:
            is_sel = (not entry.is_dir) and (entry.path.resolve() in self._selected)
            sel_mark = "✓ " if is_sel else "  "
            icon = "📁 " if entry.is_dir else "📄 "
            size_str = _fmt_size(entry.size) if not entry.is_dir else "    -"
            date_str = _fmt_date(entry.created_at)
            name_part = entry.name
            # Truncate name for display
            max_name = 44
            if len(name_part) > max_name:
                name_part = name_part[: max_name - 1] + "…"
            text = f"{sel_mark}{icon}{name_part:<{max_name}}  {size_str:>8}  {date_str}"
            item = BrowserItem(text, entry, is_dir=entry.is_dir)
            if is_sel:
                item.add_class("--selected")
            lv.append(item)

    def _build_skills_list(self, lv: ListView) -> None:
        query = self.query_one("#bp-filter", Input).value.strip().lower()
        for skill in self._items:
            name = getattr(skill, "display_name", str(skill))
            desc = getattr(skill, "description", "")
            desc_line = desc.strip().splitlines()[0][:60] if desc.strip() else ""
            if query and query not in name.lower() and query not in desc_line.lower():
                continue
            label = f"  🤖 {name}"
            if desc_line:
                label += f"  —  {desc_line}"
            lv.append(BrowserItem(label, skill))

    def _build_models_list(self, lv: ListView) -> None:
        from engineering_hub.journaler.model_catalog import format_catalog_entry_line

        query = self.query_one("#bp-filter", Input).value.strip().lower()
        for entry in self._items:
            label = entry.label
            load_value = entry.load_value
            profile = entry.profile_name or ""
            if query and (
                query not in label.lower()
                and query not in load_value.lower()
                and query not in profile.lower()
            ):
                continue
            line = format_catalog_entry_line(entry, width=64)
            lv.append(BrowserItem(f"  🧠 {line}", entry))

    def _build_capture_list(self, lv: ListView) -> None:
        query = self.query_one("#bp-filter", Input).value.strip().lower()
        for tpl in self._items:
            name = getattr(tpl, "display_name", str(tpl))
            key = getattr(tpl, "key", "")
            desc = getattr(tpl, "description", "")
            desc_line = desc.strip().splitlines()[0][:50] if desc else ""
            if query and query not in name.lower() and query not in desc_line.lower():
                continue
            key_badge = f" [{key}]" if key else ""
            label = f"  📋 {name}{key_badge}"
            if desc_line:
                label += f"  —  {desc_line}"
            lv.append(BrowserItem(label, tpl))

    # ------------------------------------------------------------------
    # Header / footer
    # ------------------------------------------------------------------

    def _update_header(self) -> None:
        header = self.query_one("#bp-header", Static)
        if self._mode == "load":
            sel = len(self._selected)
            sel_text = f"  [{sel} selected]" if sel else ""
            mode_text = "Search results" if self._is_search else str(self._current_dir)
            header.update(f" 📂 Load Browse: {mode_text}{sel_text}")
        elif self._mode == "edit":
            mode_text = "Search results" if self._is_search else str(self._current_dir)
            header.update(f" ✏️  Edit Browse: {mode_text}")
        elif self._mode == "skills":
            header.update(" 🤖 Agent Browse: Select an agent persona")
        elif self._mode == "capture":
            header.update(" 📋 Capture Browse: Select a capture template")
        elif self._mode == "models":
            header.update(" 🧠 Model Browse: Select an MLX model")

    def _update_footer(self) -> None:
        footer = self.query_one("#bp-footer", Static)
        if self._mode == "load":
            hints = (
                " ↑↓ navigate  Space toggle-select  "
                "Enter open-dir / confirm  Tab filter  Esc cancel"
            )
        elif self._mode == "edit":
            hints = " ↑↓ navigate  Enter select / open dir  Tab filter  Esc cancel"
        else:
            hints = " ↑↓ navigate  Enter select  Tab filter  Esc cancel"
        footer.update(hints)

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "bp-filter":
            self._rebuild_list()
            self._update_header()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Tab or Enter in filter box — jump focus to the list."""
        if event.input.id == "bp-filter":
            self.query_one("#bp-list", ListView).focus()
            event.stop()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.post_message(self.Cancelled())
            event.stop()
            return

        if event.key == "tab":
            # Toggle focus between filter and list
            filt = self.query_one("#bp-filter", Input)
            lv = self.query_one("#bp-list", ListView)
            if filt.has_focus:
                lv.focus()
            else:
                filt.focus()
            event.stop()
            return

        # Space = toggle selection (load mode only, when list is focused)
        if event.key == "space" and self._mode == "load":
            lv = self.query_one("#bp-list", ListView)
            if not lv.has_focus:
                return
            highlighted = lv.highlighted_child
            if not isinstance(highlighted, BrowserItem):
                return
            entry = highlighted.entry
            if entry is None or not isinstance(entry, _Entry) or entry.is_dir:
                return
            resolved = entry.path.resolve()
            if resolved in self._selected:
                self._selected.discard(resolved)
                highlighted.mark_selected(False)
            else:
                self._selected.add(resolved)
                highlighted.mark_selected(True)
            self._update_header()
            event.stop()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, BrowserItem) or item.entry is None:
            return

        if self._mode in ("load", "edit"):
            entry: _Entry = item.entry
            if entry.is_dir:
                root = self._root
                if root is None:
                    return
                target = entry.path.resolve()
                # Clamp to root
                try:
                    target.relative_to(root)
                except ValueError:
                    target = root
                self._current_dir = target
                # Clear search when navigating directories
                self.query_one("#bp-filter", Input).value = ""
                self._refresh()
            else:
                # File confirm
                if self._mode == "edit":
                    self.post_message(self.Confirmed(self._mode, files=[entry.path.resolve()]))
                else:
                    # load mode: confirm current selection (or just this file)
                    files = sorted(self._selected) if self._selected else [entry.path.resolve()]
                    self.post_message(self.Confirmed(self._mode, files=files))

        elif self._mode == "skills":
            self.post_message(self.Confirmed(self._mode, skill=item.entry))

        elif self._mode == "capture":
            self.post_message(self.Confirmed(self._mode, template=item.entry))

        elif self._mode == "models":
            self.post_message(self.Confirmed(self._mode, model_entry=item.entry))
