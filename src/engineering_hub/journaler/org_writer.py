"""Org-roam write utilities for the Journaler daemon.

Provides safe, format-aware functions for common write operations on org-roam
files.  All functions return ``(ok, message)`` so callers can display status
without raising.

Supported operations
--------------------
- ``append_to_heading``     — add body text under a named heading
- ``assert_org_path_under_roam`` — verify a path is a writable ``.org`` under roam root
- ``add_todo_to_journal``   — insert a ``- [ ]`` item in today's daily journal
- ``mark_done_in_journal``  — flip a matching ``- [ ]`` to ``- [X]``
- ``create_org_node``       — create a new org-roam node with UUID and frontmatter
- ``find_org_by_title``     — locate files by ``#+title:`` (case-insensitive)

Format conventions produced
---------------------------
Node file::

    :PROPERTIES:
    :ID:       <uuid4>
    :END:
    #+title: My Note Title
    #+filetags: :engineering:project-42:
    #+created: [2026-04-02 Wed 08:30]

    * First heading

    Body text.

Daily journal items follow the Engineering Hub ``- [ ] @agent: description``
convention understood by the Orchestrator's task parser.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(\*+)\s+", re.MULTILINE)
_TODO_ITEM_RE = re.compile(r"^(\s*[-*]\s+)\[ \]\s+(.+)$", re.MULTILINE)


def _org_timestamp(dt: datetime | None = None) -> str:
    """Return an inactive org timestamp string: ``[YYYY-MM-DD Day HH:MM]``."""
    if dt is None:
        dt = datetime.now()
    day_abbr = dt.strftime("%a")
    return dt.strftime(f"[%Y-%m-%d {day_abbr} %H:%M]")


def _org_active_timestamp(dt: datetime | None = None) -> str:
    """Return an active org timestamp string: ``<YYYY-MM-DD Day HH:MM>``."""
    if dt is None:
        dt = datetime.now()
    day_abbr = dt.strftime("%a")
    return dt.strftime(f"<%Y-%m-%d {day_abbr} %H:%M>")


def _today_journal_path(journal_dir: Path) -> Path:
    """Return the path for today's daily journal file."""
    today = datetime.now().strftime("%Y-%m-%d")
    return journal_dir / f"{today}.org"


