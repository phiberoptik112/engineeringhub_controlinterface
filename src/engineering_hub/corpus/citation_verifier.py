"""Citation verifier — validates [SOURCE:] tags in agent output against the
retrieval audit log and the corpus database."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches [SOURCE: ASTM_E336.pdf p.23 — Section 7.3]
_SOURCE_PATTERN = re.compile(r"\[SOURCE:\s*([^\]]+?)\s*\]")
# Matches [PARAMETRIC: ...]
_PARAMETRIC_PATTERN = re.compile(r"\[PARAMETRIC:[^\]]+\]")


@dataclass
class CitationCheckResult:
    """Result of verifying a single [SOURCE: ...] citation."""

    citation_text: str
    source_file: str
    page_num: int | None
    in_corpus: bool
    was_retrieved: bool
    page_retrieved: bool
    similarity: float | None
    retrieved_pages: list[int | None] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Human-readable verification status."""
        if self.page_retrieved:
            return "VERIFIED"
        if self.was_retrieved:
            return "PARTIAL"
        if self.in_corpus:
            return "NOT_RETRIEVED"
        return "NOT_IN_CORPUS"


class CitationVerifier:
    """Cross-checks [SOURCE:] tags in agent output against the audit log and
    the corpus SQLite database.

    Args:
        audit_log_path: Path to ``retrieval_audit.jsonl``.
        corpus_db_path: Path to the ``corpus.db`` SQLite file produced by
            ``libraryfiles_corpus`` ingest.
    """

    def __init__(
        self,
        audit_log_path: Path | None,
        corpus_db_path: Path | None,
    ) -> None:
        self._audit_log_path = audit_log_path
        self._corpus_db_path = corpus_db_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_output(
        self,
        output_text: str,
        task_id: str,
    ) -> list[CitationCheckResult]:
        """Parse all [SOURCE:] tags from *output_text* and verify each one.

        Args:
            output_text: The full agent response text.
            task_id:     ``ParsedTask.task_id`` for this task — used to look
                         up the matching audit-log entries.

        Returns:
            One ``CitationCheckResult`` per unique [SOURCE:] tag found.
        """
        if self._audit_log_path is None:
            logger.debug("CitationVerifier: no audit log path configured, skipping.")
            return []

        retrieved_chunks = self._load_retrieved_chunks(task_id)
        raw_citations = _SOURCE_PATTERN.findall(output_text)

        seen: set[str] = set()
        results: list[CitationCheckResult] = []

        for citation_text in raw_citations:
            if citation_text in seen:
                continue
            seen.add(citation_text)

            source_file, page_num = self._parse_citation(citation_text)
            in_corpus = self._file_in_corpus(source_file)

            matching_chunks = [
                c for c in retrieved_chunks
                if c.get("source_file") == source_file
            ]
            was_retrieved = bool(matching_chunks)

            retrieved_pages = [c.get("page_num") for c in matching_chunks]
            page_retrieved = (
                page_num is not None
                and page_num in retrieved_pages
            ) if was_retrieved else False

            similarity: float | None = None
            if matching_chunks:
                sims = [
                    c["similarity"]
                    for c in matching_chunks
                    if c.get("similarity") is not None
                ]
                similarity = max(sims) if sims else None

            results.append(CitationCheckResult(
                citation_text=citation_text,
                source_file=source_file,
                page_num=page_num,
                in_corpus=in_corpus,
                was_retrieved=was_retrieved,
                page_retrieved=page_retrieved,
                similarity=similarity,
                retrieved_pages=[p for p in retrieved_pages if p is not None],
            ))

        return results

    def count_parametric_claims(self, output_text: str) -> int:
        """Return the number of [PARAMETRIC:] tags in *output_text*."""
        return len(_PARAMETRIC_PATTERN.findall(output_text))

    def format_verification_report(
        self,
        results: list[CitationCheckResult],
        parametric_count: int = 0,
    ) -> str:
        """Render a markdown verification report.

        Args:
            results:          Output of ``verify_output()``.
            parametric_count: Number of [PARAMETRIC:] tags found (for summary).

        Returns:
            Markdown string suitable for appending to the org journal or
            writing to a ``.citations.md`` sidecar file.
        """
        lines = ["### Citation Verification Report", ""]

        if not results and parametric_count == 0:
            lines.append("_No [SOURCE:] or [PARAMETRIC:] tags found in agent output._")
            lines.append(
                "_Consider checking whether citation requirements are in the system prompt._"
            )
            return "\n".join(lines)

        verified = sum(1 for r in results if r.status == "VERIFIED")
        partial = sum(1 for r in results if r.status == "PARTIAL")
        not_retrieved = sum(1 for r in results if r.status == "NOT_RETRIEVED")
        not_in_corpus = sum(1 for r in results if r.status == "NOT_IN_CORPUS")

        lines.append(
            f"**Summary:** {len(results)} source citation(s) | "
            f"{verified} verified | {partial} partial | "
            f"{not_retrieved} not retrieved | {not_in_corpus} not in corpus | "
            f"{parametric_count} parametric claim(s)"
        )
        lines.append("")

        for r in results:
            sim_str = f"sim={r.similarity:.2f}" if r.similarity is not None else "sim=n/a"
            if r.status == "VERIFIED":
                flag = "✓ VERIFIED"
                detail = f"page retrieved ({sim_str})"
            elif r.status == "PARTIAL":
                pages_str = ", ".join(
                    f"p.{p}" for p in sorted(r.retrieved_pages)
                ) or "unknown pages"
                flag = "⚠ PARTIAL"
                detail = f"document retrieved ({sim_str}) but not this page — retrieved: {pages_str}"
            elif r.status == "NOT_RETRIEVED":
                flag = "✗ NOT RETRIEVED"
                detail = "document in corpus but was not fetched for this task"
            else:
                flag = "✗ NOT IN CORPUS"
                detail = "document not found in corpus.db — possible hallucination"

            lines.append(f"- `[{r.citation_text}]` → **{flag}** — {detail}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_retrieved_chunks(self, task_id: str) -> list[dict]:
        """Return all chunk dicts from audit log entries matching *task_id*."""
        if self._audit_log_path is None or not self._audit_log_path.exists():
            return []

        chunks: list[dict] = []
        try:
            with self._audit_log_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("task_id") == task_id:
                        chunks.extend(entry.get("results", []))
        except OSError as exc:
            logger.warning("Could not read audit log: %s", exc)

        return chunks

    def _file_in_corpus(self, source_file: str) -> bool:
        """Return True if *source_file* appears in the corpus chunks table."""
        if self._corpus_db_path is None or not self._corpus_db_path.exists():
            return False
        try:
            with sqlite3.connect(str(self._corpus_db_path)) as conn:
                row = conn.execute(
                    "SELECT 1 FROM chunks WHERE source_file = ? LIMIT 1",
                    (source_file,),
                ).fetchone()
                return row is not None
        except sqlite3.Error as exc:
            logger.warning("corpus.db query failed for '%s': %s", source_file, exc)
            return False

    @staticmethod
    def _parse_citation(citation_text: str) -> tuple[str, int | None]:
        """Parse ``'ASTM_E336.pdf p.23 — Section 7.3'`` → ``('ASTM_E336.pdf', 23)``.

        The source file is everything before the first whitespace or em-dash
        that precedes a page reference.
        """
        page_match = re.search(r"\bp\.(\d+)", citation_text)
        page_num = int(page_match.group(1)) if page_match else None

        # Source file is the token before any whitespace / em-dash / page ref
        source_file = re.split(r"\s+p\.\d+|\s+—|\s+--", citation_text)[0].strip()
        return source_file, page_num
