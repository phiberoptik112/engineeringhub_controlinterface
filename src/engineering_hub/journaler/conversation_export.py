"""Export Journaler chat transcripts to org-mode for org-roam."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engineering_hub.journaler.org_writer import _org_timestamp


def load_transcript(path: Path) -> list[dict[str, Any]]:
    """Load conversation turns from JSONL (one object per line).

    Preserves file order. Skips malformed lines.
    """
    path = path.expanduser().resolve()
    if not path.is_file():
        return []

    turns: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        role = obj.get("role")
        content = obj.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        turns.append(obj)
    return turns


def transcript_to_plain_text(turns: list[dict[str, Any]]) -> str:
    """Flatten transcript for model prompts."""
    lines: list[str] = []
    for t in turns:
        role = str(t.get("role", "")).upper()
        ts = t.get("timestamp", "")
        archived = t.get("archived")
        prefix = f"{role}"
        if archived:
            prefix += " (archived)"
        if isinstance(ts, str) and ts:
            prefix += f" [{ts}]"
        content = t.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        lines.append(f"{prefix}:\n{content}")
    return "\n\n".join(lines)


def _escape_src_block_body(text: str) -> str:
    """Ensure #+end_src is not accidentally closed by message content."""
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("#+end_src") or s.startswith("#+begin_src"):
            out.append(" " + line)
        else:
            out.append(line)
    return "\n".join(out)


def render_raw_org(
    turns: list[dict[str, Any]],
    *,
    title: str = "Journaler conversation export",
) -> str:
    """Render transcript as org with per-turn headings and text src blocks."""
    now = datetime.now(timezone.utc).astimezone()
    stamp = _org_timestamp(now.replace(tzinfo=None))

    parts: list[str] = [
        f"Exported at: {stamp}",
        "",
        f"* {title}",
        "",
    ]

    for i, t in enumerate(turns):
        role = str(t.get("role", "unknown")).upper()
        ts = t.get("timestamp", "")
        archived = t.get("archived")
        archived_tag = " (archived)" if archived else ""
        ts_bit = f" — {ts}" if isinstance(ts, str) and ts else ""
        content = t.get("content", "")
        if not isinstance(content, str):
            content = str(content)

        parts.append(f"** Turn {i + 1}: {role}{archived_tag}{ts_bit}")
        parts.append("#+begin_src text")
        parts.append(_escape_src_block_body(content.rstrip()))
        parts.append("#+end_src")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


_SUMMARIZE_PROMPT_TEMPLATE = """\
You are formatting a chat transcript for org mode (Emacs org-roam).

Read the conversation below. Output ONLY valid org structure, no markdown fences, no preamble:
1. A top-level heading: * Summary
   Then 1–3 short paragraphs of plain text (no subheadings inside Summary).
2. A top-level heading: * Open TODOs
   Then a list of checkbox items. Each line must start with exactly: "- [ ] "
   Include actionable follow-ups implied by the conversation. If there are none, output a single line: "- [ ] (none)".

Do not use ``` fences. Do not add #+title or PROPERTIES.

Conversation:
---
{transcript_text}
---
"""


def build_summarize_prompt(transcript_text: str) -> str:
    return _SUMMARIZE_PROMPT_TEMPLATE.format(transcript_text=transcript_text.strip())


_FENCE_RE = re.compile(
    r"^\s*```(?:\w*)?\s*\n(.*?)\n\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def postprocess_model_org(text: str) -> str:
    """Strip accidental markdown fences; trim whitespace."""
    s = text.strip()
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    return s.rstrip() + "\n"