def _create_journal_file(path: Path) -> None:
    """Create a minimal daily journal file if it does not exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    today = path.stem
    try:
        dt = datetime.strptime(today, "%Y-%m-%d")
        day_abbr = dt.strftime("%a")
        ts = f"[{today} {day_abbr}]"
    except ValueError:
        ts = f"[{today}]"

    content = (
        f":PROPERTIES:\n"
        f":ID:       {uuid.uuid4()}\n"
        f":END:\n"
        f"#+title: {today}\n"
        f"#+filetags: :journal:\n"
        f"#+created: {_org_timestamp()}\n"
        f"\n"
        f"* Overnight Agent Tasks\n\n"
        f"* Notes\n\n"
    )
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assert_org_path_under_roam(
    path: Path,
    org_roam_dir: Path,
) -> tuple[bool, Path | str]:
    """Verify *path* is an existing ``.org`` file under *org_roam_dir*.

    Both paths are expanded and resolved. Used by journaler ``/open`` so
    edits cannot escape the roam tree.

    Returns:
        ``(True, resolved_path)`` on success, or ``(False, error_message)``.
    """
    roam_root = org_roam_dir.expanduser().resolve()
    candidate = path.expanduser().resolve()
    if not candidate.exists():
        return False, f"File not found: {candidate}"
    if not candidate.is_file():
        return False, f"Not a file: {candidate}"
    if candidate.suffix.lower() != ".org":
        return False, f"Not an org file: {candidate.name}"
    try:
        if not candidate.is_relative_to(roam_root):
            return False, f"Path must be under org-roam root {roam_root}"
    except ValueError:
        return False, f"Path must be under org-roam root {roam_root}"
    return True, candidate


def append_to_heading(
    path: Path,
    heading: str,
    text: str,
    create_heading_if_missing: bool = True,
) -> tuple[bool, str]:
    """Append ``text`` under a named heading in an existing org file.

    Searches for the first ``* <heading>`` (any depth, case-sensitive) and
    appends ``text`` immediately before the next same-or-higher heading, or
    at the end of the section.  If the heading is not found and
    ``create_heading_if_missing`` is True, appends a new top-level heading.

    Args:
        path: Path to the ``.org`` file to modify.
        heading: Heading title text (without leading ``*`` stars).
        text: Content to append.  Leading/trailing whitespace is stripped;
            a single blank line is inserted before the text.
        create_heading_if_missing: Whether to add the heading if absent.

    Returns:
        ``(ok, message)``
    """
    path = path.expanduser().resolve()
    if not path.exists():
        return False, f"File not found: {path}"
    if not path.is_file():
        return False, f"Not a file: {path}"

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Could not read {path.name}: {exc}"

    heading_pattern = re.compile(
        r"^(\*+)\s+" + re.escape(heading) + r"\s*$", re.MULTILINE
    )
    match = heading_pattern.search(raw)

    text_block = "\n" + text.strip() + "\n"

    if match:
        star_count = len(match.group(1))
        # Find the next heading at the same or higher level
        rest_start = match.end()
        next_heading = re.compile(
            r"^\*{1," + str(star_count) + r"}\s+", re.MULTILINE
        )
        next_match = next_heading.search(raw, rest_start)
        insert_pos = next_match.start() if next_match else len(raw)
        # Ensure a blank line before insertion
        section = raw[rest_start:insert_pos].rstrip("\n")
        new_raw = raw[:rest_start] + section + text_block + raw[insert_pos:]
    elif create_heading_if_missing:
        new_raw = raw.rstrip("\n") + f"\n\n* {heading}\n{text_block}"
    else:
        return False, f"Heading '* {heading}' not found in {path.name}"

    try:
        path.write_text(new_raw, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write {path.name}: {exc}"

    return True, f"Appended to '* {heading}' in {path.name}"


def add_todo_to_journal(
    journal_dir: Path,
    description: str,
    section_heading: str = "Overnight Agent Tasks",
) -> tuple[bool, str]:
    """Add a ``- [ ] <description>`` item to today's daily journal.

    The item is appended under ``section_heading``.  The journal file is
    created with minimal frontmatter if it does not yet exist.

    Args:
        journal_dir: Directory containing ``YYYY-MM-DD.org`` daily files.
        description: Task description text.
        section_heading: Heading under which to insert the item.

    Returns:
        ``(ok, message)``
    """
    journal_dir = journal_dir.expanduser().resolve()
    today_path = _today_journal_path(journal_dir)
    _create_journal_file(today_path)

    item_text = f"- [ ] {description.strip()}"
    ok, msg = append_to_heading(
        today_path,
        section_heading,
        item_text,
        create_heading_if_missing=True,
    )
    if ok:
        return True, f"Added task to {today_path.name} under '* {section_heading}'"
    return ok, msg


def mark_done_in_journal(
    journal_dir: Path,
    description_fragment: str,
    section_heading: str | None = None,
) -> tuple[bool, str]:
    """Mark the first matching ``- [ ]`` item as ``- [X]`` in today's journal.

    Searches today's daily journal for a checkbox item whose text contains
    ``description_fragment`` (case-insensitive).  When found, replaces
    ``[ ]`` with ``[X]`` and appends a ``CLOSED:`` timestamp on the same line.

    Args:
        journal_dir: Directory containing daily journal files.
        description_fragment: Substring to match against the item text.
        section_heading: If provided, restricts the search to the named heading
            section.  If None, searches the whole file.

    Returns:
        ``(ok, message)``
    """
    journal_dir = journal_dir.expanduser().resolve()
    today_path = _today_journal_path(journal_dir)

    if not today_path.exists():
        return False, f"Today's journal not found: {today_path.name}"

    try:
        raw = today_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Could not read {today_path.name}: {exc}"

    fragment_lower = description_fragment.lower()
    closed_ts = f"CLOSED: {_org_timestamp()}"

    def _replace_first(text: str) -> tuple[str, bool]:
        for m in _TODO_ITEM_RE.finditer(text):
            if fragment_lower in m.group(2).lower():
                replacement = f"{m.group(1)}[X] {m.group(2)} {closed_ts}"
                return text[: m.start()] + replacement + text[m.end() :], True
        return text, False

    if section_heading:
        heading_pattern = re.compile(
            r"^(\*+)\s+" + re.escape(section_heading) + r"\s*$", re.MULTILINE
        )
        hm = heading_pattern.search(raw)
        if not hm:
            return False, f"Heading '* {section_heading}' not found in {today_path.name}"
        star_count = len(hm.group(1))
        rest_start = hm.end()
        next_heading = re.compile(r"^\*{1," + str(star_count) + r"}\s+", re.MULTILINE)
        nm = next_heading.search(raw, rest_start)
        section_end = nm.start() if nm else len(raw)
        section_text, changed = _replace_first(raw[rest_start:section_end])
        new_raw = raw[:rest_start] + section_text + raw[section_end:]
    else:
        new_raw, changed = _replace_first(raw)

    if not changed:
        return False, f"No unchecked item matching '{description_fragment}' found in {today_path.name}"

    try:
        today_path.write_text(new_raw, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write {today_path.name}: {exc}"

    return True, f"Marked done in {today_path.name}: '{description_fragment}'"


def create_org_node(
    roam_dir: Path,
    title: str,
    filetags: list[str] | None = None,
    body: str = "",
) -> tuple[bool, str]:
    """Create a new org-roam node file with proper frontmatter.

    The file is placed directly under ``roam_dir`` and named using the current
    datetime prefix (``YYYYMMDDHHMMSS-<slug>.org``) matching the org-roam
    naming convention.  A UUID `:ID:` property is generated automatically.

    Args:
        roam_dir: Target directory for the new file.
        title: Node title (used in ``#+title:`` and the filename slug).
        filetags: Optional list of tag strings (without colons).
        body: Optional body content appended after the frontmatter.

    Returns:
        ``(ok, message)`` where message includes the new file path on success.
    """
    roam_dir = roam_dir.expanduser().resolve()
    if not roam_dir.exists():
        return False, f"Directory not found: {roam_dir}"

    node_id = str(uuid.uuid4())
    now = datetime.now()
    date_prefix = now.strftime("%Y%m%d%H%M%S")
    day_abbr = now.strftime("%a")
    created_ts = now.strftime(f"[%Y-%m-%d {day_abbr} %H:%M]")

    slug = "".join(
        c if c.isalnum() or c == "-" else "-" for c in title.lower()
    ).strip("-")
    slug = "-".join(filter(None, slug.split("-")))[:60]
    filename = f"{date_prefix}-{slug}.org"
    file_path = roam_dir / filename

    tags_str = ""
    if filetags:
        inner = ":".join(t.strip().lstrip(":").rstrip(":") for t in filetags if t.strip())
        tags_str = f":{inner}:" if inner else ""

    lines = [
        ":PROPERTIES:",
        f":ID:       {node_id}",
        ":END:",
        f"#+title: {title}",
    ]
    if tags_str:
        lines.append(f"#+filetags: {tags_str}")
    lines.append(f"#+created: {created_ts}")
    lines.append("")

    if body.strip():
        lines.append(body.strip())
        lines.append("")

    content = "\n".join(lines)

    try:
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write {filename}: {exc}"

    return True, f"Created org-roam node: {file_path}"


def find_org_by_title(
    roam_dir: Path,
    title_fragment: str,
    recursive: bool = True,
) -> tuple[bool, list[Path]]:
    """Search org files for a case-insensitive ``#+title:`` match.

    Reads only the first 50 lines of each file so the scan stays fast even
    for large org-roam corpora.

    Args:
        roam_dir: Root directory to search.
        title_fragment: Substring to match against ``#+title:`` values.
        recursive: If True (default), search subdirectories.

    Returns:
        ``(ok, [matching_paths])`` — ok is False only on directory errors.
    """
    roam_dir = roam_dir.expanduser().resolve()
    if not roam_dir.exists():
        return False, []

    pattern = "**/*.org" if recursive else "*.org"
    fragment_lower = title_fragment.lower()
    title_re = re.compile(r"^\s*#\+title:\s*(.+)$", re.IGNORECASE)

    matches: list[Path] = []
    for org_file in sorted(roam_dir.glob(pattern)):
        try:
            with org_file.open(encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 50:
                        break
                    m = title_re.match(line)
                    if m and fragment_lower in m.group(1).lower():
                        matches.append(org_file)
                        break
        except OSError:
            continue

    return True, matches
