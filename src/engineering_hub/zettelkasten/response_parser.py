"""Parse zettelkasten-curator LLM responses into ProposedAtomicNote objects.

Handles two output formats:
- Org-roam format (default): one or more note blocks delimited by #+title: headers.
- JSON format (explicit request only): the ``{"notes": [...]}`` shape from the prompt.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path

from engineering_hub.zettelkasten.models import (
    ProposedAtomicNote,
    SuggestedLink,
    stable_hash,
)

logger = logging.getLogger(__name__)

# ── Org-roam section header patterns ────────────────────────────────────────

_TITLE_RE = re.compile(r"^#\+title:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_FILETAGS_RE = re.compile(r"^#\+filetags:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
# Matches   - [[target][title]] — reason  OR  - [[target][title]] (no reason)
_ORG_LINK_RE = re.compile(
    r"-\s+\[\[([^\]]+)\]\[([^\]]+)\]\](?:\s+[—–-]\s+(.+))?$"
)
_SECTION_RE = re.compile(r"^\*+\s+(.+?)\s*$", re.MULTILINE)


def parse_curator_response(
    response: str,
    task_description: str,
    *,
    org_journal_dir: Path | None = None,
) -> list[ProposedAtomicNote]:
    """Parse an LLM response from the zettelkasten-curator into ProposedAtomicNote objects.

    Args:
        response: Raw text returned by the LLM.
        task_description: The original user task (used as source_heading fallback).
        org_journal_dir: When provided, today's daily journal path is used as source_path.

    Returns:
        A list of ProposedAtomicNote instances ready for write_proposal_batch().
    """
    stripped = response.strip()
    if not stripped:
        return []

    source_path = _derive_source_path(org_journal_dir)

    # JSON path: explicit machine-readable requests
    if _looks_like_json(stripped):
        logger.debug("Curator response detected as JSON — using JSON parser.")
        return _parse_json(stripped, task_description, source_path)

    # Org-roam path: default
    logger.debug("Curator response detected as org-roam format — using org parser.")
    return _parse_org(stripped, task_description, source_path)


# ── JSON parsing ─────────────────────────────────────────────────────────────


def _looks_like_json(text: str) -> bool:
    return text.startswith("{") or text.startswith("[")


def _parse_json(text: str, task_description: str, source_path: str) -> list[ProposedAtomicNote]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse curator response as JSON: %s", exc)
        return []

    # Accept both top-level list and {"notes": [...]} wrapper
    if isinstance(data, list):
        raw_notes = data
    elif isinstance(data, dict):
        raw_notes = data.get("notes", [])
    else:
        return []

    notes: list[ProposedAtomicNote] = []
    for raw in raw_notes:
        if not isinstance(raw, dict):
            continue
        title = (raw.get("title") or "").strip()
        body = (raw.get("body") or "").strip()
        if not title:
            continue

        tags = list(raw.get("tags") or [])
        open_q = list(raw.get("open_questions") or [])
        links = _json_links_to_suggested(raw.get("related") or [])

        if open_q:
            body = body + "\n\n** Open Questions\n" + "\n".join(f"- {q}" for q in open_q)

        notes.append(_build_note(title, body, tags, links, task_description, source_path))

    return notes


def _json_links_to_suggested(related: list) -> list[SuggestedLink]:
    links = []
    for item in related:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        target = (item.get("target") or "").strip()
        reason = (item.get("reason") or "").strip()
        if not title:
            continue
        links.append(
            SuggestedLink(
                title=title,
                target=target or title,
                similarity=0.0,
                reason=reason or "Suggested by curator agent.",
                category="Tangentially Related",
            )
        )
    return links


# ── Org-roam parsing ──────────────────────────────────────────────────────────


def _parse_org(text: str, task_description: str, source_path: str) -> list[ProposedAtomicNote]:
    """Split a multi-note org response on #+title: boundaries and parse each block."""
    # Find all #+title: positions to split the text into per-note segments
    title_matches = list(_TITLE_RE.finditer(text))
    if not title_matches:
        logger.warning("No #+title: found in curator org response — cannot parse notes.")
        return []

    notes = []
    for i, match in enumerate(title_matches):
        start = match.start()
        end = title_matches[i + 1].start() if i + 1 < len(title_matches) else len(text)
        block = text[start:end]
        note = _parse_org_block(block, task_description, source_path)
        if note is not None:
            notes.append(note)

    return notes


