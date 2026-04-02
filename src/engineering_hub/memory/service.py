"""
MemoryService -- the Engineering Hub's interface to its local memory store.

This is the only class the rest of the codebase touches.
LocalMemDB and OllamaEmbedder are implementation details hidden here.

Capture flow:
    text -> OllamaEmbedder.embed() -> LocalMemDB.insert()

Search flow:
    query -> OllamaEmbedder.embed() -> LocalMemDB.search() -> list[MemoryResult]

All failures are non-fatal: if Ollama is down or the DB write fails,
a warning is logged and execution continues normally.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from engineering_hub.memory.db import LocalMemDB
from engineering_hub.memory.embedder import OllamaEmbedder

logger = logging.getLogger(__name__)


@dataclass
class MemoryResult:
    """A single result returned from a memory search."""

    id: int
    content: str
    source: str
    similarity: float
    project_id: Optional[int] = None
    agent: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    created_at: Optional[str] = None

    def as_context_snippet(self, max_chars: int = 400) -> str:
        """Format this result as a short readable snippet."""
        date = (self.created_at or "")[:10]
        src_label = {
            "task_output": "Prior output",
            "journal_entry": "Journal note",
            "agent_message": "Agent message",
            "file_ingest": "Ingested doc",
            "manual": "Note",
        }.get(self.source, self.source)

        proj = f" · project {self.project_id}" if self.project_id else ""
        agent = f" · @{self.agent}" if self.agent else ""
        header = f"**{src_label}{proj}{agent} -- {date}** _{self.similarity:.0%} match_"

        body = self.content
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "..."

        return f"{header}\n{body}"


class MemoryService:
    """
    Facade over LocalMemDB + OllamaEmbedder.

    Instantiated once by the Orchestrator and injected into ContextManager.
    Can be disabled cleanly (enabled=False) without affecting task execution.
    """

    def __init__(
        self,
        db: LocalMemDB,
        embedder: OllamaEmbedder,
        enabled: bool = True,
        search_k: int = 5,
        search_threshold: float = 0.35,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.enabled = enabled
        self.search_k = search_k
        self.search_threshold = search_threshold

    @classmethod
    def from_workspace(
        cls,
        workspace_dir: Path,
        ollama_host: str = "http://localhost:11434",
        ollama_model: str = "nomic-embed-text",
        enabled: bool = True,
        search_k: int = 5,
        search_threshold: float = 0.35,
    ) -> "MemoryService":
        """
        Convenience constructor: creates the DB file inside the workspace directory
        and initialises the embedder.
        """
        db_path = workspace_dir / "memory.db"
        db = LocalMemDB(db_path)
        embedder = OllamaEmbedder(model=ollama_model, host=ollama_host)

        service = cls(
            db=db,
            embedder=embedder,
            enabled=enabled,
            search_k=search_k,
            search_threshold=search_threshold,
        )

        if enabled:
            if embedder.is_available():
                logger.info(
                    f"Memory service ready -- DB: {db_path}, "
                    f"embedder: {ollama_model}"
                )
            else:
                logger.warning(
                    f"Ollama model '{ollama_model}' not available. "
                    "Memory capture/search disabled until Ollama is running. "
                    f"Fix: ollama pull {ollama_model}"
                )

        return service

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(
        self,
        content: str,
        source: str,
        project_id: Optional[int] = None,
        agent: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> Optional[int]:
        """
        Embed and store content as a memory. Returns the new row ID, or None
        if disabled or if an error occurs (failure is always non-fatal).

        source should be one of:
            'task_output'   -- agent-generated document or response
            'journal_entry' -- note from the engineer's journal
            'agent_message' -- message in the communication thread
            'manual'        -- captured via MCP tool or CLI
        """
        if not self.enabled or not content.strip():
            return None

        try:
            embedding = self.embedder.embed(content)
        except RuntimeError as e:
            logger.warning(f"Embedding failed, storing without vector: {e}")
            embedding = None

        try:
            row_id = self.db.insert(
                content=content,
                embedding=embedding,
                source=source,
                project_id=project_id,
                agent=agent,
                tags=tags,
            )
            logger.debug(f"Memory #{row_id} captured ({source}): {content[:60]}...")
            return row_id
        except Exception as e:
            logger.warning(f"Memory DB write failed (non-fatal): {e}")
            return None

    def capture_document(
        self,
        chunks: list,
        project_id: Optional[int] = None,
    ) -> int:
        """Embed and store each document chunk. Returns count of stored chunks.

        Each chunk is stored with source='file_ingest' and provenance tags
        so it can be filtered or traced back to the original file.

        Args:
            chunks: List of DocumentChunk objects from memory.chunker.
            project_id: Optional project ID for all chunks.
        """
        if not self.enabled or not chunks:
            return 0

        stored = 0
        for chunk in chunks:
            tags = [
                "file_chunk",
                f"file:{chunk.source_file}",
            ]
            if chunk.heading:
                tags.append(f"heading:{chunk.heading[:80]}")

            row_id = self.capture(
                content=chunk.text,
                source="file_ingest",
                project_id=project_id,
                tags=tags,
            )
            if row_id is not None:
                stored += 1

        logger.info(
            f"Captured {stored}/{len(chunks)} chunks from "
            f"{chunks[0].source_file if chunks else '?'}"
        )
        return stored

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        k: Optional[int] = None,
        threshold: Optional[float] = None,
        project_id: Optional[int] = None,
        source: Optional[str] = None,
    ) -> list[MemoryResult]:
        """
        Semantic search. Returns up to k MemoryResults above the similarity
        threshold, sorted by relevance.

        project_id and source are optional SQL-level pre-filters that reduce
        the candidate set before cosine scoring.
        """
        if not self.enabled or not query.strip():
            return []

        k = k if k is not None else self.search_k
        threshold = threshold if threshold is not None else self.search_threshold

        try:
            query_embedding = self.embedder.embed(query)
        except RuntimeError as e:
            logger.warning(f"Search embedding failed (non-fatal): {e}")
            return []

        try:
            rows = self.db.search(
                query_embedding=query_embedding,
                k=k,
                threshold=threshold,
                project_id=project_id,
                source=source,
            )
        except Exception as e:
            logger.warning(f"Memory search failed (non-fatal): {e}")
            return []

        return [
            MemoryResult(
                id=r["id"],
                content=r["content"],
                source=r["source"],
                similarity=r["similarity"],
                project_id=r.get("project_id"),
                agent=r.get("agent"),
                tags=r.get("tags", []),
                created_at=r.get("created_at"),
            )
            for r in rows
        ]

    def browse_recent(
        self,
        limit: int = 10,
        project_id: Optional[int] = None,
        source: Optional[str] = None,
    ) -> list[dict]:
        """Return the most recently stored memories (no embedding needed)."""
        try:
            return self.db.browse_recent(
                limit=limit,
                project_id=project_id,
                source=source,
            )
        except Exception as e:
            logger.warning(f"Memory browse failed (non-fatal): {e}")
            return []

    def get_stats(self) -> dict:
        """Return summary statistics about the memory database."""
        try:
            return self.db.get_stats()
        except Exception as e:
            logger.warning(f"Memory stats failed (non-fatal): {e}")
            return {}

    # ------------------------------------------------------------------
    # Context formatting
    # ------------------------------------------------------------------

    def format_for_context(
        self,
        results: list[MemoryResult],
        max_results: int = 5,
    ) -> str:
        """
        Format a list of search results as a markdown context block suitable
        for injection into an agent prompt. Returns empty string if no results.
        """
        if not results:
            return ""

        lines = ["### Relevant Past Context", ""]
        for r in results[:max_results]:
            lines.append(r.as_context_snippet())
            lines.append("")

        return "\n".join(lines)
