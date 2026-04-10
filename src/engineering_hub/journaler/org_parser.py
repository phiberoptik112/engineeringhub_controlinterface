"""Focused org-mode parser for the Journaler daemon.

Extracts headings, TODO/DONE items, timestamps, properties, filetags,
and body text from org files.  Not a general-purpose org parser — just
the subset needed for ambient awareness scanning.

Builds on patterns from ``notes.weekly_reader`` and ``notes.org_task_parser``
but returns richer ``OrgEntry`` structures suitable for context compression.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from engineering_hub.journaler.models import OrgEntry, OrgFileInfo

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# :PROPERTIES: ... :END: block (file-level or entry-level)
_PROPERTIES_BLOCK = re.compile(r":PROPERTIES:\s*\n(.*?):END:", re.DOTALL)

# Individual property line inside a drawer:  :KEY: value
_PROPERTY_LINE = re.compile(r"^\s*:([A-Za-z_-]+):\s+(.+)$", re.MULTILINE)

# #+keyword: value lines
_KEYWORD_LINE = re.compile(r"^\s*#\+(\w+):\s*(.*)$", re.MULTILINE)

# Org heading: stars, optional TODO/DONE keyword, title, optional :tags:
_HEADING = re.compile(
    r"^(\*+)\s+"
    r"(?:(TODO|DONE|WAITING|CANCELLED)\s+)?"
    r"(.+?)(?:\s+:([\w:]+):)?\s*$",
    re.MULTILINE,
)

# Checkbox items: - [ ] text or - [X] text
_CHECKBOX = re.compile(r"^\s*[-*]\s+\[([xX ])\]\s+(.+)$", re.MULTILINE)

# Org active timestamp: <2026-04-01 Tue 09:00>
_ACTIVE_TS = re.compile(r"<(\d{4}-\d{2}-\d{2})\s+\w{2,3}(?:\s+(\d{2}:\d{2}))?>")

# Org inactive timestamp: [2026-04-01 Tue]
_INACTIVE_TS = re.compile(r"\[(\d{4}-\d{2}-\d{2})\s+\w{2,3}(?:\s+(\d{2}:\d{2}))?\]")

# @agent: task prefix (from org_task_parser conventions)
_AGENT_TASK = re.compile(
    r"^\s*[-*]\s+\[([xX ])\]\s+@([\w-]+):\s+(.+)$", re.MULTILINE
)

# Standards references: ASTM E336, ISO 717-1, IBC 1207.3, E1007, etc.
_STANDARDS_REF = re.compile(
    r"\b(?:ASTM\s+[A-Z]\d{3,4}(?:[/-]\d+)?|ISO\s+\d{3,5}(?:[/-]\d+)?|IBC\s+[\d.]+|E\d{3,4}(?:-\d+)?|ANSI\s+[\w/.-]+)\b",
    re.IGNORECASE,
)


def parse_org_file(path: Path, max_body_chars: int = 500) -> OrgFileInfo:
    """Extract structured entries from an org file.

    Returns an OrgFileInfo with file-level metadata and a flat list of
    OrgEntry objects.  Body text is truncated to ``max_body_chars`` per entry.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return OrgFileInfo(path=path)

    title, filetags, cleaned = _extract_file_metadata(raw)
    entries = _parse_entries(cleaned, max_body_chars)

    return OrgFileInfo(
        path=path,
        title=title,
        filetags=filetags,
        entries=entries,
    )


def extract_pending_tasks(entries: list[OrgEntry]) -> list[str]:
    """Pull all uncompleted task items from parsed entries."""
    results: list[str] = []
    for entry in entries:
        if entry.state == "TODO":
            results.append(entry.title)
        # Also scan body for checkbox items
        for m in _CHECKBOX.finditer(entry.body):
            if m.group(1).strip() == "":
                results.append(m.group(2))
        results.extend(extract_pending_tasks(entry.children))
    return results


def extract_completed_tasks(entries: list[OrgEntry]) -> list[str]:
    """Pull all completed task items from parsed entries."""
    results: list[str] = []
    for entry in entries:
        if entry.state == "DONE":
            results.append(entry.title)
        for m in _CHECKBOX.finditer(entry.body):
            if m.group(1).lower() == "x":
                results.append(m.group(2))
        results.extend(extract_completed_tasks(entry.children))
    return results


def extract_agent_tasks(entries: list[OrgEntry]) -> list[dict]:
    """Pull @agent task lines (Engineering Hub format) from entries.

    Returns dicts with keys: agent, description, checked.
    """
    results: list[dict] = []
    for entry in entries:
        for m in _AGENT_TASK.finditer(entry.body):
            results.append({
                "agent": m.group(2),
                "description": m.group(3).strip(),
                "checked": m.group(1).lower() == "x",
            })
        # Also check heading-level tasks with @agent in the title
        if entry.title.startswith("@"):
            parts = entry.title.split(":", 1)
            if len(parts) == 2:
                results.append({
                    "agent": parts[0].lstrip("@").strip(),
                    "description": parts[1].strip(),
                    "checked": entry.state == "DONE",
                })
        results.extend(extract_agent_tasks(entry.children))
    return results


