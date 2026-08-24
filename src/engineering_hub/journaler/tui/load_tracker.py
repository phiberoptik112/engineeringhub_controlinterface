"""Persistence layer for tracking file load frequency and recency."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class LoadEntry:
    """A single tracked file entry."""

    load_count: int = 0
    last_loaded: float = 0.0
    pinned: bool = False
    category: str = "project"


@dataclass
class LoadTrackerData:
    """Serializable state for the load tracker."""

    entries: dict[str, LoadEntry] = field(default_factory=dict)


_PRUNE_AGE_DAYS = 90
_PRUNE_MIN_COUNT = 3
_RECENCY_HALF_LIFE_DAYS = 7.0


class LoadTracker:
    """Tracks how often and recently files are loaded into context.

    Persists to ``{state_dir}/load_tracker.json``.
    Scoring: ``0.7 * recency_decay + 0.3 * log(count + 1)``
    """

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "load_tracker.json"
        self._data = self._load()
        self._prune()

    def _load(self) -> LoadTrackerData:
        if not self._path.exists():
            return LoadTrackerData()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            entries = {}
            for path_str, entry_dict in raw.get("entries", {}).items():
                entries[path_str] = LoadEntry(**entry_dict)
            return LoadTrackerData(entries=entries)
        except (json.JSONDecodeError, OSError, TypeError):
            return LoadTrackerData()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "entries": {k: asdict(v) for k, v in self._data.entries.items()}
        }
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _prune(self) -> None:
        """Remove entries older than 90 days with count < 3."""
        now = time.time()
        cutoff = now - (_PRUNE_AGE_DAYS * 86400)
        to_remove = [
            path
            for path, entry in self._data.entries.items()
            if entry.last_loaded < cutoff and entry.load_count < _PRUNE_MIN_COUNT
        ]
        for path in to_remove:
            del self._data.entries[path]
        if to_remove:
            self._save()

    def record_load(self, path: Path, category: str = "project") -> None:
        """Record a file load event."""
        key = str(path.resolve())
        entry = self._data.entries.get(key)
        if entry is None:
            entry = LoadEntry(category=category)
            self._data.entries[key] = entry

        entry.load_count += 1
        entry.last_loaded = time.time()
        entry.category = category
        self._save()

    def pin(self, path: Path) -> None:
        """Pin a file so it always appears at the top."""
        key = str(path.resolve())
        entry = self._data.entries.get(key)
        if entry:
            entry.pinned = True
            self._save()

    def unpin(self, path: Path) -> None:
        """Unpin a file."""
        key = str(path.resolve())
        entry = self._data.entries.get(key)
        if entry:
            entry.pinned = False
            self._save()

    def score(self, path: Path) -> float:
        """Compute a relevance score for a file (higher = more relevant)."""
        key = str(path.resolve())
        entry = self._data.entries.get(key)
        if entry is None:
            return 0.0

        if entry.pinned:
            return float("inf")

        recency = self._recency_decay(entry.last_loaded)
        frequency = math.log(entry.load_count + 1)
        return 0.7 * recency + 0.3 * frequency

    def _recency_decay(self, last_loaded: float) -> float:
        """Exponential decay based on days since last load."""
        if last_loaded <= 0:
            return 0.0
        days_ago = (time.time() - last_loaded) / 86400.0
        return math.exp(-days_ago / _RECENCY_HALF_LIFE_DAYS)

    def top_files(self, n: int = 10, category: str | None = None) -> list[tuple[str, float]]:
        """Return top-N files by score, optionally filtered by category."""
        items = []
        for path_str, entry in self._data.entries.items():
            if category and entry.category != category:
                continue
            path = Path(path_str)
            items.append((path_str, self.score(path)))

        items.sort(key=lambda x: x[1], reverse=True)
        return items[:n]

    def get_entry(self, path: Path) -> LoadEntry | None:
        """Get the entry for a specific path."""
        return self._data.entries.get(str(path.resolve()))
