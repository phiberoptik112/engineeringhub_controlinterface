"""Retrieval audit log — persistent JSONL record of every corpus search."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RetrievalAuditLog:
    """Append-only JSONL log of corpus retrieval calls.

    Each line is a JSON object with:
      timestamp, task_id, query, k, threshold, results[]
    """

    def __init__(self, log_path: Path | None) -> None:
        """Initialise the audit log.

        Args:
            log_path: Path to the JSONL file. If None, writes are silently
                skipped so the rest of the system works without a corpus DB.
        """
        self._log_path = log_path

    @property
    def path(self) -> Path | None:
        return self._log_path

    def write(
        self,
        task_id: str,
        query: str,
        results: list[Any],
        *,
        k: int | None = None,
        threshold: float | None = None,
    ) -> None:
        """Append one retrieval event to the log.

        Args:
            task_id: Stable identifier from ``ParsedTask.task_id``.
            query:   The search string passed to ``CorpusService.search()``.
            results: Raw result objects returned by the corpus service.
                     Each object must expose ``source_file``, ``page_num``,
                     ``section``, ``similarity``, and ``content`` attributes
                     (matching the ``libraryfiles_corpus`` ChunkResult shape).
            k:       Max chunks requested (informational).
            threshold: Similarity threshold used (informational).
        """
        if self._log_path is None:
            return

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "query": query,
            "k": k,
            "threshold": threshold,
            "results": [
                {
                    "source_file": getattr(r, "source_file", None),
                    "page_num": getattr(r, "page_num", None),
                    "section": getattr(r, "section", None),
                    "similarity": getattr(r, "similarity", None),
                    "content_preview": (getattr(r, "content", "") or "")[:120],
                }
                for r in results
            ],
        }

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError as exc:
            logger.warning("Could not write retrieval audit log: %s", exc)
