"""FastMCP sub-server exposing the Bay Area Rental Scout tools.

Thin MCP layer over :mod:`engineering_hub.rental_scout.service`. Mounted into
the engineering-brain server (``engineering_hub.mcp.server``), so external MCP
clients (Cursor, Claude Desktop) reach these tools via:

    engineering-hub mcp-server

It can also run standalone:

    python -m engineering_hub.mcp.rental_scout                # stdio
    python -m engineering_hub.mcp.rental_scout --transport sse

The workspace directory resolves from ``rental_scout.workspace_dir`` in
config.yaml (default ``~/dev/rental_scout``).
"""

import argparse
from typing import Any

from fastmcp import FastMCP

from engineering_hub.rental_scout import service

mcp = FastMCP(
    name="rental-scout",
    instructions=(
        "Tools for scanning Bay Area rental listings, managing search criteria, "
        "querying the deduplication database, and triggering the daily digest. "
        "Use run_scan to fetch and score new listings. Use get_top_matches to "
        "retrieve the best results. Use update_criteria to adjust search params."
    ),
)


@mcp.tool
def get_criteria() -> dict[str, Any]:
    """Return the current rental search criteria from criteria.yaml.

    Useful for answering questions like 'what are my current rental search
    settings?' without reading the file directly."""
    return service.get_criteria(service.resolve_workspace())


@mcp.tool
def update_criteria(
    cities: list[str] | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    bedrooms_min: int | None = None,
    bedrooms_max: int | None = None,
    amenities_required: list[str] | None = None,
    amenities_preferred: list[str] | None = None,
) -> dict[str, Any]:
    """Patch one or more fields in criteria.yaml and save.

    Only supplied arguments are modified; everything else is preserved.
    Returns the full updated criteria dict."""
    return service.update_criteria(
        service.resolve_workspace(),
        cities=cities,
        price_min=price_min,
        price_max=price_max,
        bedrooms_min=bedrooms_min,
        bedrooms_max=bedrooms_max,
        amenities_required=amenities_required,
        amenities_preferred=amenities_preferred,
    )


@mcp.tool
def run_scan(
    sources: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Trigger a rental listing scan across one or more sources.

    Shells out to the rental-scout pipeline (main.py in the workspace) and
    writes results to latest_digest.json; call get_top_matches afterwards.
    sources restricts the scan (e.g. ["craigslist", "zumper"]); dry_run
    scrapes and scores without sending the digest or marking listings seen."""
    return service.run_scan(
        service.resolve_workspace(), sources=sources, dry_run=dry_run
    )


@mcp.tool
def get_top_matches(
    limit: int = 10,
    min_score: int = 6,
    city_filter: str | None = None,
) -> dict[str, Any]:
    """Return top rental matches from the most recent scan digest.

    Each listing contains url, score, price, beds, city, neighborhood,
    summary, pros, cons, and red_flags. city_filter restricts to one city
    (e.g. "Oakland")."""
    return service.get_top_matches(
        service.resolve_workspace(),
        limit=limit,
        min_score=min_score,
        city_filter=city_filter,
    )


@mcp.tool
def get_listing_stats() -> dict[str, Any]:
    """Return summary statistics from the deduplication database:
    total listings seen, counts by source, last-seen timestamp, and
    last-scan metadata."""
    return service.get_listing_stats(service.resolve_workspace())


@mcp.tool
def clear_seen_listings(confirm: bool = False) -> dict[str, Any]:
    """Clear the deduplication database so ALL listings are re-evaluated on
    the next scan. Dry-run unless confirm=True. Useful after changing
    cities or criteria."""
    return service.clear_seen_listings(service.resolve_workspace(), confirm=confirm)


@mcp.tool
def add_journal_task(
    task_description: str,
    org_journal_dir: str | None = None,
) -> dict[str, Any]:
    """Write an @rental-scout task to today's org-roam journal so the
    Orchestrator picks it up on its next scan. org_journal_dir overrides the
    configured journal.org_journal_dir."""
    return service.add_journal_task(
        task_description, org_journal_dir=org_journal_dir
    )


@mcp.tool
def format_digest_for_org(
    limit: int = 5,
    min_score: int = 6,
) -> str:
    """Return today's top matches formatted as org-mode text, ready to append
    to a journal entry or org-roam node."""
    return service.format_digest_for_org(
        service.resolve_workspace(), limit=limit, min_score=min_score
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rental Scout MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport (stdio for Claude Desktop / claude CLI; sse for HTTP clients)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18791)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse", host=args.host, port=args.port)
