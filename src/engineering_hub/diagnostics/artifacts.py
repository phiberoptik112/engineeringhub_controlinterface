"""Persist context pipeline diagnostic artifacts (no orchestrator import)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from engineering_hub.config.settings import Settings
from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import ParsedTask, TaskResult
from engineering_hub.diagnostics.context_checklist import (
    analyze_formatted_context,
    checklist_for_template,
)


def new_diagnostic_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def _safe_task_dir_name(task: ParsedTask, index: int) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", task.description[:40]).strip("_") or "task"
    return f"{index:02d}_{slug}"


def extract_audit_lines_for_task(audit_path: Path | None, task_id: str) -> str:
    """Return JSONL lines from the corpus audit log matching ``task_id``."""
    if audit_path is None or not audit_path.is_file():
        return ""
    lines_out: list[str] = []
    try:
        text = audit_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"# failed to read audit log: {exc}\n"
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("task_id") == task_id:
            lines_out.append(json.dumps(obj, ensure_ascii=False))
    return "\n".join(lines_out) + ("\n" if lines_out else "")


def write_task_json(task_dir: Path, task: ParsedTask) -> None:
    payload = task.model_dump(mode="json")
    (task_dir / "task.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_formatted_context(task_dir: Path, formatted: str) -> None:
    (task_dir / "formatted_context.txt").write_text(formatted, encoding="utf-8")


def write_checklist(task_dir: Path, formatted: str) -> dict[str, Any]:
    analysis = analyze_formatted_context(formatted)
    checklist = checklist_for_template(analysis)
    (task_dir / "checklist.json").write_text(
        json.dumps(
            {"analysis": analysis, "context_delivered": checklist},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"analysis": analysis, "context_delivered": checklist}


def write_corpus_audit_excerpt(
    task_dir: Path,
    settings: Settings,
    task_id: str,
) -> None:
    audit_path = settings.corpus_audit_log_path
    body = extract_audit_lines_for_task(
        audit_path.expanduser() if audit_path else None,
        task_id,
    )
    path = task_dir / "corpus_audit_excerpt.jsonl"
    if body.strip():
        path.write_text(body, encoding="utf-8")
    elif audit_path and audit_path.expanduser().is_file():
        path.write_text(
            f"# no entries for task_id={task_id!r} in {audit_path}\n",
            encoding="utf-8",
        )


def write_task_result_artifacts(task_dir: Path, result: TaskResult) -> None:
    out: dict[str, Any] = {
        "success": result.success,
        "error_message": result.error_message,
        "output_path": result.output_path,
    }
    (task_dir / "result.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if result.agent_response:
        (task_dir / "agent_response.md").write_text(
            result.agent_response,
            encoding="utf-8",
        )


def load_tasks_from_yaml(path: Path) -> list[ParsedTask]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("tasks")
    if not isinstance(rows, list):
        raise ValueError("YAML must contain a top-level 'tasks' list")

    tasks: list[ParsedTask] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"tasks[{i}] must be a mapping")
        agent = str(row["agent"])
        description = str(row["description"])
        raw_pid = row.get("project_id")
        if raw_pid in (None, "", "none", "null"):
            project_id = None
        else:
            project_id = int(raw_pid) if isinstance(raw_pid, (int, float)) else raw_pid

        tasks.append(
            ParsedTask(
                agent=agent,
                status=TaskStatus.PENDING,
                project_id=project_id,
                description=description,
                context=row.get("context"),
                deliverable=row.get("deliverable"),
                input_paths=list(row.get("input_paths") or []),
                start_line=i + 1,
                end_line=i + 1,
                raw_block=row.get("raw_block") or f"synthetic:{i}",
                journal_date=row.get("journal_date"),
                category=row.get("category"),
                source_path=row.get("source_path"),
            )
        )
    return tasks


def write_summary(
    run_root: Path,
    run_id: str,
    task_summaries: list[dict[str, Any]],
    failure_mode_counts: dict[str, int] | None = None,
) -> None:
    summary = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tasks": task_summaries,
        "failure_mode_counts": failure_mode_counts or {},
    }
    (run_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def diagnostic_task_dir(run_root: Path, task: ParsedTask, index: int) -> Path:
    return run_root / _safe_task_dir_name(task, index)


def persist_task_diagnostic_bundle(
    run_root: Path,
    index: int,
    task: ParsedTask,
    formatted: str,
    settings: Settings,
) -> tuple[Path, dict[str, Any]]:
    """Write context + checklist + audit excerpt for one task. Returns task_dir and checklist payload."""
    task_dir = diagnostic_task_dir(run_root, task, index)
    task_dir.mkdir(parents=True, exist_ok=True)
    write_task_json(task_dir, task)
    write_formatted_context(task_dir, formatted)
    checklist_payload = write_checklist(task_dir, formatted)
    write_corpus_audit_excerpt(task_dir, settings, task.task_id)
    return task_dir, checklist_payload
