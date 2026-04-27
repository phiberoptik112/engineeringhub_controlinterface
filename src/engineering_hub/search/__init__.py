"""Local web search helpers for agent context enrichment."""

from engineering_hub.search.providers import (
    AgentWebSearchSettings,
    SearchProvider,
    SearchResult,
    SearxngSearchProvider,
    build_agent_search_provider_from_settings,
    format_search_results_for_context,
)

__all__ = [
    "AgentWebSearchSettings",
    "SearchProvider",
    "SearchResult",
    "SearxngSearchProvider",
    "build_agent_search_provider_from_settings",
    "format_search_results_for_context",
]
