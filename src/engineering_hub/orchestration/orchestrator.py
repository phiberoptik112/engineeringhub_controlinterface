"""Main orchestrator that ties all components together."""

import logging
import signal
import sys
import threading
from pathlib import Path

from engineering_hub.agents.worker import AgentWorker
from engineering_hub.config.settings import Settings
from engineering_hub.context.manager import ContextManager
from engineering_hub.core.models import ParsedTask, TaskResult
from engineering_hub.django.client import DjangoClient
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
        # Notes manager (journal or legacy mode)
        self.notes_manager = SharedNotesManager(
            self.settings.notes_file,
            use_journal_mode=self.settings.use_journal_mode,
            journal_categories=self.settings.journal_categories,
        )

        # Django client
        self.django_client = DjangoClient(
            api_url=self.settings.django_api_url,
            api_token=self.settings.django_api_token,
            cache_ttl=self.settings.django_cache_ttl,
        )

        # Context manager
        self.context_manager = ContextManager(
            django_client=self.django_client,
            notes_manager=self.notes_manager,
        )

        # Agent worker
        self.agent_worker = AgentWorker(
            api_key=self.settings.anthropic_api_key,
            model=self.settings.anthropic_model,
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
            notes_path=self.settings.notes_file,
            callback=self._on_notes_changed,
            debounce_seconds=1.0,
        )

    def _execute_task(self, task: ParsedTask) -> TaskResult:
        """Execute a task using the agent worker.

        Args:
            task: The task to execute

        Returns:
            TaskResult with execution outcome
        """
        # Build context for the task
        context = self.context_manager.format_for_agent(task)

        # Execute with agent
        return self.agent_worker.execute(task, context)

    def _on_notes_changed(self) -> None:
        """Called when the shared notes file changes."""
        logger.debug("Notes file changed, checking for pending tasks")
        self.dispatcher.check_for_pending_tasks()

    def start(self) -> None:
        """Start the orchestrator."""
        logger.info("Starting Engineering Hub orchestrator...")

        # Validate notes file exists
        if not self.notes_manager.file_exists():
            raise FileNotFoundError(
                f"Notes file not found: {self.settings.notes_file}\n"
                "Create the file or check your configuration."
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

        logger.info("Orchestrator started successfully")
        logger.info(f"Watching: {self.settings.notes_file}")
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

        return results

    def status(self) -> dict:
        """Get current orchestrator status.

        Returns:
            Status dictionary
        """
        pending = self.notes_manager.get_pending_tasks()
        in_progress = self.notes_manager.get_tasks_by_status(
            __import__("engineering_hub.core.constants", fromlist=["TaskStatus"]).TaskStatus.IN_PROGRESS
        )

        return {
            "watcher_running": self.watcher.is_running,
            "dispatcher_running": self.dispatcher.is_running,
            "queue_size": self.dispatcher.queue_size,
            "pending_tasks": len(pending),
            "in_progress_tasks": len(in_progress),
            "notes_file": str(self.settings.notes_file),
            "output_dir": str(self.settings.output_dir),
        }
