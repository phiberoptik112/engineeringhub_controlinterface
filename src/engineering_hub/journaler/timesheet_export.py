"""Export a final monthly timesheet org file from a template."""

from __future__ import annotations

import re
import shlex
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from engineering_hub.capture.applicator import _expand_placeholders
from engineering_hub.journaler.org_writer import (
    _monthly_timesheet_slug,
    _org_timestamp,
    _timesheet_reference_path,
    monthly_timesheet_path_for_month,
    read_section_body,
)

_ENTRY_RE = re.compile(
    r"^- \[(\d{4}-\d{2}-\d{2})[^\]]*\]\s+([\d.]+)h\s+::\s+(.+)$",
    re.MULTILINE,
)
_PROJECT_HEADING_RE = re.compile(
    r"^\*\*\s+(.+?)(?:\s+:[\w:]+:)?\s*$",
    re.MULTILINE,
)

EXPORT_USAGE = (
    "Usage: `/timesheet export --month YYYY-MM --project \"<project>\"`\n"
    "   optional: `--project-id <id>`, `--template <path>`, `-o <output-path>`"
)


@dataclass(frozen=True)
class TimesheetExportRequest:
    """Parsed `/timesheet export` fields."""

    month: str
    project: str
    project_id: str | None = None
    template_path: Path | None = None
    output_path: Path | None = None


@dataclass(frozen=True)
class TimesheetLine:
    """One parsed hour entry."""

    date: str
    hours: float
    description: str


def default_timesheet_export_template_path() -> Path:
    """Return the shipped default monthly export template."""
    return (
        Path(__file__).resolve().parents[3] / "timesheet_templates" / "monthly.org"
    )


def parse_timesheet_export_command(raw: str) -> TimesheetExportRequest:
    """Parse a ``/timesheet export`` command."""
    line = raw.strip()
    if not line.lower().startswith("/timesheet"):
        raise ValueError(EXPORT_USAGE)

    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        raise ValueError(f"{exc}\n\n{EXPORT_USAGE}") from exc

    if len(tokens) < 2 or tokens[0].lower() != "/timesheet":
        raise ValueError(EXPORT_USAGE)
    if tokens[1].lower() != "export":
        raise ValueError(EXPORT_USAGE)

    month = ""
    project = ""
    project_id: str | None = None
    template_path: Path | None = None
    output_path: Path | None = None
    i = 2

    while i < len(tokens):
        token = tokens[i]
        lower = token.lower()

        if lower == "--month" and i + 1 < len(tokens):
            month = tokens[i + 1]
            i += 2
            continue
        if lower.startswith("--month="):
            month = token.split("=", 1)[1]
            i += 1
            continue
        if lower in ("--project", "-p") and i + 1 < len(tokens):
            project = tokens[i + 1]
            i += 2
            continue
        if lower.startswith("--project="):
            project = token.split("=", 1)[1]
            i += 1
            continue
        if lower == "--project-id" and i + 1 < len(tokens):
            project_id = tokens[i + 1]
            i += 2
            continue
        if lower.startswith("--project-id="):
            project_id = token.split("=", 1)[1]
            i += 1
            continue
        if lower == "--template" and i + 1 < len(tokens):
            template_path = Path(tokens[i + 1]).expanduser()
            i += 2
            continue
        if lower.startswith("--template="):
            template_path = Path(token.split("=", 1)[1]).expanduser()
            i += 1
            continue
        if lower in ("-o", "--output") and i + 1 < len(tokens):
            output_path = Path(tokens[i + 1]).expanduser()
            i += 2
            continue
        if lower.startswith("--output="):
            output_path = Path(token.split("=", 1)[1]).expanduser()
            i += 1
            continue

        raise ValueError(f"Unknown argument: {token}\n\n{EXPORT_USAGE}")

    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError(f"Month must be YYYY-MM.\n\n{EXPORT_USAGE}")
    if not project:
        raise ValueError(f"Project is required.\n\n{EXPORT_USAGE}")

    if project_id is None and project.isdigit():
        project_id = project
        project = f"Project {project}"

    return TimesheetExportRequest(
        month=month,
        project=project.strip(),
        project_id=project_id.strip() if project_id else None,
        template_path=template_path,
        output_path=output_path,
    )


def _parse_entries(text: str, month: str) -> list[TimesheetLine]:
    entries: list[TimesheetLine] = []
    for match in _ENTRY_RE.finditer(text):
        date, hours_text, description = match.groups()
        if not date.startswith(month):
            continue
        entries.append(
            TimesheetLine(
                date=date,
                hours=float(hours_text),
                description=description.strip(),
            )
        )
    return entries


def _project_heading_matches(heading: str, project: str, project_id: str | None) -> bool:
    heading_clean = heading.strip()
    if project_id and f"django://project/{project_id}" in heading_clean:
        return True
    if project.lower() in heading_clean.lower():
        return True
    link_match = re.search(r"\[\[django://project/\d+\]\[(.+?)\]\]", heading_clean)
    if link_match and link_match.group(1).lower() == project.lower():
        return True
    return heading_clean.lower() == project.lower()


