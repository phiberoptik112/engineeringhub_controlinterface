"""Tests for context pipeline diagnostics (checklist + artifact helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineering_hub.config.settings import Settings
from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import ParsedTask
from engineering_hub.diagnostics.artifacts import (
    diagnostic_task_dir,
    load_tasks_from_yaml,
    new_diagnostic_run_id,
    persist_task_diagnostic_bundle,
    write_summary,
)
from engineering_hub.diagnostics.context_checklist import (
    analyze_formatted_context,
    checklist_for_template,
)
from engineering_hub.diagnostics.runner import run_context_pipeline_diagnostic


def test_analyze_formatted_context_base_sections() -> None:
    text = """
## Project Context: Acme Office

### Project Overview
- **Client**: Acme Construction
- **Status**: in_progress

### Scope of Work
- STC field testing

### Standards & Requirements
- ASTM E336-17a (ASTM)

### Available Project Files
- Report (pdf)

### Referenced Documents

#### notes (md)
```
body
```

### Research Focus
more
""".strip()
    a = analyze_formatted_context(text)
    assert a["project_overview"] is True
    assert a["scope_of_work"] is True
    assert a["standards_list"] is True
    assert a["available_files_list"] is True
    assert a["task_referenced_documents"] is True
    assert a["memory_block"] is False
    assert a["corpus_block"] is False
    assert a["template_skeleton"] is False


def test_analyze_post_base_segments_memory_and_template() -> None:
    base = "## Project Context: P\n\n### Project Overview\n- **Client**: C\n- **Status**: s\n"
    memory = "### Relevant Past Context\n\n**Prior output**"
    template = "## Report Template: T\n\n### Required Sections\n- `## Intro`"
    formatted = base + "\n\n---\n\n" + memory + "\n\n---\n\n" + template
    a = analyze_formatted_context(formatted)
    assert a["memory_block"] is True
    assert a["template_skeleton"] is True
    assert a["corpus_block"] is False


def test_analyze_corpus_like_middle_segment() -> None:
    base = "## Project Context: P\n\n### Project Overview\n- **Client**: C\n- **Status**: x\n"
    corpus_only = "Chunk from PDF with page 12 and similarity scores"
    formatted = base + "\n\n---\n\n" + corpus_only
    a = analyze_formatted_context(formatted)
    assert a["corpus_block"] is True


def test_load_tasks_from_yaml(tmp_path: Path) -> None:
    p = tmp_path / "tasks.yaml"
    p.write_text(
        """
tasks:
  - agent: research
    description: Hello
    project_id: none
  - agent: technical-writer
    description: Second
    project_id: 2
""",
        encoding="utf-8",
    )
    tasks = load_tasks_from_yaml(p)
    assert len(tasks) == 2
    assert tasks[0].project_id is None
    assert tasks[0].agent == "research"
    assert tasks[1].project_id == 2


def test_persist_task_diagnostic_bundle(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "outputs").mkdir(parents=True)
    settings = Settings(workspace_dir=ws)
    run_root = ws / "outputs" / "diagnostics" / "context-pipeline" / "testrun"
    run_root.mkdir(parents=True)
    task = ParsedTask(
        agent="research",
        status=TaskStatus.PENDING,
        project_id=1,
        description="Do something",
        start_line=1,
        end_line=2,
        raw_block="x",
    )
    formatted = "## Project Context: X\n\n### Project Overview\n- **Client**: Y\n- **Status**: z\n"
    task_dir, _payload = persist_task_diagnostic_bundle(
        run_root, 0, task, formatted, settings
    )
    assert task_dir == diagnostic_task_dir(run_root, task, 0)
    assert (task_dir / "formatted_context.txt").read_text() == formatted
    assert (task_dir / "task.json").exists()
    assert (task_dir / "checklist.json").exists()
    import json

    chk = json.loads((task_dir / "checklist.json").read_text())
    assert "context_delivered" in chk


def test_write_summary_failure_mode_counts(tmp_path: Path) -> None:
    write_summary(
        tmp_path,
        "run-1",
        [{"task_index": 0, "task_dir": "/a"}],
        {"A": 1, "E": 2},
    )
    import json

    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["failure_mode_counts"]["A"] == 1


def test_run_context_pipeline_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Dry-run avoids LLM; patch Orchestrator to stub context_manager."""
    ws = tmp_path / "ws"
    (ws / "outputs").mkdir(parents=True)
    settings = Settings(workspace_dir=ws)

    tasks_file = tmp_path / "tasks.yaml"
    tasks_file.write_text(
        "tasks:\n  - agent: research\n    description: T\n    project_id: none\n",
        encoding="utf-8",
    )

    class FakeCM:
        def format_for_agent(self, task: ParsedTask) -> str:
            return f"CTX:{task.description}"

    class FakeOrchestrator:
        def __init__(self, s: Settings) -> None:
            self.context_manager = FakeCM()
            self.task_router = None  # unused in dry-run

    monkeypatch.setattr(
        "engineering_hub.diagnostics.runner.Orchestrator",
        FakeOrchestrator,
    )

    code, run_root = run_context_pipeline_diagnostic(
        settings,
        tasks_file,
        max_tasks=5,
        dry_run_context_only=True,
        run_id="fixed-id",
    )
    assert code == 0
    assert run_root.name == "fixed-id"
    task_dirs = sorted(p for p in run_root.iterdir() if p.is_dir())
    assert task_dirs
    txt = (task_dirs[0] / "formatted_context.txt").read_text()
    assert txt == "CTX:T"
    assert (run_root / "summary.json").exists()


def test_checklist_for_template_keys() -> None:
    a = analyze_formatted_context("## Project Context: P\n\n### Project Overview\n- **Client**: C\n- **Status**: s\n")
    c = checklist_for_template(a)
    assert set(c.keys()) == {
        "project_overview",
        "scope_of_work",
        "standards_list",
        "available_files_list",
        "task_referenced_document_contents",
        "memory_block",
        "corpus_block",
        "template_skeleton",
    }


def test_new_diagnostic_run_id_format() -> None:
    rid = new_diagnostic_run_id()
    assert "T" in rid and len(rid) > 10
