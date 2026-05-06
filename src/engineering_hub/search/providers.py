"""Search provider abstractions for local-first agent web retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import requests

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    """A normalized search result suitable for prompt context."""

    title: str
    url: str
    snippet: str = ""
    published_date: str | None = None
    engine: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class AgentWebSearchSettings:
    """Runtime settings for agent web search context injection."""

    enabled: bool = False
    provider: str = "searxng"
    searxng_url: str = "http://localhost:8080"
    max_results: int = 5
    max_chars: int = 12_000
    timeout_seconds: float = 10.0
    anthropic_backup_enabled: bool = False
    anthropic_tool_version: str = "web_search_20250305"
    anthropic_max_uses: int = 3


class SearchProvider(Protocol):
    """Minimal interface for web search providers."""

    def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class SearxngSearchProvider:
    """Query a SearXNG instance using its JSON search API."""

    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        if not query.strip():
            return []

        response = requests.get(
            f"{self.base_url}/search",
            params={"q": query, "format": "json"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        raw_results = data.get("results") or []
        if not isinstance(raw_results, list):
            return []

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            result = _normalize_searxng_result(item)
            if result is not None:
                results.append(result)
            if len(results) >= max_results:
                break
        return results


def _normalize_searxng_result(item: dict[str, Any]) -> SearchResult | None:
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    if not title or not url:
        return None

    snippet = str(item.get("content") or item.get("snippet") or "").strip()
    published = item.get("publishedDate") or item.get("published_date")
    engine = item.get("engine")
    score = item.get("score")
    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        published_date=str(published).strip() if published else None,
        engine=str(engine).strip() if engine else None,
        score=float(score) if isinstance(score, int | float) else None,
    )


def format_search_results_for_context(
    results: list[SearchResult],
    *,
    query: str,
    max_chars: int,
    provider_label: str = "SearXNG",
) -> str:
    """Format search results as a bounded markdown context block."""
    if not results:
        return ""

    parts = [
        f"## Web search results ({provider_label})",
        "",
        f"Retrieved for query: `{query}`",
        "",
        "Use these as current web references. Cite URLs when relying on a result, "
        "and treat snippets as search excerpts rather than authoritative full text.",
        "",
    ]
    for idx, result in enumerate(results, start=1):
        metadata: list[str] = []
        if result.published_date:
            metadata.append(f"published: {result.published_date}")
        if result.engine:
            metadata.append(f"engine: {result.engine}")
        if result.score is not None:
            metadata.append(f"score: {result.score:g}")

        parts.append(f"{idx}. **{result.title}**")
        parts.append(f"   URL: {result.url}")
        if metadata:
            parts.append(f"   Metadata: {', '.join(metadata)}")
        if result.snippet:
            parts.append(f"   Snippet: {result.snippet}")
        parts.append("")

    block = "\n".join(parts).strip()
    if len(block) <= max_chars:
        return block
    return (
        block[: max(0, max_chars)].rstrip()
        + "\n\n[Web results truncated to fit context budget.]"
    )


def build_agent_search_provider_from_settings(settings: Settings) -> SearchProvider | None:
    """Build the configured local search provider, if supported."""
    provider = (settings.agent_web_search_provider or "").strip().lower()
    if provider == "searxng":
        return SearxngSearchProvider(
            base_url=settings.agent_web_search_searxng_url,
            timeout_seconds=settings.agent_web_search_timeout_seconds,
        )
    if settings.agent_web_search_enabled:
        logger.warning("Unsupported agent web search provider: %s", provider)
    return None