def _entries_from_reference(
    reference_path: Path,
    month: str,
    project: str,
    project_id: str | None,
) -> list[TimesheetLine]:
    if not reference_path.is_file():
        return []

    raw = reference_path.read_text(encoding="utf-8")
    timesheet_match = re.search(r"^\*+\s+Timesheet\s*$", raw, re.MULTILINE)
    if not timesheet_match:
        return []

    body = raw[timesheet_match.end() :]
    entries: list[TimesheetLine] = []
    current_heading = ""

    for line in body.splitlines():
        heading_match = _PROJECT_HEADING_RE.match(line)
        if heading_match:
            current_heading = heading_match.group(1).strip()
            continue
        if not current_heading:
            continue
        if not _project_heading_matches(current_heading, project, project_id):
            continue
        entry_match = _ENTRY_RE.match(line)
        if entry_match:
            date, hours_text, description = entry_match.groups()
            if date.startswith(month):
                entries.append(
                    TimesheetLine(
                        date=date,
                        hours=float(hours_text),
                        description=description.strip(),
                    )
                )
    return entries


def collect_month_entries(
    journal_dir: Path,
    month: str,
    project: str,
    project_id: str | None,
) -> list[TimesheetLine]:
    """Collect hour lines for a project/month from working note and reference."""
    monthly_path = monthly_timesheet_path_for_month(
        journal_dir, month, project_id, project
    )

    entries: list[TimesheetLine] = []
    if monthly_path.is_file():
        hours_body = read_section_body(monthly_path, "Hours")
        entries.extend(_parse_entries(hours_body, month))

    if not entries:
        reference_path = _timesheet_reference_path(journal_dir)
        entries.extend(
            _entries_from_reference(reference_path, month, project, project_id)
        )

    entries.sort(key=lambda item: (item.date, item.description))
    return entries


def _format_entry_lines(entries: list[TimesheetLine]) -> str:
    if not entries:
        return "(no entries for this month)"
    return "\n".join(
        f"- [{item.date}] {item.hours:.2f}h :: {item.description}"
        for item in entries
    )


def _format_entry_table(entries: list[TimesheetLine]) -> str:
    if not entries:
        return "| Date | Hours | Description |\n|------|-------|-------------|\n| | | |"
    lines = [
        "| Date | Hours | Description |",
        "|------|-------|-------------|",
    ]
    for item in entries:
        desc = item.description.replace("|", "\\|")
        lines.append(f"| {item.date} | {item.hours:.2f} | {desc} |")
    return "\n".join(lines)


def export_monthly_timesheet(
    journal_dir: Path,
    request: TimesheetExportRequest,
    template_path: Path | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Fill the export template and write the final monthly timesheet org file."""
    journal_dir = journal_dir.expanduser().resolve()
    now = now or datetime.now()
    template = (request.template_path or template_path or default_timesheet_export_template_path()).expanduser()
    if not template.is_file():
        return False, f"Template not found: {template}"

    entries = collect_month_entries(
        journal_dir,
        request.month,
        request.project,
        request.project_id,
    )
    total_hours = sum(item.hours for item in entries)

    slug = _monthly_timesheet_slug(request.project_id, request.project)
    monthly_path = monthly_timesheet_path_for_month(
        journal_dir,
        request.month,
        request.project_id,
        request.project,
    )
    notes = read_section_body(monthly_path, "Notes") if monthly_path.is_file() else ""
    review = read_section_body(monthly_path, "Review") if monthly_path.is_file() else ""

    values = {
        "export_id": str(uuid.uuid4()),
        "month": request.month,
        "project": request.project,
        "project_id": request.project_id or "",
        "project_tag": f"project_{request.project_id}" if request.project_id else f"project_{slug}",
        "client": request.project,
        "total_hours": f"{total_hours:.2f}",
        "entry_lines": _format_entry_lines(entries),
        "entry_table": _format_entry_table(entries),
        "notes": notes or "",
        "review": review or "",
        "created": _org_timestamp(now),
    }

    filled = _expand_placeholders(template.read_text(encoding="utf-8"), values)

    if request.output_path is not None:
        output_path = request.output_path.expanduser().resolve()
    else:
        exports_dir = journal_dir.parent / "timesheets" / "exports"
        output_path = exports_dir / f"{request.month}-{slug}-final.org"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_path.write_text(filled, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write export: {exc}"

    return (
        True,
        f"Exported {request.month} timesheet for {request.project} "
        f"({total_hours:.2f}h, {len(entries)} entries) to {output_path}",
    )


def handle_timesheet_export_command(
    raw: str,
    journal_dir: Path,
    template_path: Path | None = None,
) -> str:
    """Handle ``/timesheet export`` and return a status message."""
    try:
        request = parse_timesheet_export_command(raw)
    except ValueError as exc:
        return str(exc)

    ok, msg = export_monthly_timesheet(
        journal_dir=journal_dir,
        request=request,
        template_path=template_path,
    )
    return msg if ok else f"Could not export timesheet: {msg}"
