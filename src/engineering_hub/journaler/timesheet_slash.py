"""Handle Journaler ``/timesheet`` commands."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from engineering_hub.journaler.org_writer import append_timesheet_entry

USAGE = (
    "Usage: `/timesheet <hours> project \"<project>\" :: <description>`\n"
    "   or: `/timesheet <hours> --project \"<project>\" --desc \"<description>\"`\n"
    "   optional: `--project-id <id>` to link a named project to Django."
)


@dataclass(frozen=True)
class TimesheetEntry:
    """Parsed timesheet command fields."""

    hours: float
    project: str
    description: str
    project_id: str | None = None


def parse_timesheet_slash_command(raw: str) -> TimesheetEntry:
    """Parse a ``/timesheet`` command into structured fields."""
    line = raw.strip()
    if not line.lower().startswith("/timesheet"):
        raise ValueError(USAGE)

    left, separator, right = line.partition("::")
    try:
        tokens = shlex.split(left)
    except ValueError as exc:
        raise ValueError(f"{exc}\n\n{USAGE}") from exc

    if len(tokens) < 2 or tokens[0].lower() != "/timesheet":
        raise ValueError(USAGE)

    try:
        hours = float(tokens[1])
    except ValueError as exc:
        raise ValueError(f"Hours must be a number.\n\n{USAGE}") from exc
    if hours <= 0:
        raise ValueError(f"Hours must be greater than 0.\n\n{USAGE}")

    args = tokens[2:]
    project, project_id, flag_desc = _parse_project_args(args)
    description = right.strip() if separator else flag_desc

    if not project:
        raise ValueError(f"Project is required.\n\n{USAGE}")
    if not description:
        raise ValueError(f"Description is required.\n\n{USAGE}")

    if project_id is None and project.isdigit():
        project_id = project
        project = f"Project {project}"

    return TimesheetEntry(
        hours=hours,
        project=project,
        description=description,
        project_id=project_id,
    )


def handle_timesheet_slash_command(raw: str, journal_dir: Path) -> str:
    """Handle a ``/timesheet`` command and append it to today's journal."""
    try:
        entry = parse_timesheet_slash_command(raw)
    except ValueError as exc:
        return str(exc)

    ok, msg = append_timesheet_entry(
        journal_dir=journal_dir,
        project=entry.project,
        hours=entry.hours,
        description=entry.description,
        project_id=entry.project_id,
    )
    return msg if ok else f"Could not log timesheet entry: {msg}"


def _parse_project_args(args: list[str]) -> tuple[str, str | None, str]:
    project = ""
    project_id: str | None = None
    description = ""
    positional: list[str] = []
    i = 0

    while i < len(args):
        token = args[i]
        lower = token.lower()

        if lower == "project" and i + 1 < len(args):
            project = args[i + 1]
            i += 2
            continue
        if lower in ("--project", "-p") and i + 1 < len(args):
            project = args[i + 1]
            i += 2
            continue
        if lower.startswith("--project="):
            project = token.split("=", 1)[1]
            i += 1
            continue
        if lower == "--project-id" and i + 1 < len(args):
            project_id = args[i + 1]
            i += 2
            continue
        if lower.startswith("--project-id="):
            project_id = token.split("=", 1)[1]
            i += 1
            continue
        if lower in ("--desc", "--description") and i + 1 < len(args):
            description = " ".join(args[i + 1 :]).strip()
            break
        if lower.startswith("--desc=") or lower.startswith("--description="):
            description = token.split("=", 1)[1]
            if i + 1 < len(args):
                description = " ".join([description, *args[i + 1 :]]).strip()
            break

        positional.append(token)
        i += 1

    if not project and positional:
        project = " ".join(positional).strip()

    return project.strip(), project_id.strip() if project_id else None, description.strip()