def _parse_org_block(
    block: str, task_description: str, source_path: str
) -> ProposedAtomicNote | None:
    title_match = _TITLE_RE.search(block)
    if not title_match:
        return None
    title = title_match.group(1).strip()
    if not title:
        return None

    tags = _parse_filetags(block)
    body = _extract_body_section(block, title)
    links = _extract_related_links(block)
    open_questions = _extract_open_questions(block)

    if open_questions:
        body = body + "\n\n** Open Questions\n" + "\n".join(f"- {q}" for q in open_questions)

    return _build_note(title, body, tags, links, task_description, source_path)


def _parse_filetags(block: str) -> list[str]:
    match = _FILETAGS_RE.search(block)
    if not match:
        return ["zettelkasten"]
    raw = match.group(1).strip()
    # ":tag1:tag2:" → ["tag1", "tag2"]
    tags = [t.strip() for t in raw.strip(":").split(":") if t.strip()]
    if not tags:
        tags = ["zettelkasten"]
    return tags


def _extract_body_section(block: str, title: str) -> str:
    """Extract the main body text — the content between the title heading and the next section."""
    # Find the "* <title>" heading line
    heading_pattern = re.compile(
        r"^\*+\s+" + re.escape(title) + r"\s*$", re.MULTILINE | re.IGNORECASE
    )
    heading_match = heading_pattern.search(block)
    if not heading_match:
        # Fallback: grab everything after the last metadata header line
        lines = block.splitlines()
        body_lines = []
        in_properties = False
        past_headers = False
        for line in lines:
            if line.strip() == ":PROPERTIES:":
                in_properties = True
                continue
            if line.strip() == ":END:":
                in_properties = False
                continue
            if in_properties:
                continue
            if line.startswith("#+"):
                past_headers = True
                continue
            if past_headers and line.strip():
                body_lines.append(line)
        return "\n".join(body_lines).strip()

    after_heading = block[heading_match.end():]
    # Stop at the next top-level section ("* Something")
    next_section = re.search(r"^\*+\s+", after_heading, re.MULTILINE)
    if next_section:
        body_text = after_heading[: next_section.start()]
    else:
        body_text = after_heading

    return body_text.strip()


def _extract_related_links(block: str) -> list[SuggestedLink]:
    """Extract org-roam links from the * Related section."""
    related_match = re.search(r"^\*+\s+Related\s*$", block, re.MULTILINE | re.IGNORECASE)
    if not related_match:
        return []

    after = block[related_match.end():]
    next_section = re.search(r"^\*+\s+", after, re.MULTILINE)
    section_text = after[: next_section.start()] if next_section else after

    links = []
    for line in section_text.splitlines():
        m = _ORG_LINK_RE.search(line)
        if not m:
            continue
        target = m.group(1).strip()
        link_title = m.group(2).strip()
        reason = (m.group(3) or "").strip() or "Suggested by curator agent."
        links.append(
            SuggestedLink(
                title=link_title,
                target=target,
                similarity=0.0,
                reason=reason,
                category="Tangentially Related",
            )
        )
    return links


def _extract_open_questions(block: str) -> list[str]:
    oq_match = re.search(
        r"^\*+\s+Open\s+Questions\s*$", block, re.MULTILINE | re.IGNORECASE
    )
    if not oq_match:
        return []

    after = block[oq_match.end():]
    next_section = re.search(r"^\*+\s+", after, re.MULTILINE)
    section_text = after[: next_section.start()] if next_section else after

    questions = []
    for line in section_text.splitlines():
        stripped = line.strip().lstrip("- ").strip()
        if stripped:
            questions.append(stripped)
    return questions


# ── Shared helpers ────────────────────────────────────────────────────────────


def _build_note(
    title: str,
    body: str,
    tags: list[str],
    links: list[SuggestedLink],
    source_heading: str,
    source_path: str,
) -> ProposedAtomicNote:
    from datetime import datetime

    source_hash = stable_hash(title + "\n" + body)
    return ProposedAtomicNote(
        proposal_id=source_hash[:12],
        node_id=str(uuid.uuid4()),
        title=title,
        body=body,
        source_path=source_path,
        source_heading=source_heading[:120] if source_heading else "",
        source_hash=source_hash,
        tags=tags,
        links=links,
        created_at=datetime.now().isoformat(timespec="seconds"),
        status="proposed",
    )


def _derive_source_path(org_journal_dir: Path | None) -> str:
    if org_journal_dir is None:
        return ""
    today = date.today().isoformat()
    return str(org_journal_dir.expanduser() / f"{today}.org")
