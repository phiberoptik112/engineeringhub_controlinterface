"""
LocalMemDB -- SQLite-backed vector store for Engineering Hub.

Embeddings are stored as raw binary blobs (numpy float32 arrays).
Cosine similarity search is done in Python via numpy after loading
the embedding column. Fast enough for tens of thousands of entries;
no external dependencies beyond numpy (Python stdlib only for DB layer).

Schema
------
thoughts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT    NOT NULL,
    embedding   BLOB,               -- numpy float32 array, little-endian
    source      TEXT    NOT NULL,   -- 'task_output' | 'journal_entry' |
                                    --   'agent_message' | 'manual'
    project_id  INTEGER,
    agent       TEXT,
    tags        TEXT,               -- JSON array stored as string
    created_at  TEXT    NOT NULL,   -- ISO 8601
    updated_at  TEXT    NOT NULL
)
"""

import json
import logging
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS thoughts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT    NOT NULL,
    embedding  BLOB,
    source     TEXT    NOT NULL DEFAULT 'manual',
    project_id INTEGER,
    agent      TEXT,
    tags       TEXT    NOT NULL DEFAULT '[]',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_thoughts_project  ON thoughts (project_id);
CREATE INDEX IF NOT EXISTS idx_thoughts_source   ON thoughts (source);
CREATE INDEX IF NOT EXISTS idx_thoughts_created  ON thoughts (created_at);
"""


def _vec_to_blob(vec: list[float]) -> bytes:
    """Pack a list of float32 values into a bytes blob."""
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> np.ndarray:
    """Unpack a bytes blob into a float32 numpy array."""
    return np.frombuffer(blob, dtype=np.float32)


def _cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """
    Compute cosine similarity between a query vector and a matrix of row vectors.
    Returns a 1-D array of similarity scores in [-1, 1].
    """
    query_norm = query / (np.linalg.norm(query) + 1e-10)
    row_norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    normed = matrix / row_norms
    return normed @ query_norm


class LocalMemDB:
    """
    Thin SQLite wrapper with in-Python cosine similarity search.

    Usage
    -----
    db = LocalMemDB(Path("~/org-roam/engineering-hub/memory.db").expanduser())
    thought_id = db.insert("Some text content", embedding=[0.1, 0.2, ...], source="manual")
    results = db.search(query_embedding=[0.1, 0.2, ...], k=5, threshold=0.35)
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        logger.debug(f"LocalMemDB opened at {db_path}")

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def insert(
        self,
        content: str,
        embedding: Optional[list[float]],
        source: str = "manual",
        project_id: Optional[int] = None,
        agent: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> int:
        """
        Insert a thought and return its new row ID.
        embedding may be None if Ollama was unavailable -- row is still stored,
        just won't appear in similarity search.
        """
        now = datetime.now(timezone.utc).isoformat()
        blob = _vec_to_blob(embedding) if embedding else None
        cursor = self._conn.execute(
            """
            INSERT INTO thoughts (content, embedding, source, project_id, agent, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content,
                blob,
                source,
                project_id,
                agent,
                json.dumps(tags or []),
                now,
                now,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def delete(self, thought_id: int) -> bool:
        """Delete a thought by ID. Returns True if a row was deleted."""
        cursor = self._conn.execute("DELETE FROM thoughts WHERE id = ?", (thought_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search(
        self,
        query_embedding: list[float],
        k: int = 5,
        threshold: float = 0.35,
        project_id: Optional[int] = None,
        source: Optional[str] = None,
    ) -> list[dict]:
        """
        Return up to k thoughts whose embedding has cosine similarity >= threshold
        to the query_embedding, sorted by similarity descending.

        Optional filters: project_id, source.

        Returns list of dicts with keys:
            id, content, source, project_id, agent, tags, similarity, created_at
        """
        sql = "SELECT * FROM thoughts WHERE embedding IS NOT NULL"
        params: list = []

        if project_id is not None:
            sql += " AND project_id = ?"
            params.append(project_id)
        if source is not None:
            sql += " AND source = ?"
            params.append(source)

        rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return []

        query_vec = np.array(query_embedding, dtype=np.float32)
        ids, blobs = zip(*[(r["id"], r["embedding"]) for r in rows])
        matrix = np.stack([_blob_to_vec(b) for b in blobs])

        similarities = _cosine_similarity(query_vec, matrix)

        row_map = {r["id"]: r for r in rows}
        scored = [
            (ids[i], float(similarities[i]))
            for i in range(len(ids))
            if similarities[i] >= threshold
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:k]

        return [
            {
                "id": thought_id,
                "content": row_map[thought_id]["content"],
                "source": row_map[thought_id]["source"],
                "project_id": row_map[thought_id]["project_id"],
                "agent": row_map[thought_id]["agent"],
                "tags": json.loads(row_map[thought_id]["tags"] or "[]"),
                "similarity": round(sim, 4),
                "created_at": row_map[thought_id]["created_at"],
            }
            for thought_id, sim in top
        ]

    def browse_recent(
        self,
        limit: int = 10,
        project_id: Optional[int] = None,
        source: Optional[str] = None,
    ) -> list[dict]:
        """Return the most recently inserted thoughts (no embedding needed)."""
        sql = "SELECT * FROM thoughts WHERE 1=1"
        params: list = []

        if project_id is not None:
            sql += " AND project_id = ?"
            params.append(project_id)
        if source is not None:
            sql += " AND source = ?"
            params.append(source)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "content": r["content"],
                "source": r["source"],
                "project_id": r["project_id"],
                "agent": r["agent"],
                "tags": json.loads(r["tags"] or "[]"),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_stats(self) -> dict:
        """Return summary counts for the memory database."""
        total = self._conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
        with_embedding = self._conn.execute(
            "SELECT COUNT(*) FROM thoughts WHERE embedding IS NOT NULL"
        ).fetchone()[0]

        by_source = {
            row["source"]: row["cnt"]
            for row in self._conn.execute(
                "SELECT source, COUNT(*) as cnt FROM thoughts GROUP BY source"
            ).fetchall()
        }

        by_project = {
            str(row["project_id"]): row["cnt"]
            for row in self._conn.execute(
                "SELECT project_id, COUNT(*) as cnt FROM thoughts "
                "WHERE project_id IS NOT NULL GROUP BY project_id"
            ).fetchall()
        }

        oldest = self._conn.execute(
            "SELECT created_at FROM thoughts ORDER BY created_at ASC LIMIT 1"
        ).fetchone()

        return {
            "total_thoughts": total,
            "with_embedding": with_embedding,
            "without_embedding": total - with_embedding,
            "by_source": by_source,
            "by_project": by_project,
            "oldest_entry": oldest[0] if oldest else None,
        }

    def get_latest_created_at(self, source: str) -> str | None:
        """Return the most recent created_at for a given source type."""
        row = self._conn.execute(
            "SELECT created_at FROM thoughts WHERE source = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (source,),
        ).fetchone()
        return row[0] if row else None

    def get_by_id(self, thought_id: int) -> Optional[dict]:
        """Retrieve a single thought by ID."""
        row = self._conn.execute(
            "SELECT * FROM thoughts WHERE id = ?", (thought_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "content": row["content"],
            "source": row["source"],
            "project_id": row["project_id"],
            "agent": row["agent"],
            "tags": json.loads(row["tags"] or "[]"),
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
