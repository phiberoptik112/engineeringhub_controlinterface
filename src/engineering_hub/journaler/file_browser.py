"""Interactive curses-based browsers for `/load_browse`, `/edit_browse`, and `/agent_browse`.

Launches fullscreen TUIs that let the user navigate the org-roam
directory tree or agent skills with arrow keys, select items, and
confirm with Enter.
"""

from __future__ import annotations

import curses
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engineering_hub.journaler.delegator import SkillDef

_FAST_SCROLL_LINES = 5


@dataclass
class BrowseEntry:
    """A single row in the file browser listing."""

    name: str
    path: Path
    is_dir: bool
    size: int = 0
    selected: bool = False


@dataclass
class CommandEntry:
    """A single slash command shown in the command palette."""

    name: str
    args_hint: str
    description: str
    category: str


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def browse_org_roam(
    root: Path,
    extensions: frozenset[str],
) -> list[Path]:
    """Open an interactive file browser rooted at *root*.

    Returns a list of selected file paths (empty on cancel).
    Uses ``curses.wrapper`` to handle terminal setup/teardown.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        return []

    try:
        return curses.wrapper(_browse_inner, root, extensions, False)
    except Exception:
        return []


def browse_org_file(root: Path) -> Path | None:
    """Browse org-roam for a single ``.org`` file (for ``/edit_browse``).

    Returns the selected path, or ``None`` on cancel.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        return None

    try:
        result = curses.wrapper(
            _browse_inner, root, frozenset({".org"}), True,
        )
        return result[0] if result else None
    except Exception:
        return None


def browse_skills(skills: list[SkillDef]) -> SkillDef | None:
    """Open an interactive skill picker.

    Returns the selected ``SkillDef``, or ``None`` on cancel.
    """
    if not skills:
        return None
    try:
        return curses.wrapper(_browse_skills_inner, skills)
    except Exception:
        return None


def browse_commands(commands: list[CommandEntry]) -> str | None:
    """Open the command palette overlay.

    Returns the selected command text (e.g. ``/load ``), or ``None`` on cancel.
    The returned string is intended to be pre-filled into the readline input
    buffer so the user can edit and submit it.
    """
    if not commands:
        return None
    try:
        return curses.wrapper(_browse_commands_inner, commands)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}K"
    return f"{size / (1024 * 1024):.1f}M"


def _display_path(path: Path, root: Path) -> str:
    """Shortened display path relative to root, using ~ prefix."""
    try:
        rel = path.relative_to(root)
        return f"~/{rel}" if str(rel) != "." else "~"
    except ValueError:
        return str(path)


def _scan_directory(
    path: Path,
    root: Path,
    extensions: frozenset[str],
) -> list[BrowseEntry]:
    """List directory contents: directories first (sorted), then filtered files (sorted)."""
    entries: list[BrowseEntry] = []

    if path.resolve() != root.resolve():
        entries.append(BrowseEntry(name="../", path=path.parent, is_dir=True))

    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        return entries

    dirs = []
    files = []
    for child in children:
        if child.name.startswith("."):
            continue
        if child.is_dir():
            dirs.append(
                BrowseEntry(name=child.name + "/", path=child, is_dir=True)
            )
        elif child.is_file():
            if not extensions or child.suffix.lower() in extensions:
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                files.append(
                    BrowseEntry(name=child.name, path=child, is_dir=False, size=size)
                )

    entries.extend(dirs)
    entries.extend(files)
    return entries


# -- Curses color pair IDs ---------------------------------------------------
_CP_DIR = 1
_CP_FILE = 2
_CP_SELECTED = 3
_CP_HEADER = 4
_CP_FOOTER = 5
_CP_CURSOR = 6
_CP_DESC = 7