def extract_topic_keywords(info: OrgFileInfo) -> list[str]:
    """Extract topic keywords from a parsed org file.

    Returns a deduplicated list of:
    - Heading titles (excluding raw TODO/DONE/WAITING/CANCELLED keywords alone)
    - Standards references found in headings or body text
    - Filetags from the file-level metadata
    - Headings tagged with :meeting:, :call:, or :client:

    Results are suitable for cross-day topic frequency analysis.
    """
    _SKIP_STATES = {"TODO", "DONE", "WAITING", "CANCELLED"}
    _NOTABLE_TAGS = {"meeting", "call", "client", "project", "review"}

    seen: set[str] = set()
    keywords: list[str] = []

    def _add(kw: str) -> None:
        kw = kw.strip()
        if kw and kw not in seen:
            seen.add(kw)
            keywords.append(kw)

    # File-level tags
    for tag in info.filetags:
        _add(tag)

    def _visit(entries: list[OrgEntry]) -> None:
        for entry in entries:
            title = entry.title.strip()
            # Skip headings that are just a bare task state word
            if title.upper() in _SKIP_STATES:
                continue
            if title:
                _add(title)

            # Notable-tag headings get added even if already seen (for signal)
            for tag in entry.tags:
                if tag.lower() in _NOTABLE_TAGS:
                    # prefix with tag type for clarity, e.g. "meeting: Client call"
                    _add(f"{tag.lower()}: {title}")

            # Standards references from body text
            for m in _STANDARDS_REF.finditer(entry.body):
                _add(m.group(0).upper().strip())

            # Standards refs in the title itself
            for m in _STANDARDS_REF.finditer(title):
                _add(m.group(0).upper().strip())

            _visit(entry.children)

    _visit(info.entries)
    return keywords


def summarize_file(info: OrgFileInfo, max_chars: int = 800) -> str:
    """Produce a compact text summary of an org file for context injection."""
    parts: list[str] = []
    if info.title:
        parts.append(info.title)
    if info.filetags:
        parts.append(f"Tags: {', '.join(info.filetags)}")

    for entry in info.entries:
        prefix = "#" * entry.level
        state = f" {entry.state}" if entry.state else ""
        parts.append(f"{prefix}{state} {entry.title}")
        if entry.body.strip():
            body_preview = entry.body.strip()[:200]
            parts.append(f"  {body_preview}")

    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_file_metadata(raw: str) -> tuple[str, list[str], str]:
    """Extract title, filetags, and return cleaned text with metadata stripped."""
    title = ""
    filetags: list[str] = []

    for m in _KEYWORD_LINE.finditer(raw):
        keyword = m.group(1).lower()
        value = m.group(2).strip()
        if keyword == "title":
            title = value
        elif keyword == "filetags":
            filetags = [t.strip() for t in value.split(":") if t.strip()]

    cleaned = _KEYWORD_LINE.sub("", raw)

    # Strip file-level properties block
    cleaned = _PROPERTIES_BLOCK.sub("", cleaned, count=1)

    return title, filetags, cleaned


def _parse_entries(text: str, max_body_chars: int) -> list[OrgEntry]:
    """Parse org headings into a flat list of OrgEntry objects."""
    headings = list(_HEADING.finditer(text))
    if not headings:
        return []

    entries: list[OrgEntry] = []
    for i, m in enumerate(headings):
        level = len(m.group(1))
        state = m.group(2)  # TODO, DONE, or None
        title = m.group(3).strip()
        tags_str = m.group(4)
        tags = [t for t in (tags_str or "").split(":") if t] if tags_str else []

        body_start = m.end()
        body_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        raw_body = text[body_start:body_end]

        properties = _extract_properties(raw_body)
        timestamp = _extract_first_timestamp(raw_body)

        # Strip property drawers from body
        body = _PROPERTIES_BLOCK.sub("", raw_body).strip()
        if len(body) > max_body_chars:
            body = body[:max_body_chars].rstrip() + "..."

        entries.append(OrgEntry(
            level=level,
            title=title,
            state=state,
            tags=tags,
            timestamp=timestamp,
            body=body,
            properties=properties,
        ))

    return _nest_entries(entries)


def _nest_entries(flat: list[OrgEntry]) -> list[OrgEntry]:
    """Nest flat entries into a tree based on heading level.

    Returns only top-level entries; deeper entries become children.
    """
    if not flat:
        return []

    root: list[OrgEntry] = []
    stack: list[OrgEntry] = []

    for entry in flat:
        while stack and stack[-1].level >= entry.level:
            stack.pop()

        if stack:
            stack[-1].children.append(entry)
        else:
            root.append(entry)

        stack.append(entry)

    return root


def _extract_properties(text: str) -> dict[str, str]:
    """Extract properties from :PROPERTIES: ... :END: drawer."""
    props: dict[str, str] = {}
    block_match = _PROPERTIES_BLOCK.search(text)
    if block_match:
        for m in _PROPERTY_LINE.finditer(block_match.group(1)):
            props[m.group(1)] = m.group(2).strip()
    return props


def _extract_first_timestamp(text: str) -> datetime | None:
    """Extract the first timestamp (active or inactive) from text."""
    for pattern in (_ACTIVE_TS, _INACTIVE_TS):
        m = pattern.search(text)
        if m:
            date_str = m.group(1)
            time_str = m.group(2)
            try:
                if time_str:
                    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                return datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
    return None
