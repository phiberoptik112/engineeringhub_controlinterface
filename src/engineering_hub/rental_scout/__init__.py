"""Bay Area Rental Scout — criteria management, scan orchestration, and digests.

Core logic lives in :mod:`engineering_hub.rental_scout.service`; it is exposed
to journaler agents via TOOL_REGISTRY entries in ``agents/tools.py`` and to
external MCP clients via the ``rental-scout`` sub-server mounted into
``engineering_hub.mcp.server``.
"""

from engineering_hub.rental_scout.service import (
    add_journal_task,
    clear_seen_listings,
    format_digest_for_org,
    get_criteria,
    get_listing_stats,
    get_top_matches,
    resolve_python_executable,
    resolve_workspace,
    run_scan,
    update_criteria,
)

__all__ = [
    "add_journal_task",
    "clear_seen_listings",
    "format_digest_for_org",
    "get_criteria",
    "get_listing_stats",
    "get_top_matches",
    "resolve_python_executable",
    "resolve_workspace",
    "run_scan",
    "update_criteria",
]
