"""Main orchestrator that ties all components together."""

import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

from engineering_hub.actions.file_ingest import FileIngestAction
from engineering_hub.agents.backends import create_backend
from engineering_hub.agents.worker import AgentWorker
from engineering_hub.config.settings import Settings
from engineering_hub.context.manager import ContextManager
from engineering_hub.core.constants import TaskStatus, is_ingest_task
from engineering_hub.core.models import ParsedTask, TaskResult
from engineering_hub.corpus_service_factory import build_corpus_service_from_settings
from engineering_hub.django.client import DjangoClient
from engineering_hub.memory.service import MemoryService
from engineering_hub.notes.manager import SharedNotesManager
from engineering_hub.orchestration.dispatcher import TaskDispatcher
from engineering_hub.orchestration.watcher import FileWatcher

logger = logging.getLogger(__name__)


class Orchestrator:
    """Main orchestrator for the Engineering Hub.

    Coordinates file watching, task dispatching, and agent execution.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the orchestrator.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self._shutdown_event = threading.Event()

        # Initialize components
        self._init_components()

    def _init_components(self) -> None:
        """Initialize all orchestrator components."""
        # Org mode: watch the daily journal directory; otherwise watch the notes file.
        if self.settings.use_org_mode:
            notes_path = self.settings.org_journal_dir
        else:
            notes_path = self.settings.notes_file

        # Notes manager (org, journal, or legacy mode) — task dispatch window
        self.notes_manager = SharedNotesManager(
            notes_path,
            use_journal_mode=self.settings.use_journal_mode,
            journal_categories=self.settings.journal_categories,
            use_org_mode=self.settings.use_org_mode,
            org_task_sections=self.settings.org_task_sections,
            org_lookback_days=self.settings.org_lookback_days,
        )

        # Separate notes manager with a wider lookback for context enrichment
        self._history_notes_manager = SharedNotesManager(
            notes_path,
            use_journal_mode=self.settings.use_journal_mode,
            journal_categories=self.settings.journal_categories,
            use_org_mode=self.settings.use_org_mode,
            org_task_sections=self.settings.org_task_sections,
            org_lookback_days=self.settings.org_context_lookback_days,
        )

        # Django client
        self.django_client = DjangoClient(
            api_url=self.settings.django_api_url,
            api_token=self.settings.django_api_token.get_secret_value(),
            cache_ttl=self.settings.django_cache_ttl,
        )

        # Memory service (standalone, no Django dependency)
        self.memory_service: Optional[MemoryService] = self._init_memory_service()

        corpus_service = build_corpus_service_from_settings(self.settings)

        # Context manager
        self.context_manager = ContextManager(
            django_client=self.django_client,
            notes_manager=self.notes_manager,
            output_dir=self.settings.output_dir,
            workspace_dir=self.settings.workspace_dir,
            inputs_dir=self.settings.resolved_inputs_dir,
            memory_service=self.memory_service,
            history_notes_manager=self._history_notes_manager,
            corpus_service=corpus_service,
        )

        # Agent worker (provider chosen by llm_provider setting)
        backend = create_backend(self.settings)
        self.agent_worker = AgentWorker(
            backend=backend,
            prompts_dir=self.settings.prompts_dir,
            output_dir=self.settings.output_dir,
            max_tokens=self.settings.max_tokens,
        )

        # Task dispatcher
        self.dispatcher = TaskDispatcher(
            notes_manager=self.notes_manager,
            task_executor=self._execute_task,
        )

        # File watcher (created but not started)
        self.watcher = FileWatcher(
            notes_path=notes_path,
            callback=self._on_notes_changed,
            debounce_seconds=1.0,
            watch_dir=self.settings.use_org_mode,
        )

        # File ingest action
        self._ingest_action = FileIngestAction(
            output_dir=self.settings.output_dir,
            manifest_name=self.settings.staging_manifest_name,
        )

        self._capture_journal_entries()

    def _execute_task(self, task: ParsedTask) -> TaskResult:
        """Execute a task using the agent worker or an action.

        Args:
            task: The task to execute

        Returns:
            TaskResult with execution outcome
        """
        # Route ingest tasks to FileIngestAction before agent
        if is_ingest_task(task.description):
            return self._execute_ingest(task)

        # Pre-flight: verify all input files exist before calling the agent
        if task.input_paths:
            missing = []
            for path_str in task.input_paths:
                resolved = self.context_manager._resolve_input_path(path_str)
                if resolved is None or not resolved.is_file():
                    missing.append(path_str)
            if missing:
                missing_list = ", ".join(missing)
                error_msg = (
                    f"Input file(s) not found: {missing_list}. "
                    f"Place them under {self.context_manager.inputs_dir} "
                    f"or use an absolute path."
                )
                logger.error(error_msg)
                return TaskResult(
                    task=task,
                    success=False,
                    error_message=error_msg,
                )

        # Build context for the task
        context = self.context_manager.format_for_agent(task)

        # Execute with agent
        result = self.agent_worker.execute(task, context)

        if result.success:
            self._capture_task_result(task, result)
            self._create_roam_wrapper(task, result)

        return result

    def _execute_ingest(self, task: ParsedTask) -> TaskResult:
        """Execute file ingest action for a task, then chunk + embed into memory."""
        result = self._ingest_action.execute_from_description(
            description=task.description,
            project_id=task.project_id,
        )

        if result.success and self.memory_service and self.settings.chunk_enabled:
            self._embed_ingest_chunks(result.converted_docs, task.project_id)

        return TaskResult(
            task=task,
            success=result.success,
            output_path=result.manifest_path,
            error_message=result.error_message,
        )

    def _embed_ingest_chunks(
        self,
        converted_docs: dict,
        project_id: int | None,
    ) -> None:
        """Chunk converted documents and embed into memory."""
        from engineering_hub.memory.chunker import chunk_document

        for filename, (markdown, docling_doc) in converted_docs.items():
            try:
                chunks = chunk_document(markdown, filename, docling_doc)
                if chunks:
                    self.memory_service.capture_document(chunks, project_id=project_id)  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning(f"Failed to chunk/embed {filename} (non-fatal): {exc}")

    def _init_memory_service(self) -> Optional[MemoryService]:
        """Initialise memory service. Returns None if disabled or on error."""
        if not self.settings.memory_enabled:
            logger.info("Memory service disabled in config")
            return None

        try:
            return MemoryService.from_workspace(
                workspace_dir=self.settings.workspace_dir,
                ollama_host=self.settings.ollama_host,
                ollama_model=self.settings.ollama_embed_model,
                enabled=True,
                search_k=self.settings.memory_search_k,
                search_threshold=self.settings.memory_search_threshold,
            )
        except Exception as e:
            logger.warning(f"Memory service init failed (non-fatal): {e}")
            return None

    def _capture_task_result(self, task: ParsedTask, result: TaskResult) -> None:
        """
        Store agent output as a memory after successful task completion.

        Two entries are written:
        1. The full agent response (for semantic retrieval of detailed findings).
        2. A short task summary (for 'what did I work on' style queries).
        """
        if self.memory_service is None or not result.success:
            return

        proj_tag = f"project_{task.project_id}" if task.project_id else "no_project"
        base_tags = [task.agent, proj_tag]

        if result.agent_response:
            self.memory_service.capture(
                content=result.agent_response,
                source="task_output",
                project_id=task.project_id,
                agent=task.agent,
                tags=base_tags,
            )

        summary = f"@{task.agent} completed: {task.description}"
        if result.output_path:
            summary += f"\nOutput: {result.output_path}"
        if task.project_id:
            summary += f"\nProject: {task.project_id}"

        self.memory_service.capture(
            content=summary,
            source="task_output",
            project_id=task.project_id,
            agent=task.agent,
            tags=base_tags + ["summary"],
        )

    def _create_roam_wrapper(self, task: ParsedTask, result: TaskResult) -> None:
        """Create a lightweight .org file in the roam directory that links to
        the agent output, giving it a presence in the org-roam graph."""
        if not self.settings.roam_wrappers_enabled:
            return
        if not result.output_path:
            return

        import uuid
        from datetime import datetime

        output_path = Path(result.output_path)
        node_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y-%m-%d %a %H:%M")
        date_prefix = datetime.now().strftime("%Y%m%d%H%M%S")

        slug = "".join(
            c if c.isalnum() or c == "-" else "-"
            for c in task.description[:40].lower()
        ).strip("-")
        slug = "-".join(filter(None, slug.split("-")))

        wrapper_dir = self.settings.workspace_dir
        wrapper_path = wrapper_dir / f"{date_prefix}-{slug}.org"

        proj_tag = f":project-{task.project_id}:" if task.project_id else ""
        tags = f":engineering:{task.agent}:{proj_tag}"

        journal_ref = (
            f"[[file:{task.journal_date}.org][{task.journal_date}]]"
            if task.journal_date
            else "n/a"
        )

        content = (
            f":PROPERTIES:\n"
            f":ID: {node_id}\n"
            f":END:\n"
            f"#+title: {task.agent}: {task.description[:60]}\n"
            f"#+filetags: {tags}\n"
            f"#+created: [{timestamp}]\n"
            f"\n"
            f"* Output\n"
            f"\n"
            f"Agent output: [[file:{output_path}]]\n"
            f"\n"
            f"* Task context\n"
            f"\n"
            f"- Agent: ={task.agent}=\n"
            f"- Project: {task.project_id or 'none'}\n"
            f"- Journal: {journal_ref}\n"
            f"- Description: {task.description}\n"
        )

        try:
            wrapper_path.write_text(content, encoding="utf-8")
            logger.info(f"Roam wrapper: {wrapper_path.name}")
        except OSError as e:
            logger.warning(f"Failed to write roam wrapper: {e}")

    def _capture_journal_entries(self) -> None:
        """Backfill recent journal entries into memory.

        Reads daily .org files from the context lookback window and captures
        any section content that hasn't been stored yet (based on the
        latest journal_entry timestamp in memory).
        """
        if self.memory_service is None:
            return
        if not self.settings.use_org_mode:
            return

        from engineering_hub.notes.weekly_reader import OrgJournalReader

        reader = OrgJournalReader(self.settings.org_journal_dir)
        entries = reader.collect_week(days=self.settings.org_context_lookback_days)

        if not entries:
            return

        latest = self.memory_service.db.get_latest_created_at("journal_entry")

        captured = 0
        for entry in entries:
            entry_date = entry.date.isoformat()

            if latest and entry_date < latest[:10]:
                continue

            for heading, body in entry.sections.items():
                body_stripped = body.strip()
                if not body_stripped or len(body_stripped) < 20:
                    continue

                if heading in self.settings.org_task_sections:
                    continue

                content = f"[{entry_date}] {heading}\n{body_stripped}"
                self.memory_service.capture(
                    content=content,
                    source="journal_entry",
                    tags=[f"journal_{entry_date}", heading.lower().replace(" ", "_")],
                )
                captured += 1

        if captured:
            logger.info(f"Captured {captured} journal entries into memory")

    def _on_notes_changed(self) -> None:
        """Called when the shared notes file changes."""
        logger.debug("Notes file changed, checking for pending tasks")
        self.dispatcher.check_for_pending_tasks()

    def start(self) -> None:
        """Start the orchestrator."""
        logger.info("Starting Engineering Hub orchestrator...")

        # Validate notes file / journal dir exists
        if not self.notes_manager.file_exists():
            notes_display = (
                self.settings.org_journal_dir
                if self.settings.use_org_mode
                else self.settings.notes_file
            )
            raise FileNotFoundError(
                f"Notes path not found: {notes_display}\n"
                "Create the file/directory or check your configuration."
            )

        # Test Claude API connection
        if not self.agent_worker.test_connection():
            logger.warning("Claude API connection test failed - tasks may fail")

        # Start dispatcher worker
        self.dispatcher.start_worker()

        # Check for any pending tasks
        self.dispatcher.check_for_pending_tasks()

        # Start file watcher
        self.watcher.start()

        watch_path = (
            self.settings.org_journal_dir
            if self.settings.use_org_mode
            else self.settings.notes_file
        )
        logger.info("Orchestrator started successfully")
        logger.info(f"Watching: {watch_path}")
        logger.info(f"Outputs: {self.settings.output_dir}")

    def stop(self) -> None:
        """Stop the orchestrator gracefully."""
        logger.info("Stopping orchestrator...")

        # Stop file watcher
        self.watcher.stop()

        # Stop dispatcher
        self.dispatcher.stop_worker()

        self._shutdown_event.set()
        logger.info("Orchestrator stopped")

    def run(self) -> None:
        """Run the orchestrator until shutdown signal."""
        # Set up signal handlers
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start and wait
        self.start()

        try:
            # Wait for shutdown
            while not self._shutdown_event.is_set():
                self._shutdown_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def process_pending_now(self) -> list[TaskResult]:
        """Process all pending tasks immediately (synchronous).

        Useful for one-shot processing without file watching.

        Returns:
            List of task results
        """
        results = []
        pending = self.notes_manager.get_pending_tasks()

        for task in pending:
            logger.info(f"Processing: @{task.agent} - {task.description[:50]}")
            self.notes_manager.mark_task_in_progress(task)

            result = self._execute_task(task)
            results.append(result)

            if result.success:
                self.notes_manager.mark_task_completed(task)
                self.notes_manager.record_task_result(
                    task,
                    success=True,
                    output_path=result.output_path,
                )
            else:
                self.notes_manager.mark_task_blocked(task, result.error_message)
                self.notes_manager.record_task_result(
                    task,
                    success=False,
                    error_message=result.error_message,
                )

        self._capture_journal_entries()
        return results

    def status(self) -> dict:
        """Get current orchestrator status.

        Returns:
            Status dictionary
        """
        pending = self.notes_manager.get_pending_tasks()
        in_progress = self.notes_manager.get_tasks_by_status(TaskStatus.IN_PROGRESS)

        status = {
            "watcher_running": self.watcher.is_running,
            "dispatcher_running": self.dispatcher.is_running,
            "queue_size": self.dispatcher.queue_size,
            "pending_tasks": len(pending),
            "in_progress_tasks": len(in_progress),
            "notes_file": str(self.settings.notes_file),
            "output_dir": str(self.settings.output_dir),
            "memory_enabled": self.memory_service is not None,
        }

        if self.memory_service is not None:
            status["memory_stats"] = self.memory_service.get_stats()

        return status
