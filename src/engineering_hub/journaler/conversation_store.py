"""Multi-conversation index and per-conversation JSONL storage for Journaler."""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engineering_hub.journaler.conversation_export import load_transcript

logger = logging.getLogger(__name__)

_ACTIVE_ID_FILENAME = "active_conversation_id"
_JSONL_FILENAME = "conversation.jsonl"
_MANIFEST_FILENAME = "manifest.json"
_LEGACY_DEFAULT_ID = "default"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    project_id   INTEGER,
    topic        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    turn_count   INTEGER NOT NULL DEFAULT 0,
    archived     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_conv_project ON conversations(project_id);
CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);
"""


def slugify(title: str) -> str:
    """Convert a title to a URL-safe conversation id base."""
    slug = title.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "conversation"


@dataclass
class Conversation:
    """Metadata for a named Journaler conversation."""

    id: str
    title: str
    project_id: int | None = None
    topic: str | None = None
    created_at: str = ""
    updated_at: str = ""
    turn_count: int = 0
    file_manifest: list[str] = field(default_factory=list)
    archived: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "project_id": self.project_id,
            "topic": self.topic,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": self.turn_count,
            "file_manifest": list(self.file_manifest),
            "archived": self.archived,
        }

    @classmethod
    def from_row(
        cls,
        row: sqlite3.Row,
        *,
        file_manifest: list[str] | None = None,
    ) -> Conversation:
        return cls(
            id=row["id"],
            title=row["title"],
            project_id=row["project_id"],
            topic=row["topic"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turn_count=int(row["turn_count"]),
            file_manifest=list(file_manifest or []),
            archived=bool(row["archived"]),
        )


@dataclass(frozen=True)
class ConversationChoice:
    """Result from the conversation picker."""

    kind: str  # "select" | "new" | "cancel"
    conversation: Conversation | None = None


class ConversationStore:
    """SQLite index + per-conversation JSONL/manifest under state_dir."""

    def __init__(
        self,
        state_dir: Path,
        *,
        db_path: Path | None = None,
        store_dir: Path | None = None,
    ) -> None:
        self._state_dir = state_dir.expanduser().resolve()
        self._db_path = (db_path or self._state_dir / "conversations.db").expanduser().resolve()
        self._store_dir = (store_dir or self._state_dir / "conversations").expanduser().resolve()
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    @property
    def state_dir(self) -> Path:
        return self._state_dir

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def conv_dir(self, conv_id: str) -> Path:
        return self._store_dir / conv_id

    def jsonl_path(self, conv_id: str) -> Path:
        return self.conv_dir(conv_id) / _JSONL_FILENAME

    def manifest_path(self, conv_id: str) -> Path:
        return self.conv_dir(conv_id) / _MANIFEST_FILENAME

    def legacy_jsonl_path(self) -> Path:
        return self._state_dir / _JSONL_FILENAME

    def _active_id_path(self) -> Path:
        return self._state_dir / _ACTIVE_ID_FILENAME

    def _read_manifest(self, conv_id: str) -> dict[str, Any]:
        path = self.manifest_path(conv_id)
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_manifest(
        self,
        conv_id: str,
        *,
        file_manifest: list[str] | None = None,
        topic: str | None = None,
        project_id: int | None = None,
    ) -> None:
        """Merge-update manifest.json for a conversation."""
        conv_dir = self.conv_dir(conv_id)
        conv_dir.mkdir(parents=True, exist_ok=True)
        existing = self._read_manifest(conv_id)
        if file_manifest is not None:
            existing["file_manifest"] = file_manifest
        if topic is not None:
            existing["topic"] = topic
        if project_id is not None:
            existing["project_id"] = project_id
        existing.setdefault("id", conv_id)
        existing["updated_at"] = self._now_iso()
        path = self.manifest_path(conv_id)
        try:
            path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to write manifest for %s: %s", conv_id, exc)

    def _hydrate(self, row: sqlite3.Row) -> Conversation:
        manifest = self._read_manifest(row["id"])
        file_manifest = manifest.get("file_manifest", [])
        if not isinstance(file_manifest, list):
            file_manifest = []
        return Conversation.from_row(
            row,
            file_manifest=[str(p) for p in file_manifest],
        )

    def _unique_id(self, base: str) -> str:
        candidate = base
        n = 2
        while self.get(candidate) is not None:
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    def create(
        self,
        title: str,
        *,
        conv_id: str | None = None,
        project_id: int | None = None,
        topic: str | None = None,
    ) -> Conversation:
        """Create a new conversation row and on-disk store."""
        now = self._now_iso()
        base_id = conv_id or slugify(title)
        final_id = self._unique_id(base_id)
        conv_dir = self.conv_dir(final_id)
        conv_dir.mkdir(parents=True, exist_ok=True)
        jsonl = self.jsonl_path(final_id)
        if not jsonl.exists():
            jsonl.touch()

        self._conn.execute(
            """
            INSERT INTO conversations
                (id, title, project_id, topic, created_at, updated_at, turn_count, archived)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (final_id, title, project_id, topic, now, now),
        )
        self._conn.commit()
        self.write_manifest(final_id, file_manifest=[], topic=topic, project_id=project_id)
        return self.get(final_id)  # type: ignore[return-value]

    def get(self, conv_id: str) -> Conversation | None:
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()
        if row is None:
            return None
        return self._hydrate(row)

    def find_by_title_fragment(self, fragment: str) -> list[Conversation]:
        pattern = f"%{fragment.strip().lower()}%"
        rows = self._conn.execute(
            """
            SELECT * FROM conversations
            WHERE archived = 0
              AND (LOWER(id) LIKE ? OR LOWER(title) LIKE ?)
            ORDER BY updated_at DESC
            """,
            (pattern, pattern),
        ).fetchall()
        return [self._hydrate(r) for r in rows]

    def list(self, *, include_archived: bool = False) -> list[Conversation]:
        if include_archived:
            rows = self._conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM conversations WHERE archived = 0 ORDER BY updated_at DESC"
            ).fetchall()
        return [self._hydrate(r) for r in rows]

    def update_meta(
        self,
        conv_id: str,
        *,
        title: str | None = None,
        project_id: int | None = None,
        topic: str | None = None,
        turn_count: int | None = None,
        file_manifest: list[str] | None = None,
    ) -> Conversation | None:
        conv = self.get(conv_id)
        if conv is None:
            return None
        now = self._now_iso()
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [now]
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if project_id is not None:
            fields.append("project_id = ?")
            values.append(project_id)
        if topic is not None:
            fields.append("topic = ?")
            values.append(topic)
        if turn_count is not None:
            fields.append("turn_count = ?")
            values.append(turn_count)
        values.append(conv_id)
        self._conn.execute(
            f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        self._conn.commit()
        if file_manifest is not None or topic is not None or project_id is not None:
            self.write_manifest(
                conv_id,
                file_manifest=file_manifest,
                topic=topic,
                project_id=project_id,
            )
        return self.get(conv_id)

    def archive(self, conv_id: str) -> bool:
        conv = self.get(conv_id)
        if conv is None:
            return False
        now = self._now_iso()
        self._conn.execute(
            "UPDATE conversations SET archived = 1, updated_at = ? WHERE id = ?",
            (now, conv_id),
        )
        self._conn.commit()
        active = self.get_active_id()
        if active == conv_id:
            self._active_id_path().unlink(missing_ok=True)
        return True

    def delete(self, conv_id: str) -> bool:
        conv = self.get(conv_id)
        if conv is None:
            return False
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self._conn.commit()
        shutil.rmtree(self.conv_dir(conv_id), ignore_errors=True)
        active = self.get_active_id()
        if active == conv_id:
            self._active_id_path().unlink(missing_ok=True)
        return True

    def set_active(self, conv_id: str) -> None:
        self._active_id_path().write_text(conv_id.strip() + "\n", encoding="utf-8")

    def get_active_id(self) -> str | None:
        path = self._active_id_path()
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None

    def get_active(self) -> Conversation | None:
        active_id = self.get_active_id()
        if not active_id:
            return None
        return self.get(active_id)

    def active_jsonl_path(self) -> Path:
        """Path to the active conversation JSONL, or legacy singleton."""
        active = self.get_active()
        if active is not None:
            return self.jsonl_path(active.id)
        legacy = self.legacy_jsonl_path()
        if legacy.is_file():
            return legacy
        return legacy

    def all_jsonl_paths(self) -> list[Path]:
        """All per-conversation JSONL files for cross-session search."""
        paths: list[Path] = []
        if not self._store_dir.is_dir():
            return paths
        for child in sorted(self._store_dir.iterdir()):
            if not child.is_dir():
                continue
            jsonl = child / _JSONL_FILENAME
            if jsonl.is_file():
                paths.append(jsonl)
        legacy = self.legacy_jsonl_path()
        if legacy.is_file() and legacy not in paths:
            paths.append(legacy)
        return paths

    def count_jsonl_turns(self, conv_id: str) -> int:
        turns = load_transcript(self.jsonl_path(conv_id))
        return sum(1 for t in turns if not t.get("archived"))

    def read_turns(
        self,
        conv_id: str,
        *,
        limit: int | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """Read JSONL turns for rehydration (newest tail when limit set)."""
        turns = load_transcript(self.jsonl_path(conv_id))
        if not include_archived:
            turns = [t for t in turns if not t.get("archived")]
        if limit is not None and limit > 0 and len(turns) > limit:
            turns = turns[-limit:]
        return turns

    def seed_from_legacy_jsonl(self, legacy_path: Path | None = None) -> Conversation:
        """Wrap legacy singleton conversation.jsonl as id=default."""
        legacy = (legacy_path or self.legacy_jsonl_path()).expanduser().resolve()
        existing = self.get(_LEGACY_DEFAULT_ID)
        if existing is not None:
            return existing

        now = self._now_iso()
        conv_dir = self.conv_dir(_LEGACY_DEFAULT_ID)
        conv_dir.mkdir(parents=True, exist_ok=True)
        target_jsonl = self.jsonl_path(_LEGACY_DEFAULT_ID)

        if legacy.is_file() and legacy.stat().st_size > 0:
            if not target_jsonl.exists() or target_jsonl.stat().st_size == 0:
                shutil.copy2(legacy, target_jsonl)

        turn_count = self.count_jsonl_turns(_LEGACY_DEFAULT_ID)
        self._conn.execute(
            """
            INSERT INTO conversations
                (id, title, project_id, topic, created_at, updated_at, turn_count, archived)
            VALUES (?, ?, NULL, NULL, ?, ?, ?, 0)
            """,
            (_LEGACY_DEFAULT_ID, "Default", now, now, turn_count),
        )
        self._conn.commit()
        self.write_manifest(_LEGACY_DEFAULT_ID, file_manifest=[])
        self.set_active(_LEGACY_DEFAULT_ID)
        return self.get(_LEGACY_DEFAULT_ID)  # type: ignore[return-value]

    def ensure_initialized(self) -> Conversation:
        """Seed legacy JSONL if DB empty; return active or default conversation."""
        rows = self._conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()
        count = int(rows["n"]) if rows else 0
        if count == 0:
            conv = self.seed_from_legacy_jsonl()
            return conv
        active = self.get_active()
        if active is not None:
            return active
        # Pick most recently updated non-archived conversation
        convs = self.list(include_archived=False)
        if convs:
            self.set_active(convs[0].id)
            return convs[0]
        return self.seed_from_legacy_jsonl()


def build_conversation_store(
    state_dir: Path,
    *,
    enabled: bool = True,
    db_path: Path | None = None,
    store_dir: Path | None = None,
) -> ConversationStore | None:
    """Factory that returns None when conversations are disabled."""
    if not enabled:
        return None
    return ConversationStore(state_dir, db_path=db_path, store_dir=store_dir)
