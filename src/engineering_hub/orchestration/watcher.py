"""File watcher for monitoring shared notes changes."""

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class NotesFileHandler(FileSystemEventHandler):
    """Handler for shared notes file modifications."""

    def __init__(
        self,
        notes_path: Path,
        callback: Callable[[], None],
        debounce_seconds: float = 1.0,
    ) -> None:
        """Initialize the handler.

        Args:
            notes_path: Path to the shared notes file
            callback: Function to call when file changes
            debounce_seconds: Minimum time between callbacks
        """
        self.notes_path = notes_path.resolve()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._last_callback = 0.0
        self._lock = threading.Lock()

    def on_modified(self, event: FileModifiedEvent) -> None:
        """Handle file modification events."""
        if event.is_directory:
            return

        # Check if this is our notes file
        event_path = Path(event.src_path).resolve()
        if event_path != self.notes_path:
            return

        # Debounce rapid changes
        with self._lock:
            now = time.time()
            if now - self._last_callback < self.debounce_seconds:
                logger.debug("Debouncing file change")
                return
            self._last_callback = now

        logger.info(f"Notes file modified: {self.notes_path}")
        try:
            self.callback()
        except Exception as e:
            logger.error(f"Callback error: {e}")


class FileWatcher:
    """Watches the shared notes file for changes."""

    def __init__(
        self,
        notes_path: Path,
        callback: Callable[[], None],
        debounce_seconds: float = 1.0,
    ) -> None:
        """Initialize the file watcher.

        Args:
            notes_path: Path to the shared notes file
            callback: Function to call when file changes
            debounce_seconds: Minimum time between callbacks
        """
        self.notes_path = notes_path
        self.callback = callback
        self.debounce_seconds = debounce_seconds

        self._observer: Observer | None = None
        self._running = False

    def start(self) -> None:
        """Start watching the notes file."""
        if self._running:
            logger.warning("Watcher already running")
            return

        if not self.notes_path.exists():
            raise FileNotFoundError(f"Notes file not found: {self.notes_path}")

        # Create handler and observer
        handler = NotesFileHandler(
            self.notes_path,
            self.callback,
            self.debounce_seconds,
        )

        self._observer = Observer()
        self._observer.schedule(
            handler,
            str(self.notes_path.parent),
            recursive=False,
        )

        self._observer.start()
        self._running = True
        logger.info(f"Started watching: {self.notes_path}")

    def stop(self) -> None:
        """Stop watching the notes file."""
        if not self._running or self._observer is None:
            return

        self._observer.stop()
        self._observer.join(timeout=5)
        self._observer = None
        self._running = False
        logger.info("File watcher stopped")

    @property
    def is_running(self) -> bool:
        """Check if the watcher is running."""
        return self._running

    def __enter__(self) -> "FileWatcher":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit."""
        self.stop()
