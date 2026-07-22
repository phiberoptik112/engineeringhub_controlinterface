"""CLI entry for context-pipeline diagnostic runs."""

from __future__ import annotations

import logging
from pathlib import Path

from engineering_hub.config.settings import Settings
from engineering_hub.core.models import TaskResult
from engineering_hub.diagnostics.artifacts import (
    load_tasks_from_yaml,
    new_diagnostic_run_id,
    persist_task_diagnostic_bundle,
    write_summary,
    write_task_result_artifacts,
)
from engineering_hub.orchestration.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


def run_context_pipeline_diagnostic(
    settings: Settings,
    tasks_path: Path,
    *,
    max_tasks: int = 10,
    dry_run_context_only: bool = False,
    run_id: str | None = None,
) -> tuple[int, Path]:
    """Execute diagnostic tasks; return (exit_code, run_root)."""
    run_id = run_id or new_diagnostic_run_id()
    run_root = settings.output_dir / "diagnostics" / "context-pipeline" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    tasks = load_tasks_from_yaml(tasks_path)[:max_tasks]
    if not tasks:
        logger.warning("No tasks in %s", tasks_path)
        write_summary(run_root, run_id, [], {})
        return 0, run_root

    orchestrator = Orchestrator(settings)
    task_summaries: list[dict] = []
    fail_count = 0

    for i, task in enumerate(tasks):
        formatted = orchestrator.context_manager.format_for_agent(task)
        task_dir, checklist_payload = persist_task_diagnostic_bundle(
            run_root, i, task, formatted, settings
        )

        if dry_run_context_only:
            task_summaries.append(
                {
                    "task_index": i,
                    "task_id": task.task_id,
                    "agent": task.agent,
                    "description": task.description,
                    "project_id": task.project_id,
                    "task_dir": str(task_dir),
                    "dry_run": True,
                    "success": True,
                    "checklist": checklist_payload["context_delivered"],
                    "failure_mode": None,
                    "evaluator_result_path": None,
                }
            )
            continue

        if task.input_paths:
            missing = []
            for path_str in task.input_paths:
                resolved = orchestrator.context_manager._resolve_input_path(path_str)
                if resolved is None or not resolved.is_file():
                    missing.append(path_str)
            if missing:
                err = (
                    f"Input file(s) not found: {', '.join(missing)}. "
                    f"Place them under {orchestrator.context_manager.inputs_dir}."
                )
                result = TaskResult(
                    task=task,
                    success=False,
                    error_message=err,
                )
                write_task_result_artifacts(task_dir, result)
                fail_count += 1
                task_summaries.append(
                    {
                        "task_index": i,
                        "task_id": task.task_id,
                        "agent": task.agent,
                        "description": task.description,
                        "project_id": task.project_id,
                        "task_dir": str(task_dir),
                        "dry_run": False,
                        "success": False,
                        "checklist": checklist_payload["context_delivered"],
                        "failure_mode": None,
                        "evaluator_result_path": None,
                    }
                )
                continue

        result = orchestrator.task_router.execute(task, formatted)
        write_task_result_artifacts(task_dir, result)
        if not result.success:
            fail_count += 1

        task_summaries.append(
            {
                "task_index": i,
                "task_id": task.task_id,
                "agent": task.agent,
                "description": task.description,
                "project_id": task.project_id,
                "task_dir": str(task_dir),
                "dry_run": False,
                "success": result.success,
                "checklist": checklist_payload["context_delivered"],
                "failure_mode": None,
                "evaluator_result_path": None,
            }
        )

    write_summary(run_root, run_id, task_summaries, {})
    return (1 if fail_count else 0), run_root
