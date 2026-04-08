"""Document chunking for embedding-ready text segments.

Uses Docling's HybridChunker when available, falling back to a simple
heading-based splitter for environments without the docling extra.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass
class DocumentChunk:
    """A single embedding-ready chunk of a converted document."""

    text: str
    heading: str
    chunk_index: int
    source_file: str


def chunk_document(
    markdown: str,
    source_file: str,
    docling_doc: Any | None = None,
    max_chunk_chars: int = 4000,
) -> list[DocumentChunk]:
    """Split a document into chunks suitable for embedding.

    When a Docling ``DoclingDocument`` is provided, the HybridChunker is used
    for tokenization-aware splitting with contextualised headings.  Otherwise a
    simple markdown heading-based fallback is used.

    Args:
        markdown: The full markdown text of the document.
        source_file: Original filename (for provenance tags).
        docling_doc: Optional DoclingDocument from Docling conversion.
        max_chunk_chars: Maximum characters per chunk (fallback only;
            the Docling path uses token-based limits).

    Returns:
        List of DocumentChunk objects ready for embedding.
    """
    if docling_doc is not None:
        try:
            return _chunk_with_docling(docling_doc, source_file)
        except Exception as exc:
            logger.warning(f"Docling chunking failed, using fallback: {exc}")

    return _chunk_by_headings(markdown, source_file, max_chunk_chars)


def _chunk_with_docling(
    docling_doc: Any,
    source_file: str,
) -> list[DocumentChunk]:
    """Chunk using Docling's HybridChunker with contextualised output."""
    from docling.chunking import HybridChunker

    chunker = HybridChunker()
    chunks: list[DocumentChunk] = []

    for i, chunk in enumerate(chunker.chunk(dl_doc=docling_doc)):
        enriched = chunker.contextualize(chunk=chunk)
        heading = ""
        for line in enriched.splitlines():
            stripped = line.strip()
            if stripped:
                heading = stripped
                break

        chunks.append(
            DocumentChunk(
                text=enriched,
                heading=heading,
                chunk_index=i,
                source_file=source_file,
            )
        )

    logger.info(f"Docling chunker produced {len(chunks)} chunks for {source_file}")
    return chunks


def _chunk_by_headings(
    markdown: str,
    source_file: str,
    max_chars: int,
) -> list[DocumentChunk]:
    """Fallback: split markdown on ## headings, then by paragraph if oversized."""
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in markdown.splitlines(keepends=True):
        m = _HEADING_RE.match(line)
        if m:
            if current_lines:
                sections.append((current_heading, "".join(current_lines).strip()))
                current_lines = []
            current_heading = m.group(2).strip()
        current_lines.append(line)

    if current_lines:
        sections.append((current_heading, "".join(current_lines).strip()))

    chunks: list[DocumentChunk] = []
    idx = 0

    for heading, body in sections:
        if not body.strip():
            continue

        if len(body) <= max_chars:
            text = f"{heading}\n{body}" if heading else body
            chunks.append(
                DocumentChunk(
                    text=text, heading=heading, chunk_index=idx, source_file=source_file
                )
            )
            idx += 1
        else:
            paragraphs = re.split(r"\n\n+", body)
            buffer = ""
            for para in paragraphs:
                candidate = f"{buffer}\n\n{para}".strip() if buffer else para
                if len(candidate) > max_chars and buffer:
                    text = f"{heading}\n{buffer}" if heading else buffer
                    chunks.append(
                        DocumentChunk(
                            text=text,
                            heading=heading,
                            chunk_index=idx,
                            source_file=source_file,
                        )
                    )
                    idx += 1
                    buffer = para
                else:
                    buffer = candidate

            if buffer.strip():
                text = f"{heading}\n{buffer}" if heading else buffer
                chunks.append(
                    DocumentChunk(
                        text=text, heading=heading, chunk_index=idx, source_file=source_file
                    )
                )
                idx += 1

    if not chunks and markdown.strip():
        chunks.append(
            DocumentChunk(
                text=markdown[:max_chars],
                heading="",
                chunk_index=0,
                source_file=source_file,
            )
        )

    logger.info(f"Fallback chunker produced {len(chunks)} chunks for {source_file}")
    return chunks
