"""Semantic link suggestions for proposed Zettelkasten notes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from engineering_hub.zettelkasten.models import SuggestedLink

if TYPE_CHECKING:
    from engineering_hub.memory.service import MemoryService

_ID_RE = re.compile(r"^\s*:ID:\s+(.+?)\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^\s*#\+title:\s+(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def suggest_links(
    text: str,
    memory_service: "MemoryService | None",
    *,
    top_k: int = 5,
    threshold: float = 0.75,
) -> list[SuggestedLink]:
    """Return conservative semantic link suggestions from the memory store."""
    if memory_service is None:
        return []

    results = memory_service.search(query=text, k=top_k, threshold=threshold)
    suggestions: list[SuggestedLink] = []
    for result in results:
        title = _extract_title(result.content) or _title_from_tags(result.tags)
        if not title:
            title = f"Memory #{result.id}"

        node_id = _extract_id(result.content)
        target = f"id:{node_id}" if node_id else _target_from_tags(result.tags, result.id)
        category = "Directly Related" if result.similarity >= 0.85 else "Tangentially Related"
        suggestions.append(
            SuggestedLink(
                title=title,
                target=target,
                similarity=result.similarity,
                category=category,
                reason=f"Semantic similarity {result.similarity:.0%} to indexed note content.",
            )
        )
    return suggestions


def _extract_id(content: str) -> str | None:
    match = _ID_RE.search(content)
    return match.group(1).strip() if match else None


def _extract_title(content: str) -> str | None:
    match = _TITLE_RE.search(content)
    return match.group(1).strip() if match else None


def _title_from_tags(tags: list[str]) -> str | None:
    for tag in tags:
        if tag.startswith("title:"):
            return tag.removeprefix("title:").strip()
    return None


def _target_from_tags(tags: list[str], fallback_id: int) -> str:
    for tag in tags:
        if tag.startswith("file:"):
            return tag.removeprefix("file:").strip()
    return f"memory:{fallback_id}"
