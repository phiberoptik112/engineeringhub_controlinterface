"""Parse an org-roam note into project context for template-based report drafting."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from engineering_hub.core.models import Project, ProjectContext, Standard

logger = logging.getLogger(__name__)

_KEYWORD_RE = re.compile(r"^\s*#\+(\w+):\s*(.*)$", re.MULTILINE)
_PROPERTIES_BLOCK = re.compile(r":PROPERTIES:\s*\n(.*?):END:", re.DOTALL)
_PROPERTY_LINE = re.compile(r"^\s*:([A-Za-z_-]+):\s+(.+)$", re.MULTILINE)
_HEADING_RE = re.compile(r"^(\*+)\s+(.+)$", re.MULTILINE)
_STANDARD_RE = re.compile(
    r"((?:ASTM|ISO|ANSI|IBC|IEC|ASCE)\s*[A-Z]?\d[\w.-]*)",
    re.IGNORECASE,
)


def parse_org_note(path: Path) -> ProjectContext:
    """Read an org-roam note and extract project context.

    Extracts:
    - #+title as project title
    - #+filetags or :tags: as metadata tags
    - Properties (CLIENT, PROJECT_ID, BUDGET, etc.)
    - Standards references (ASTM/ISO/ANSI/IBC patterns in body text)
    - Headings as scope items
    - Body text as description and supplementary context

    Args:
        path: Path to an .org file.

    Returns:
        ProjectContext populated from the note.
    """
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Org note not found: {path}")

    raw = path.read_text(encoding="utf-8", errors="replace")

    title, filetags, properties, body = _parse_file(raw)
    client = properties.get("CLIENT", properties.get("client", ""))
    project_id = _safe_int(properties.get("PROJECT_ID", properties.get("project_id", "")))
    budget = properties.get("BUDGET", properties.get("budget"))
    status = properties.get("STATUS", properties.get("status", "active"))

    scope = _extract_scope(body)
    standards = _extract_standards(raw)
    description = _extract_description(body)
    metadata: dict = {}

    tech_level = properties.get("TECHNICAL_LEVEL", properties.get("technical_level"))
    if tech_level:
        metadata["client_technical_level"] = tech_level.lower()

    if filetags:
        metadata["filetags"] = filetags

    metadata["org_note_path"] = str(path)
    metadata["org_note_body"] = body[:40000]

    return ProjectContext(
        project=Project(
            id=project_id or 0,
            title=title or path.stem.replace("-", " ").replace("_", " ").title(),
            client_name=client or "Unknown Client",
            status=status,
            budget=budget,
            description=description,
        ),
        scope=scope,
        standards=standards,
        metadata=metadata,
    )


def _parse_file(raw: str) -> tuple[str, list[str], dict[str, str], str]:
    """Extract title, filetags, properties, and body from raw org text."""
    title = ""
    filetags: list[str] = []

    for m in _KEYWORD_RE.finditer(raw):
        kw = m.group(1).lower()
        val = m.group(2).strip()
        if kw == "title":
            title = val
        elif kw == "filetags":
            filetags = [t.strip() for t in val.split(":") if t.strip()]

    properties: dict[str, str] = {}
    for block in _PROPERTIES_BLOCK.finditer(raw):
        for m in _PROPERTY_LINE.finditer(block.group(1)):
            properties[m.group(1)] = m.group(2).strip()

    cleaned = _KEYWORD_RE.sub("", raw)
    cleaned = _PROPERTIES_BLOCK.sub("", cleaned)

    return title, filetags, properties, cleaned.strip()


def _extract_scope(body: str) -> list[str]:
    """Extract top-level heading text as scope items."""
    scope: list[str] = []
    for m in _HEADING_RE.finditer(body):
        level = len(m.group(1))
        text = m.group(2).strip()
        text = re.sub(r"\s+:([\w:]+):$", "", text)
        if level <= 2 and text:
            scope.append(text)
    return scope


def _extract_standards(raw: str) -> list[Standard]:
    """Find standard references (ASTM E336-17a, ISO 717-1, etc.) in the full text."""
    seen: set[str] = set()
    standards: list[Standard] = []
    for m in _STANDARD_RE.finditer(raw):
        ref = m.group(1).strip()
        if ref in seen:
            continue
        seen.add(ref)
        prefix = ref.split()[0].upper() if " " in ref else ref[:4].rstrip("0123456789").upper()
        standards.append(Standard(type=prefix, id=ref))
    return standards


def _extract_description(body: str) -> str:
    """Extract the first non-heading block of text as a project description."""
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("*"):
            if lines:
                break
            continue
        if stripped:
            lines.append(stripped)
    return " ".join(lines)[:2000] if lines else ""


def _safe_int(val: str) -> int | None:
    try:
        return int(val.strip())
    except (ValueError, AttributeError):
        return None
