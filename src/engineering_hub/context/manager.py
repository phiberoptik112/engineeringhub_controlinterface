"""Context manager for building agent context."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from engineering_hub.core.constants import AgentType
from engineering_hub.core.models import (
    FileInfo,
    ParsedTask,
    Project,
    ProjectContext,
    Standard,
)
from engineering_hub.context.formatters import ContextFormatter
from engineering_hub.django.client import DjangoClient
from engineering_hub.notes.manager import SharedNotesManager

if TYPE_CHECKING:
    from engineering_hub.memory.service import MemoryService

logger = logging.getLogger(__name__)


class ContextManager:
    """Manages building context for agent tasks."""

    def __init__(
        self,
        django_client: DjangoClient,
        notes_manager: SharedNotesManager,
        output_dir: Path | None = None,
        workspace_dir: Path | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        """Initialize context manager.

        Args:
            django_client: Client for Django API
            notes_manager: Manager for shared notes
            output_dir: Base output directory (for staging manifest lookup)
            workspace_dir: Workspace directory (for resolving task file paths)
            memory_service: Optional memory service for semantic context enrichment
        """
        self.django_client = django_client
        self.notes_manager = notes_manager
        self.output_dir = Path(output_dir) if output_dir else Path("outputs")
        self.workspace_dir = Path(workspace_dir) if workspace_dir else Path.cwd()
        self.memory_service = memory_service

    def build_context(self, task: ParsedTask) -> ProjectContext:
        """Build project context for a task.

        Args:
            task: The task to build context for

        Returns:
            ProjectContext with all available data
        """
        if task.project_id is None:
            logger.warning(f"Task has no project ID, using minimal context")
            context = self._build_minimal_context()
            context = self._enrich_with_task_file_refs(context, task)
            context = self._enrich_with_memory(context, task)
            return context

        try:
            # Get context from Django API
            django_context = self.django_client.get_project_context(task.project_id)

            # Convert Django response to our models
            context = ProjectContext(
                project=Project(
                    id=django_context.project.id,
                    title=django_context.project.title,
                    client_name=django_context.project.client_name,
                    status=django_context.project.status,
                    budget=django_context.project.budget,
                    description=django_context.project.description,
                    start_date=django_context.project.start_date,
                    end_date=django_context.project.end_date,
                ),
                scope=django_context.scope,
                standards=[
                    Standard(type=s.type, id=s.id) for s in django_context.standards
                ],
                recent_files=[
                    FileInfo(
                        id=f.id,
                        title=f.title,
                        file_type=f.file_type,
                        url=f.url,
                        created_at=f.created_at,
                    )
                    for f in django_context.recent_files
                ],
                proposals=[p.model_dump() for p in django_context.proposals],
                metadata=django_context.metadata,
            )

            # Enrich with historical context from notes
            context = self._enrich_with_notes_context(context, task.project_id)

            # Enrich with staged files from ingest
            context = self._enrich_with_staged_files(context, task.project_id)

            # Enrich with task file refs (.tex, etc.)
            context = self._enrich_with_task_file_refs(context, task)

            # Enrich with semantic memory from past tasks
            context = self._enrich_with_memory(context, task)

            return context

        except Exception as e:
            logger.error(f"Failed to fetch Django context: {e}")
            return self._build_minimal_context()

    def _build_minimal_context(self) -> ProjectContext:
        """Build minimal context when Django data is unavailable."""
        return ProjectContext(
            project=Project(
                id=0,
                title="Unknown Project",
                client_name="Unknown Client",
                status="unknown",
            ),
        )

    def _enrich_with_notes_context(
        self,
        context: ProjectContext,
        project_id: int,
    ) -> ProjectContext:
        """Enrich context with historical data from shared notes.

        This adds information about previous tasks, decisions, and
        research findings for the same project.
        """
        try:
            # Get all tasks for this project
            all_tasks = self.notes_manager.get_all_tasks()
            project_tasks = [t for t in all_tasks if t.project_id == project_id]

            # Add task history to metadata
            completed_tasks = [
                {"agent": t.agent, "description": t.description}
                for t in project_tasks
                if t.status.value == "COMPLETED"
            ]

            if completed_tasks:
                context.metadata["completed_tasks"] = completed_tasks

            # Could also extract communication thread entries for this project
            # This would require parsing the thread section - future enhancement

        except Exception as e:
            logger.warning(f"Failed to enrich context from notes: {e}")

        return context

    def _enrich_with_staged_files(
        self,
        context: ProjectContext,
        project_id: int,
    ) -> ProjectContext:
        """Enrich context with staged markdown files from ingest."""
        try:
            staging_dir = self.output_dir / "staging" / f"project-{project_id}"
            manifest_path = staging_dir / "manifest.json"
            if not manifest_path.exists():
                return context

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest.get("files", [])
            staged = [
                {
                    "original_name": f.get("original_name", ""),
                    "staged_path": f.get("staged_path", ""),
                    "sections": f.get("sections", []),
                }
                for f in files
                if "error" not in f
            ]
            if staged:
                context.metadata["staged_source_files"] = staged
        except Exception as e:
            logger.warning(f"Failed to enrich with staged files: {e}")
        return context

    def _enrich_with_task_file_refs(
        self,
        context: ProjectContext,
        task: ParsedTask,
    ) -> ProjectContext:
        """Enrich context with contents of files referenced in task (e.g. .tex)."""
        if not task.input_paths:
            return context

        contents: list[dict] = []
        for path_str in task.input_paths:
            try:
                resolved = Path(path_str).expanduser()
                if not resolved.is_absolute():
                    resolved = self.workspace_dir / resolved
                resolved = resolved.resolve()
                if resolved.exists() and resolved.is_file():
                    text = resolved.read_text(encoding="utf-8", errors="replace")
                    contents.append({
                        "path": str(resolved),
                        "name": resolved.name,
                        "content": text,
                    })
            except Exception as e:
                logger.warning(f"Failed to read task file {path_str}: {e}")

        if contents:
            context.metadata["task_file_contents"] = contents
        return context

    def _enrich_with_memory(
        self,
        context: ProjectContext,
        task: ParsedTask,
    ) -> ProjectContext:
        """
        Run a semantic search using the task description as the query and inject
        the top results into context.metadata for formatters to include.
        """
        if self.memory_service is None:
            return context

        query = task.description
        if task.context:
            query = f"{task.description}. {task.context}"

        results = self.memory_service.search(
            query=query,
            project_id=task.project_id,
        )

        # Broaden search across projects if we got fewer than 2 results
        if len(results) < 2 and task.project_id is not None:
            broader = self.memory_service.search(query=query, threshold=0.40)
            seen_ids = {r.id for r in results}
            for r in broader:
                if r.id not in seen_ids:
                    results.append(r)
            results = results[: self.memory_service.search_k]

        if results:
            context.metadata["memory_results"] = [
                {
                    "content": r.content,
                    "source": r.source,
                    "similarity": r.similarity,
                    "agent": r.agent,
                    "created_at": r.created_at,
                }
                for r in results
            ]
            context.metadata["memory_context_block"] = (
                self.memory_service.format_for_context(results)
            )
            logger.debug(
                f"Injected {len(results)} memory results "
                f"for: {task.description[:50]}"
            )

        return context

    def format_for_agent(self, task: ParsedTask) -> str:
        """Build and format context for a specific agent task.

        Args:
            task: The task to build context for

        Returns:
            Formatted context string ready for agent prompt
        """
        context = self.build_context(task)
        return ContextFormatter.format(context, task.agent_type)

    def get_output_path(self, task: ParsedTask, output_dir: Path) -> Path:
        """Determine the output path for a task's deliverable.

        Args:
            task: The task
            output_dir: Base output directory

        Returns:
            Full path where output should be written
        """
        if task.deliverable:
            # Use specified deliverable path
            deliverable_path = task.deliverable.lstrip("/")
            return output_dir / deliverable_path

        # Generate default path based on agent and project
        agent_dirs = {
            AgentType.RESEARCH: "research",
            AgentType.TECHNICAL_WRITER: "docs",
            AgentType.STANDARDS_CHECKER: "analysis",
            AgentType.REF_ENGINEER: "reviews",
            AgentType.EVALUATOR: "analysis",
        }

        agent_dir = agent_dirs.get(task.agent_type, "outputs")
        project_id = task.project_id or "unknown"

        # Create a filename from the task description
        desc_slug = task.description[:30].lower().replace(" ", "-")
        desc_slug = "".join(c for c in desc_slug if c.isalnum() or c == "-")

        filename = f"project-{project_id}-{desc_slug}.md"
        return output_dir / agent_dir / filename