def _init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_CP_DIR, curses.COLOR_BLUE, -1)
    curses.init_pair(_CP_FILE, -1, -1)
    curses.init_pair(_CP_SELECTED, curses.COLOR_GREEN, -1)
    curses.init_pair(_CP_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(_CP_FOOTER, curses.COLOR_YELLOW, -1)
    curses.init_pair(_CP_CURSOR, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(_CP_DESC, curses.COLOR_WHITE, -1)


# ---------------------------------------------------------------------------
# File browser (used by /load_browse and /edit_browse)
# ---------------------------------------------------------------------------

def _browse_inner(
    stdscr: curses.window,
    root: Path,
    extensions: frozenset[str],
    single_select: bool,
) -> list[Path]:
    _init_colors()
    curses.curs_set(0)
    stdscr.keypad(True)

    current_dir = root
    cursor = 0
    scroll_offset = 0
    entries = _scan_directory(current_dir, root, extensions)
    selected: set[Path] = set()

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        header_lines = 2
        footer_lines = 3
        list_height = max(1, max_y - header_lines - footer_lines)

        # -- Header -----------------------------------------------------------
        header_text = f" Browse: {_display_path(current_dir, root)}"
        stdscr.attron(curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        stdscr.addnstr(0, 0, "─" * max_x, max_x)
        stdscr.addnstr(0, 0, header_text, max_x - 1)
        stdscr.attroff(curses.color_pair(_CP_HEADER) | curses.A_BOLD)

        if not single_select and selected:
            sel_text = f"  [{len(selected)} selected]"
            col = min(len(header_text) + 1, max_x - len(sel_text) - 1)
            if col > 0:
                stdscr.attron(curses.color_pair(_CP_SELECTED) | curses.A_BOLD)
                stdscr.addnstr(0, col, sel_text, max_x - col - 1)
                stdscr.attroff(curses.color_pair(_CP_SELECTED) | curses.A_BOLD)

        stdscr.addnstr(1, 0, "─" * max_x, max_x)

        # -- Clamp cursor and scroll -----------------------------------------
        if not entries:
            stdscr.attron(curses.A_DIM)
            stdscr.addnstr(header_lines, 2, "(empty directory)", max_x - 3)
            stdscr.attroff(curses.A_DIM)
        else:
            cursor = max(0, min(cursor, len(entries) - 1))
            if cursor < scroll_offset:
                scroll_offset = cursor
            if cursor >= scroll_offset + list_height:
                scroll_offset = cursor - list_height + 1

            # -- Draw entries -------------------------------------------------
            for i in range(list_height):
                idx = scroll_offset + i
                if idx >= len(entries):
                    break
                entry = entries[idx]
                row = header_lines + i
                if row >= max_y - footer_lines:
                    break

                is_cursor = idx == cursor
                is_sel = entry.path.resolve() in selected

                marker = ">" if is_cursor else " "
                sel_mark = " "
                if not single_select:
                    sel_mark = "*" if is_sel else " "
                size_str = ""
                if not entry.is_dir:
                    size_str = f"  [{_format_size(entry.size)}]"

                name_budget = max_x - 6 - len(size_str)
                display_name = entry.name
                if len(display_name) > name_budget > 3:
                    display_name = display_name[: name_budget - 1] + "…"

                line = f" {marker} {sel_mark} {display_name}"
                pad = max(0, max_x - len(line) - len(size_str) - 1)
                line += " " * pad + size_str

                if is_cursor:
                    attr = curses.color_pair(_CP_CURSOR) | curses.A_BOLD
                elif is_sel:
                    attr = curses.color_pair(_CP_SELECTED) | curses.A_BOLD
                elif entry.is_dir:
                    attr = curses.color_pair(_CP_DIR) | curses.A_BOLD
                else:
                    attr = curses.color_pair(_CP_FILE)

                try:
                    stdscr.addnstr(row, 0, line.ljust(max_x), max_x - 1, attr)
                except curses.error:
                    pass

            # Scroll indicators
            if scroll_offset > 0:
                try:
                    stdscr.addnstr(header_lines, max_x - 2, "▲", 1, curses.A_DIM)
                except curses.error:
                    pass
            if scroll_offset + list_height < len(entries):
                try:
                    stdscr.addnstr(
                        header_lines + list_height - 1, max_x - 2, "▼", 1, curses.A_DIM
                    )
                except curses.error:
                    pass

        # -- Footer -----------------------------------------------------------
        footer_y = max_y - footer_lines
        if footer_y > header_lines:
            try:
                stdscr.addnstr(footer_y, 0, "─" * max_x, max_x)
            except curses.error:
                pass
            stdscr.attron(curses.color_pair(_CP_FOOTER))
            if single_select:
                controls = " ↑↓ navigate  Shift+↑↓ fast  Enter select  ← back  Esc cancel"
            else:
                controls = (
                    " ↑↓ navigate  Shift+↑↓ fast  Enter open/load"
                    "  Space select  ← back  Esc cancel"
                )
            try:
                stdscr.addnstr(footer_y + 1, 0, controls, max_x - 1)
            except curses.error:
                pass
            if not single_select and selected:
                confirm_msg = f" Enter with selection → load {len(selected)} file(s)"
                try:
                    stdscr.addnstr(footer_y + 2, 0, confirm_msg, max_x - 1)
                except curses.error:
                    pass
            stdscr.attroff(curses.color_pair(_CP_FOOTER))

        stdscr.refresh()

        # -- Input handling ---------------------------------------------------
        key = stdscr.getch()

        if key in (27, ord("q")):  # Esc or q → cancel
            return []

        if key == curses.KEY_UP or key == ord("k"):
            if cursor > 0:
                cursor -= 1

        elif key == curses.KEY_DOWN or key == ord("j"):
            if entries and cursor < len(entries) - 1:
                cursor += 1

        elif key == curses.KEY_SR:  # Shift+Up
            cursor = max(0, cursor - _FAST_SCROLL_LINES)

        elif key == curses.KEY_SF:  # Shift+Down
            if entries:
                cursor = min(len(entries) - 1, cursor + _FAST_SCROLL_LINES)

        elif key == curses.KEY_LEFT or key == curses.KEY_BACKSPACE or key == 127:
            if current_dir.resolve() != root.resolve():
                current_dir = current_dir.parent
                entries = _scan_directory(current_dir, root, extensions)
                cursor = 0
                scroll_offset = 0

        elif key == curses.KEY_RIGHT:
            if entries and entries[cursor].is_dir:
                target = entries[cursor].path.resolve()
                if target.is_relative_to(root) or target == root:
                    current_dir = target
                elif entries[cursor].name == "../":
                    current_dir = entries[cursor].path.resolve()
                    if not current_dir.is_relative_to(root):
                        current_dir = root
                entries = _scan_directory(current_dir, root, extensions)
                cursor = 0
                scroll_offset = 0

        elif key == ord(" ") and not single_select:
            if entries and not entries[cursor].is_dir:
                resolved = entries[cursor].path.resolve()
                if resolved in selected:
                    selected.discard(resolved)
                    entries[cursor].selected = False
                else:
                    selected.add(resolved)
                    entries[cursor].selected = True
                if cursor < len(entries) - 1:
                    cursor += 1

        elif key in (curses.KEY_ENTER, 10, 13):
            if not entries:
                continue
            entry = entries[cursor]
            if entry.is_dir:
                target = entry.path.resolve()
                if not target.is_relative_to(root):
                    target = root
                current_dir = target
                entries = _scan_directory(current_dir, root, extensions)
                cursor = 0
                scroll_offset = 0
            else:
                if single_select:
                    return [entry.path.resolve()]
                if selected:
                    return sorted(selected)
                return [entry.path.resolve()]

        elif key == curses.KEY_PPAGE:  # Page Up
            cursor = max(0, cursor - list_height)

        elif key == curses.KEY_NPAGE:  # Page Down
            if entries:
                cursor = min(len(entries) - 1, cursor + list_height)

        elif key == curses.KEY_HOME:
            cursor = 0

        elif key == curses.KEY_END:
            if entries:
                cursor = len(entries) - 1

    return []


# ---------------------------------------------------------------------------
# Skill picker (used by /agent_browse)
# ---------------------------------------------------------------------------

def _browse_skills_inner(
    stdscr: curses.window,
    skills: list[SkillDef],
) -> SkillDef | None:
    _init_colors()
    curses.curs_set(0)
    stdscr.keypad(True)

    cursor = 0
    scroll_offset = 0

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        header_lines = 2
        footer_lines = 2
        list_height = max(1, max_y - header_lines - footer_lines)

        # -- Header -----------------------------------------------------------
        header_text = " Browse: Agent Skills"
        stdscr.attron(curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        stdscr.addnstr(0, 0, "─" * max_x, max_x)
        stdscr.addnstr(0, 0, header_text, max_x - 1)
        stdscr.attroff(curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        stdscr.addnstr(1, 0, "─" * max_x, max_x)

        # -- Build display blocks for each skill -----------------------------
        # Each skill occupies multiple lines; we track which "visual row"
        # maps to which skill index.
        blocks: list[tuple[int, list[str]]] = []  # (skill_idx, lines)
        for si, skill in enumerate(skills):
            lines: list[str] = []
            lines.append(f"  {skill.display_name}")
            desc = skill.description.strip()
            desc_first = desc.splitlines()[0] if desc else ""
            if desc_first:
                wrapped = textwrap.wrap(desc_first, width=max(20, max_x - 8))
                for wl in wrapped:
                    lines.append(f"      {wl}")
            if skill.invocation_examples:
                lines.append(f"      Use: {skill.invocation_examples[0]}")
            lines.append("")  # blank separator
            blocks.append((si, lines))

        # Flatten into visual rows: (skill_idx, line_text, is_name_line)
        visual_rows: list[tuple[int, str, bool]] = []
        for si, block_lines in blocks:
            for li, text in enumerate(block_lines):
                visual_rows.append((si, text, li == 0))

        # Determine the first visual row for each skill (for scroll targeting)
        skill_start_row: dict[int, int] = {}
        for vr_idx, (si, _, is_name) in enumerate(visual_rows):
            if is_name and si not in skill_start_row:
                skill_start_row[si] = vr_idx

        # Clamp cursor
        cursor = max(0, min(cursor, len(skills) - 1))
        target_vr = skill_start_row.get(cursor, 0)
        if target_vr < scroll_offset:
            scroll_offset = target_vr
        if target_vr >= scroll_offset + list_height:
            scroll_offset = target_vr - list_height + 1
        scroll_offset = max(0, scroll_offset)

        # -- Draw visible rows -----------------------------------------------
        for i in range(list_height):
            vr_idx = scroll_offset + i
            if vr_idx >= len(visual_rows):
                break
            si, text, is_name = visual_rows[vr_idx]
            row = header_lines + i
            if row >= max_y - footer_lines:
                break

            is_active = si == cursor

            if is_name and is_active:
                marker_line = f" > {text.lstrip()}"
            elif is_name:
                marker_line = f"   {text.lstrip()}"
            else:
                marker_line = text

            if is_active:
                if is_name:
                    attr = curses.color_pair(_CP_CURSOR) | curses.A_BOLD
                else:
                    attr = curses.color_pair(_CP_CURSOR)
            else:
                if is_name:
                    attr = curses.color_pair(_CP_FILE) | curses.A_BOLD
                else:
                    attr = curses.A_DIM

            try:
                padded = marker_line.ljust(max_x)
                stdscr.addnstr(row, 0, padded, max_x - 1, attr)
            except curses.error:
                pass

        # Scroll indicators
        if scroll_offset > 0:
            try:
                stdscr.addnstr(header_lines, max_x - 2, "▲", 1, curses.A_DIM)
            except curses.error:
                pass
        if scroll_offset + list_height < len(visual_rows):
            try:
                stdscr.addnstr(
                    header_lines + list_height - 1, max_x - 2, "▼", 1, curses.A_DIM
                )
            except curses.error:
                pass

        # -- Footer -----------------------------------------------------------
        footer_y = max_y - footer_lines
        if footer_y > header_lines:
            try:
                stdscr.addnstr(footer_y, 0, "─" * max_x, max_x)
            except curses.error:
                pass
            stdscr.attron(curses.color_pair(_CP_FOOTER))
            controls = " ↑↓ navigate  Shift+↑↓ fast  Enter select  Esc cancel"
            try:
                stdscr.addnstr(footer_y + 1, 0, controls, max_x - 1)
            except curses.error:
                pass
            stdscr.attroff(curses.color_pair(_CP_FOOTER))

        stdscr.refresh()

        # -- Input handling ---------------------------------------------------
        key = stdscr.getch()

        if key in (27, ord("q")):
            return None

        if key == curses.KEY_UP or key == ord("k"):
            if cursor > 0:
                cursor -= 1

        elif key == curses.KEY_DOWN or key == ord("j"):
            if cursor < len(skills) - 1:
                cursor += 1

        elif key == curses.KEY_SR:  # Shift+Up
            cursor = max(0, cursor - _FAST_SCROLL_LINES)

        elif key == curses.KEY_SF:  # Shift+Down
            cursor = min(len(skills) - 1, cursor + _FAST_SCROLL_LINES)

        elif key in (curses.KEY_ENTER, 10, 13):
            return skills[cursor]

        elif key == curses.KEY_HOME:
            cursor = 0

        elif key == curses.KEY_END:
            cursor = len(skills) - 1


# ---------------------------------------------------------------------------
# Command palette (used by Ctrl+P / browse_commands)
# ---------------------------------------------------------------------------

_CP_CATEGORY = 8


def _fuzzy_match(query: str, text: str) -> bool:
    """Return True if all chars of *query* appear in *text* as a subsequence."""
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


def _browse_commands_inner(
    stdscr: curses.window,
    commands: list[CommandEntry],
) -> str | None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_CP_DIR, curses.COLOR_BLUE, -1)
    curses.init_pair(_CP_FILE, -1, -1)
    curses.init_pair(_CP_SELECTED, curses.COLOR_GREEN, -1)
    curses.init_pair(_CP_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(_CP_FOOTER, curses.COLOR_YELLOW, -1)
    curses.init_pair(_CP_CURSOR, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(_CP_DESC, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_CATEGORY, curses.COLOR_CYAN, -1)
    curses.curs_set(0)
    stdscr.keypad(True)

    filter_text = ""
    cursor = 0
    scroll_offset = 0

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        header_lines = 2
        footer_lines = 2
        list_height = max(1, max_y - header_lines - footer_lines)

        # -- Apply fuzzy filter -----------------------------------------------
        if filter_text:
            filtered = [
                e for e in commands
                if _fuzzy_match(filter_text, e.name + " " + e.description)
            ]
        else:
            filtered = list(commands)

        # -- Build visual rows: category headers + command rows ---------------
        # visual_rows: list of (entry | None, display_text, is_category_header)
        visual_rows: list[tuple[CommandEntry | None, str, bool]] = []
        last_category: str | None = None
        for entry in filtered:
            if not filter_text and entry.category != last_category:
                visual_rows.append((None, f" {entry.category.upper()}", True))
                last_category = entry.category
            visual_rows.append((entry, "", False))

        # Command index list (for cursor tracking)
        cmd_indices = [i for i, (e, _, _) in enumerate(visual_rows) if e is not None]
        n_cmds = len(cmd_indices)

        cursor = max(0, min(cursor, n_cmds - 1)) if n_cmds else 0

        # Determine the visual row index of the cursor
        cursor_vr = cmd_indices[cursor] if cmd_indices else 0

        # Scroll to keep cursor visible
        if cursor_vr < scroll_offset:
            scroll_offset = cursor_vr
        if cursor_vr >= scroll_offset + list_height:
            scroll_offset = cursor_vr - list_height + 1
        scroll_offset = max(0, scroll_offset)

        # -- Header -----------------------------------------------------------
        filter_display = filter_text or ""
        filter_box = f"[filter: {filter_display:<16}]"
        header_left = " Command Palette  "
        header_text = header_left + filter_box
        stdscr.attron(curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        stdscr.addnstr(0, 0, "─" * max_x, max_x)
        stdscr.addnstr(0, 0, header_text[:max_x - 1], max_x - 1)
        stdscr.attroff(curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        stdscr.addnstr(1, 0, "─" * max_x, max_x)

        # -- Draw visible rows ------------------------------------------------
        if not visual_rows:
            stdscr.attron(curses.A_DIM)
            try:
                stdscr.addnstr(header_lines + 1, 2, "(no matching commands)", max_x - 3)
            except curses.error:
                pass
            stdscr.attroff(curses.A_DIM)
        else:
            for i in range(list_height):
                vr_idx = scroll_offset + i
                if vr_idx >= len(visual_rows):
                    break
                entry, _, is_cat = visual_rows[vr_idx]
                row = header_lines + i
                if row >= max_y - footer_lines:
                    break

                if is_cat:
                    try:
                        stdscr.addnstr(
                            row, 0,
                            visual_rows[vr_idx][1].ljust(max_x),
                            max_x - 1,
                            curses.color_pair(_CP_CATEGORY) | curses.A_BOLD | curses.A_DIM,
                        )
                    except curses.error:
                        pass
                    continue

                assert entry is not None
                # Find this entry's position in cmd_indices to check if cursor
                cmd_pos = next(
                    (ci for ci, vi in enumerate(cmd_indices) if vi == vr_idx), -1
                )
                is_active = cmd_pos == cursor

                # Build the display line
                name_col = f" {entry.name:<18}"
                args_col = f"{entry.args_hint:<16}" if entry.args_hint else " " * 16
                desc_budget = max(0, max_x - len(name_col) - len(args_col) - 4)
                desc = entry.description
                if len(desc) > desc_budget > 3:
                    desc = desc[: desc_budget - 1] + "…"

                marker = ">" if is_active else " "
                line = f" {marker}{name_col}{args_col}  {desc}"

                attr: int
                if is_active:
                    attr = curses.color_pair(_CP_CURSOR) | curses.A_BOLD
                else:
                    attr = curses.color_pair(_CP_FILE)

                try:
                    stdscr.addnstr(row, 0, line.ljust(max_x), max_x - 1, attr)
                except curses.error:
                    pass

            # Scroll indicators
            if scroll_offset > 0:
                try:
                    stdscr.addnstr(header_lines, max_x - 2, "▲", 1, curses.A_DIM)
                except curses.error:
                    pass
            if scroll_offset + list_height < len(visual_rows):
                try:
                    stdscr.addnstr(
                        header_lines + list_height - 1, max_x - 2, "▼", 1, curses.A_DIM,
                    )
                except curses.error:
                    pass

        # -- Footer -----------------------------------------------------------
        footer_y = max_y - footer_lines
        if footer_y > header_lines:
            try:
                stdscr.addnstr(footer_y, 0, "─" * max_x, max_x)
            except curses.error:
                pass
            stdscr.attron(curses.color_pair(_CP_FOOTER))
            controls = " ↑↓ navigate  type to filter  Backspace erase  Enter select  Esc cancel"
            try:
                stdscr.addnstr(footer_y + 1, 0, controls, max_x - 1)
            except curses.error:
                pass
            stdscr.attroff(curses.color_pair(_CP_FOOTER))

        stdscr.refresh()

        # -- Input handling ---------------------------------------------------
        key = stdscr.getch()

        if key == 27:  # Esc → cancel
            return None

        if key == curses.KEY_UP or key == ord("k"):
            if cursor > 0:
                cursor -= 1

        elif key == curses.KEY_DOWN or key == ord("j"):
            if cursor < n_cmds - 1:
                cursor += 1

        elif key == curses.KEY_SR:  # Shift+Up
            cursor = max(0, cursor - _FAST_SCROLL_LINES)

        elif key == curses.KEY_SF:  # Shift+Down
            cursor = min(max(0, n_cmds - 1), cursor + _FAST_SCROLL_LINES)

        elif key == curses.KEY_PPAGE:
            cursor = max(0, cursor - list_height)

        elif key == curses.KEY_NPAGE:
            cursor = min(max(0, n_cmds - 1), cursor + list_height)

        elif key == curses.KEY_HOME:
            cursor = 0

        elif key == curses.KEY_END:
            cursor = max(0, n_cmds - 1)

        elif key in (curses.KEY_ENTER, 10, 13):
            if not cmd_indices:
                continue
            entry, _, _ = visual_rows[cmd_indices[cursor]]
            if entry is None:
                continue
            return entry.name + (" " if entry.args_hint else "")

        elif key in (curses.KEY_BACKSPACE, 127, 8):  # Backspace / DEL
            if filter_text:
                filter_text = filter_text[:-1]
                cursor = 0
                scroll_offset = 0

        elif 32 <= key <= 126:  # Printable ASCII
            filter_text += chr(key)
            cursor = 0
            scroll_offset = 0


# ---------------------------------------------------------------------------
# Capture template browser (used by /capture_browse)
# ---------------------------------------------------------------------------

def browse_capture_templates(
    templates: list,
) -> object | None:
    """Open an interactive capture template picker.

    *templates* is a list of ``CaptureTemplate`` instances. Returns the
    selected template, or ``None`` on cancel.
    """
    if not templates:
        return None
    try:
        return curses.wrapper(_browse_captures_inner, templates)
    except Exception:
        return None


def _browse_captures_inner(
    stdscr: curses.window,
    templates: list,
) -> object | None:
    _init_colors()
    curses.curs_set(0)
    stdscr.keypad(True)

    cursor = 0
    scroll_offset = 0

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        header_lines = 2
        footer_lines = 2
        preview_width = max(20, max_x // 2)
        list_width = max_x - preview_width - 1
        list_height = max(1, max_y - header_lines - footer_lines)

        # -- Header -----------------------------------------------------------
        header_text = " Browse: Capture Templates"
        stdscr.attron(curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        stdscr.addnstr(0, 0, "─" * max_x, max_x)
        stdscr.addnstr(0, 0, header_text, max_x - 1)
        stdscr.attroff(curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        stdscr.addnstr(1, 0, "─" * max_x, max_x)

        # -- Build display blocks for each template --------------------------
        blocks: list[tuple[int, list[str]]] = []
        for ti, tpl in enumerate(templates):
            lines: list[str] = []
            key_label = f"[{tpl.key}]" if tpl.key else ""
            lines.append(f"  {tpl.display_name} {key_label}")
            desc_first = tpl.description.strip().splitlines()[0] if tpl.description.strip() else ""
            if desc_first:
                wrapped = textwrap.wrap(desc_first, width=max(20, list_width - 8))
                for wl in wrapped:
                    lines.append(f"      {wl}")
            lines.append("")
            blocks.append((ti, lines))

        # Flatten into visual rows
        visual_rows: list[tuple[int, str, bool]] = []
        for ti, block_lines in blocks:
            for li, text in enumerate(block_lines):
                visual_rows.append((ti, text, li == 0))

        tpl_start_row: dict[int, int] = {}
        for vr_idx, (ti, _, is_name) in enumerate(visual_rows):
            if is_name and ti not in tpl_start_row:
                tpl_start_row[ti] = vr_idx

        # Clamp cursor
        cursor = max(0, min(cursor, len(templates) - 1))
        target_vr = tpl_start_row.get(cursor, 0)
        if target_vr < scroll_offset:
            scroll_offset = target_vr
        if target_vr >= scroll_offset + list_height:
            scroll_offset = target_vr - list_height + 1
        scroll_offset = max(0, scroll_offset)

        # -- Draw left pane (template list) -----------------------------------
        for i in range(list_height):
            vr_idx = scroll_offset + i
            if vr_idx >= len(visual_rows):
                break
            ti, text, is_name = visual_rows[vr_idx]
            row = header_lines + i
            if row >= max_y - footer_lines:
                break

            is_active = ti == cursor

            if is_name and is_active:
                marker_line = f" > {text.lstrip()}"
            elif is_name:
                marker_line = f"   {text.lstrip()}"
            else:
                marker_line = text

            if is_active:
                attr = curses.color_pair(_CP_CURSOR) | (curses.A_BOLD if is_name else 0)
            else:
                attr = (curses.color_pair(_CP_FILE) | curses.A_BOLD) if is_name else curses.A_DIM

            try:
                padded = marker_line[:list_width].ljust(list_width)
                stdscr.addnstr(row, 0, padded, list_width, attr)
            except curses.error:
                pass

        # -- Draw separator ---------------------------------------------------
        sep_col = list_width
        for row in range(header_lines, max_y - footer_lines):
            try:
                stdscr.addch(row, sep_col, curses.ACS_VLINE, curses.A_DIM)
            except curses.error:
                pass

        # -- Draw right pane (preview) ----------------------------------------
        if templates and 0 <= cursor < len(templates):
            tpl = templates[cursor]
            preview_col = sep_col + 2
            pw = max(1, max_x - preview_col - 1)
            preview_lines: list[tuple[str, int]] = []

            preview_lines.append((f"Name: {tpl.display_name}", curses.A_BOLD))
            preview_lines.append((f"Key:  {tpl.key}", 0))
            preview_lines.append((f"Type: {tpl.template_type.value if hasattr(tpl.template_type, 'value') else tpl.template_type}", 0))
            if tpl.target_dir:
                preview_lines.append((f"Dir:  {tpl.target_dir}", 0))
            preview_lines.append(("", 0))

            if tpl.filetags:
                preview_lines.append((f"Tags: {', '.join(tpl.filetags)}", curses.color_pair(_CP_SELECTED)))

            if tpl.fields:
                preview_lines.append(("", 0))
                preview_lines.append(("Fields:", curses.A_BOLD))
                for f in tpl.fields:
                    default_hint = f" [{f.default}]" if f.default else ""
                    ftype = f.type.value if hasattr(f.type, "value") else f.type
                    preview_lines.append((f"  {f.name} ({ftype}){default_hint}", 0))

            if tpl.headings:
                preview_lines.append(("", 0))
                preview_lines.append(("Headings:", curses.A_BOLD))
                for h in tpl.headings:
                    preview_lines.append((f"  {'*' * h.level} {h.title}", 0))

            if tpl.agent_dispatch:
                preview_lines.append(("", 0))
                preview_lines.append(("Agent Dispatch:", curses.A_BOLD))
                ad = tpl.agent_dispatch
                trigger = ad.on.value if hasattr(ad.on, "value") else ad.on
                preview_lines.append((f"  @{ad.agent_type} ({trigger})", curses.color_pair(_CP_FOOTER)))

            for pi, (text, attr) in enumerate(preview_lines):
                row = header_lines + pi
                if row >= max_y - footer_lines:
                    break
                try:
                    stdscr.addnstr(row, preview_col, text[:pw], pw, attr)
                except curses.error:
                    pass

        # -- Footer -----------------------------------------------------------
        footer_y = max_y - footer_lines
        if footer_y > header_lines:
            try:
                stdscr.addnstr(footer_y, 0, "─" * max_x, max_x)
            except curses.error:
                pass
            stdscr.attron(curses.color_pair(_CP_FOOTER))
            controls = " ↑↓ navigate  Shift+↑↓ fast  Enter apply  Esc cancel"
            try:
                stdscr.addnstr(footer_y + 1, 0, controls, max_x - 1)
            except curses.error:
                pass
            stdscr.attroff(curses.color_pair(_CP_FOOTER))

        stdscr.refresh()

        # -- Input handling ---------------------------------------------------
        key = stdscr.getch()

        if key in (27, ord("q")):
            return None

        if key == curses.KEY_UP or key == ord("k"):
            if cursor > 0:
                cursor -= 1
        elif key == curses.KEY_DOWN or key == ord("j"):
            if cursor < len(templates) - 1:
                cursor += 1
        elif key == curses.KEY_SR:
            cursor = max(0, cursor - _FAST_SCROLL_LINES)
        elif key == curses.KEY_SF:
            cursor = min(len(templates) - 1, cursor + _FAST_SCROLL_LINES)
        elif key in (curses.KEY_ENTER, 10, 13):
            return templates[cursor]
        elif key == curses.KEY_HOME:
            cursor = 0
        elif key == curses.KEY_END:
            cursor = len(templates) - 1
