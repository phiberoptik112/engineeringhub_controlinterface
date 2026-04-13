"""Execute a capture template: prompt for fields, expand placeholders, create org nodes.

Works in both interactive (readline-based prompting) and non-interactive
(pre-supplied field values) modes.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from engineering_hub.capture.models import (
    CaptureTemplate,
    DispatchTrigger,
    FieldSpec,
    FieldType,
    HeadingSpec,
)


def _expand_placeholders(text: str, values: dict[str, str]) -> str:
    """Replace ``${name}`` placeholders with values from the dict."""
    def _replacer(m: re.Match) -> str:
        key = m.group(1)
        return values.get(key, m.group(0))
    return re.sub(r"\$\{(\w+)\}", _replacer, text)


def _expand_date_patterns(text: str, now: datetime | None = None) -> str:
    """Expand strftime-style ``%Y``, ``%m``, ``%d``, etc. in text."""
    if now is None:
        now = datetime.now()
    try:
        return now.strftime(text)
    except (ValueError, TypeError):
        return text


def _slugify(text: str, max_len: int = 60) -> str:
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in text.lower()).strip("-")
    slug = "-".join(filter(None, slug.split("-")))
    return slug[:max_len]


def prompt_for_fields(
    fields: list[FieldSpec],
    preset_values: dict[str, str] | None = None,
) -> dict[str, str]:
    """Interactively prompt the user for each field, returning a name→value dict.

    Fields with values already in *preset_values* are skipped.
    """
    values: dict[str, str] = dict(preset_values or {})
    now = datetime.now()

    for field in fields:
        if field.name in values:
            continue

        default = field.default
        if default == "today":
            default = now.strftime("%Y-%m-%d")

        if field.type == FieldType.CHOICE and field.choices:
            print(f"\n{field.prompt}:")
            for i, choice in enumerate(field.choices, 1):
                marker = " *" if choice == default else ""
                print(f"  {i}. {choice}{marker}")
            suffix = f" [{default}]" if default else ""
            answer = input(f"  Choice{suffix}: ").strip()
            if not answer and default:
                values[field.name] = default
            elif answer.isdigit() and 1 <= int(answer) <= len(field.choices):
                values[field.name] = field.choices[int(answer) - 1]
            else:
                values[field.name] = answer or default
        else:
            suffix = f" [{default}]" if default else ""
            answer = input(f"{field.prompt}{suffix}: ").strip()
            values[field.name] = answer if answer else default

    return values


def _build_heading_body(heading: HeadingSpec, values: dict[str, str]) -> str:
    """Recursively build org heading text."""
    stars = "*" * heading.level
    lines: list[str] = [f"{stars} {_expand_placeholders(heading.title, values)}"]

    if heading.body:
        lines.append(_expand_placeholders(heading.body, values))

    for child in heading.children:
        lines.append("")
        lines.append(_build_heading_body(child, values))

    return "\n".join(lines)


def apply_template(
    template: CaptureTemplate,
    roam_dir: Path,
    values: dict[str, str],
    journal_dir: Path | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Apply a capture template with the given field values.

    Creates an org-roam node file and optionally dispatches an agent task
    to today's journal.

    Args:
        template: The capture template to apply.
        roam_dir: Root org-roam directory.
        values: Field name → value mapping (from :func:`prompt_for_fields`).
        journal_dir: Daily journal directory (required for agent dispatch).
        now: Override current time for testing.

    Returns:
        ``(ok, message)`` — message includes the created file path on success.
    """
    if now is None:
        now = datetime.now()

    # Expand title
    title = _expand_placeholders(template.title_pattern, values)
    title = _expand_date_patterns(title, now)
    slug = _slugify(title)

    # Build filename
    filename = _expand_placeholders(template.filename_pattern, values)
    filename = _expand_date_patterns(filename, now)
    filename = filename.replace("${slug}", slug)

    # Resolve target directory
    target_base = roam_dir
    if template.target_dir:
        target_base = roam_dir / template.target_dir.strip("/")

    target_base.mkdir(parents=True, exist_ok=True)
    file_path = target_base / filename

    if file_path.exists():
        file_path = target_base / f"{now.strftime('%Y%m%d%H%M%S')}-{slug}.org"

    # Build file content
    node_id = str(uuid.uuid4())
    created_ts = now.strftime(f"[%Y-%m-%d {now.strftime('%a')} %H:%M]")

    lines: list[str] = [":PROPERTIES:", f":ID:       {node_id}"]

    for prop_key, prop_val in template.properties.items():
        expanded = _expand_placeholders(prop_val, values)
        lines.append(f":{prop_key}:    {expanded}")

    lines.append(":END:")
    lines.append(f"#+title: {title}")

    if template.filetags:
        inner = ":".join(template.filetags)
        lines.append(f"#+filetags: :{inner}:")

    lines.append(f"#+created: {created_ts}")
    lines.append("")

    # Add headings
    if template.headings:
        for heading in template.headings:
            lines.append(_build_heading_body(heading, values))
            lines.append("")
    elif template.raw_body:
        expanded_body = _expand_placeholders(template.raw_body, values)
        lines.append(expanded_body)
        lines.append("")

    content = "\n".join(lines)

    try:
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write {file_path.name}: {exc}"

    result_msg = f"Created: {file_path}"

    # Agent dispatch
    if (
        template.agent_dispatch
        and template.agent_dispatch.on == DispatchTrigger.ON_CAPTURE
        and journal_dir
    ):
        from engineering_hub.journaler.org_writer import add_todo_to_journal

        desc = _expand_placeholders(
            template.agent_dispatch.description_template, values
        )
        agent = template.agent_dispatch.agent_type
        task_desc = f"@{agent}: {desc}"

        ok, msg = add_todo_to_journal(journal_dir, task_desc)
        if ok:
            result_msg += f"\nAgent task queued: {task_desc}"
        else:
            result_msg += f"\nWarning: could not queue agent task: {msg}"

    return True, result_msg
