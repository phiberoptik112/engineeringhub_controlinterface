"""Task dispatcher for processing pending tasks."""

import logging
import threading
from queue import Empty, Queue
from typing import Callable

from engineering_hub.core.constants import TaskStatus
from engineering_hub.core.models import ParsedTask, TaskResult
from engineering_hub.notes.manager import SharedNotesManager

logger = logging.getLogger(__name__)


class TaskDispatcher:
    """Dispatches and processes pending tasks from shared notes."""

    def __init__(
        self,
        notes_manager: SharedNotesManager,
        task_executor: Callable[[ParsedTask], TaskResult],
    ) -> None:
        """Initialize the dispatcher.

        Args:
            notes_manager: Manager for shared notes
            task_executor: Function that executes a task and returns result
        """
        self.notes_manager = notes_manager
        self.task_executor = task_executor

        self._queue: Queue[ParsedTask] = Queue()
        self._processing = False
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def check_for_pending_tasks(self) -> list[ParsedTask]:
        """Check for and queue pending tasks.

        Returns:
            List of pending tasks found
        """
        pending = self.notes_manager.get_pending_tasks()
        for task in pending:
            if not self._is_queued(task):
                self._queue.put(task)
                logger.info(f"Queued task: @{task.agent} - {task.description[:50]}")
        return pending

    def _is_queued(self, task: ParsedTask) -> bool:
        """Check if a task is already in the queue by task_id."""
        return any(t.task_id == task.task_id for t in list(self._queue.queue))

    def process_next_task(self) -> TaskResult | None:
        """Process the next task in the queue.

        Returns:
            TaskResult if a task was processed, None if queue is empty
        """
        try:
            task = self._queue.get_nowait()
        except Empty:
            return None

        # Verify task is still pending (status might have changed)
        current_tasks = self.notes_manager.get_all_tasks()
        current_task = next(
            (t for t in current_tasks if t.task_id == task.task_id),
            None,
        )

        if current_task is None or current_task.status != TaskStatus.PENDING:
            logger.debug(f"Task no longer pending, skipping: {task.description[:50]}")
            return None

        # Mark as in progress
        logger.info(f"Starting task: @{task.agent} - {task.description[:50]}")
        self.notes_manager.mark_task_in_progress(task)

        try:
            # Execute the task
            result = self.task_executor(task)

            # Update status based on result
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

            return result

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            self.notes_manager.mark_task_blocked(task, str(e))
            return TaskResult(task=task, success=False, error_message=str(e))

    def start_worker(self) -> None:
        """Start the background worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            logger.warning("Worker already running")
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("Task dispatcher worker started")

    def stop_worker(self, timeout: float = 5.0) -> None:
        """Stop the background worker thread."""
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout)
            self._worker_thread = None
        logger.info("Task dispatcher worker stopped")

    def _worker_loop(self) -> None:
        """Background worker loop that processes tasks."""
        while not self._stop_event.is_set():
            try:
                # Try to get a task with timeout
                task = self._queue.get(timeout=1.0)
                self._process_task_safe(task)
            except Empty:
                continue

    def _process_task_safe(self, task: ParsedTask) -> None:
        """Process a task with error handling."""
        try:
            # Re-verify task is still pending
            current_tasks = self.notes_manager.get_all_tasks()
            current_task = next(
                (t for t in current_tasks if t.task_id == task.task_id),
                None,
            )

            if current_task is None or current_task.status != TaskStatus.PENDING:
                logger.debug(f"Task no longer pending: {task.description[:50]}")
                return

            # Process the task
            self.notes_manager.mark_task_in_progress(task)
            result = self.task_executor(task)

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

        except Exception as e:
            logger.error(f"Worker error processing task: {e}")
            try:
                self.notes_manager.mark_task_blocked(task, str(e))
            except Exception:
                pass

    @property
    def queue_size(self) -> int:
        """Get the current queue size."""
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        """Check if the worker is running."""
        return self._worker_thread is not None and self._worker_thread.is_alive()
