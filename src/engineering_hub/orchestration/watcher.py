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
    """Handler for shared notes file modifications.

    Can operate in two modes:

    * **Single-file mode** (``watch_dir=False``) — only fires when the exact
      ``notes_path`` file is modified.
    * **Directory mode** (``watch_dir=True``) — fires when any ``.org`` file
      inside ``notes_path`` (treated as a directory) is modified.
    """

    def __init__(
        self,
        notes_path: Path,
        callback: Callable[[], None],
        debounce_seconds: float = 1.0,
        watch_dir: bool = False,
    ) -> None:
        """Initialize the handler.

        Args:
            notes_path: Path to the shared notes file, or journal directory
                when ``watch_dir`` is True.
            callback: Function to call when a matching file changes.
            debounce_seconds: Minimum time between callbacks.
            watch_dir: When True, watch the directory for any ``.org`` change.
        """
        self.notes_path = notes_path.resolve()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.watch_dir = watch_dir
        self._last_callback = 0.0
        self._lock = threading.Lock()

    def on_modified(self, event: FileModifiedEvent) -> None:
        """Handle file modification events."""
        if event.is_directory:
            return

        event_path = Path(event.src_path).resolve()

        if self.watch_dir:
            # Accept any .org file inside the watched directory
            if event_path.suffix != ".org":
                return
            if event_path.parent != self.notes_path:
                return
        else:
            if event_path != self.notes_path:
                return

        # Debounce rapid changes
        with self._lock:
            now = time.time()
            if now - self._last_callback < self.debounce_seconds:
                logger.debug("Debouncing file change")
                return
            self._last_callback = now

        logger.info(f"Notes file modified: {event_path}")
        try:
            self.callback()
        except Exception as e:
            logger.error(f"Callback error: {e}")


class FileWatcher:
    """Watches the shared notes file (or org journal directory) for changes."""

    def __init__(
        self,
        notes_path: Path,
        callback: Callable[[], None],
        debounce_seconds: float = 1.0,
        watch_dir: bool = False,
    ) -> None:
        """Initialize the file watcher.

        Args:
            notes_path: Path to the shared notes file, or to the org-roam
                journal directory when ``watch_dir`` is True.
            callback: Function to call when a matching file changes.
            debounce_seconds: Minimum time between callbacks.
            watch_dir: When True, watch *notes_path* as a directory for any
                ``.org`` file modification (org mode).
        """
        self.notes_path = notes_path
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.watch_dir = watch_dir

        self._observer: Observer | None = None
        self._running = False

    def start(self) -> None:
        """Start watching the notes file or journal directory."""
        if self._running:
            logger.warning("Watcher already running")
            return

        if not self.notes_path.exists():
            raise FileNotFoundError(f"Notes path not found: {self.notes_path}")

        handler = NotesFileHandler(
            self.notes_path,
            self.callback,
            self.debounce_seconds,
            watch_dir=self.watch_dir,
        )

        # In directory mode watch the directory itself; in file mode watch the parent.
        watch_root = str(self.notes_path) if self.watch_dir else str(self.notes_path.parent)

        self._observer = Observer()
        self._observer.schedule(handler, watch_root, recursive=False)

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
