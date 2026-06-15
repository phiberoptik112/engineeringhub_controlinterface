"""Agent tool schemas, handlers, and registry.

Provides ToolDefinition / ToolContext dataclasses, Anthropic-format tool
schemas for each callable tool, handler functions that execute them, and
a name→definition registry consumed by AgentWorker's agentic loop.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from engineering_hub.actions.file_ingest import FileIngestAction
from engineering_hub.rental_scout import service as rental_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    """Lightweight service container injected into every tool handler."""

    corpus_service: Any | None
    memory_service: Any | None
    output_dir: Path
    project_id: int | None = None


@dataclass
class ToolDefinition:
    """Pairs a tool schema (Anthropic/Ollama format) with its handler."""

    schema: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], str]


# ---------------------------------------------------------------------------
# ingest_files
# ---------------------------------------------------------------------------

INGEST_FILES_TOOL = {
    "name": "ingest_files",
    "description": (
        "Ingest files (PDF, DOCX) from a path into staging as markdown. "
        "Use when you need to read a file that hasn't been pre-staged."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Path to file or directory (e.g. ~/path/to/file.pdf)",
            },
            "project_id": {
                "type": "integer",
                "description": "Project ID for staging directory",
            },
        },
        "required": ["source_path", "project_id"],
    },
}


def handle_ingest_files(
    source_path: str,
    project_id: int,
    output_dir: Path,
    manifest_name: str = "manifest.json",
) -> str:
    """Execute ingest_files tool and return result as string."""
    action = FileIngestAction(output_dir=output_dir, manifest_name=manifest_name)
    result = action.execute(source_paths=[source_path], project_id=project_id)
    if result.success:
        return json.dumps({
            "success": True,
            "files_converted": result.files_converted,
            "manifest_path": result.manifest_path,
        })
    return json.dumps({
        "success": False,
        "error": result.error_message,
    })


# ---------------------------------------------------------------------------
# search_corpus
# ---------------------------------------------------------------------------

SEARCH_CORPUS_TOOL = {
    "name": "search_corpus",
    "description": (
        "Semantic search over the PDF reference corpus (ASTM, IBC, ASHRAE, ISO, NRC). "
        "Returns clause text with source document, page number, and section. "
        "Use for factual lookups — standard requirements, test procedures, "
        "measurement criteria. Prefer specific queries over broad ones."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query. Use standard identifiers where known "
                    "(e.g. 'ASTM E336 field measurement uncertainty §7.3')"
                ),
            },
            "k": {
                "type": "integer",
                "description": "Max results to return (default: 5, max: 10)",
            },
            "source_file": {
                "type": "string",
                "description": "Optional: restrict search to a specific document filename",
            },
        },
        "required": ["query"],
    },
}


def handle_search_corpus(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search the PDF reference corpus and return formatted results."""
    if ctx.corpus_service is None:
        return "Corpus service unavailable — corpus.enabled may be false or corpus.db not found."
    results = ctx.corpus_service.search(
        query=args["query"],
        k=min(args.get("k", 5), 10),
        source_file=args.get("source_file"),
    )
    if not results:
        return "No corpus results found for that query."
    formatted: str = ctx.corpus_service.format_for_context(results)
    return formatted


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------

SEARCH_MEMORY_TOOL = {
    "name": "search_memory",
    "description": (
        "Semantic search over Engineering Hub working memory — prior agent outputs, "
        "captured notes, and loaded documents. Use to find prior research on this topic, "
        "previous project decisions, or earlier drafts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "k": {
                "type": "integer",
                "description": "Max results (default: 5)",
            },
            "project_id": {
                "type": "integer",
                "description": "Optional: restrict to memories for a specific project",
            },
        },
        "required": ["query"],
    },
}


def handle_search_memory(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search working memory and return formatted results."""
    if ctx.memory_service is None:
        return "Memory service unavailable."
    results = ctx.memory_service.search(
        query=args["query"],
        k=args.get("k", 5),
        project_id=args.get("project_id") or ctx.project_id,
    )
    if not results:
        return "No memory results found for that query."
    formatted: str = ctx.memory_service.format_for_context(results)
    return formatted


# ---------------------------------------------------------------------------
# Rental scout tools
# ---------------------------------------------------------------------------

RENTAL_GET_CRITERIA_TOOL = {
    "name": "rental_get_criteria",
    "description": (
        "Return the current Bay Area rental search criteria (cities, price range, "
        "bedrooms, amenities) from the rental scout workspace. Use to answer "
        "'what are my current rental search settings?'"
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def handle_rental_get_criteria(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read criteria.yaml from the rental scout workspace."""
    workspace = rental_service.resolve_workspace()
    return json.dumps(rental_service.get_criteria(workspace))


RENTAL_UPDATE_CRITERIA_TOOL = {
    "name": "rental_update_criteria",
    "description": (
        "Patch one or more fields of the rental search criteria and save. "
        "Only supplied fields change; everything else is preserved. "
        "Returns the full updated criteria."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "cities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Cities to scan, e.g. ['Oakland', 'San Francisco']",
            },
            "price_min": {"type": "integer", "description": "Minimum monthly rent"},
            "price_max": {"type": "integer", "description": "Maximum monthly rent"},
            "bedrooms_min": {"type": "integer", "description": "Minimum bedrooms"},
            "bedrooms_max": {"type": "integer", "description": "Maximum bedrooms"},
            "amenities_required": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required amenities, e.g. ['in_unit_laundry', 'allows_cats']",
            },
            "amenities_preferred": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Preferred (nice-to-have) amenities",
            },
        },
        "required": [],
    },
}


def handle_rental_update_criteria(args: dict[str, Any], ctx: ToolContext) -> str:
    """Patch criteria.yaml in the rental scout workspace."""
    workspace = rental_service.resolve_workspace()
    result = rental_service.update_criteria(
        workspace,
        cities=args.get("cities"),
        price_min=args.get("price_min"),
        price_max=args.get("price_max"),
        bedrooms_min=args.get("bedrooms_min"),
        bedrooms_max=args.get("bedrooms_max"),
        amenities_required=args.get("amenities_required"),
        amenities_preferred=args.get("amenities_preferred"),
    )
    return json.dumps(result)


RENTAL_RUN_SCAN_TOOL = {
    "name": "rental_run_scan",
    "description": (
        "Trigger a rental listing scan via the rental scout pipeline. Writes "
        "results to the workspace digest; call rental_get_top_matches afterwards. "
        "May take a few minutes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional source names to restrict the scan, "
                    "e.g. ['craigslist', 'zumper']. Defaults to all enabled sources."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": "Scrape and score without sending digest or marking seen",
            },
        },
        "required": [],
    },
}


def handle_rental_run_scan(args: dict[str, Any], ctx: ToolContext) -> str:
    """Run the rental scout scan pipeline."""
    workspace = rental_service.resolve_workspace()
    result = rental_service.run_scan(
        workspace,
        sources=args.get("sources"),
        dry_run=args.get("dry_run", False),
    )
    return json.dumps(result)


RENTAL_GET_TOP_MATCHES_TOOL = {
    "name": "rental_get_top_matches",
    "description": (
        "Return top rental matches from the most recent scan digest. Each "
        "listing has url, score, price, beds, city, neighborhood, summary, "
        "pros, cons, and red_flags."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max listings to return (default 10)"},
            "min_score": {
                "type": "integer",
                "description": "Only listings scoring >= this (default 6)",
            },
            "city_filter": {
                "type": "string",
                "description": "Restrict to one city, e.g. 'Oakland'",
            },
        },
        "required": [],
    },
}


def handle_rental_get_top_matches(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read top matches from the latest rental scan digest."""
    workspace = rental_service.resolve_workspace()
    result = rental_service.get_top_matches(
        workspace,
        limit=args.get("limit", 10),
        min_score=args.get("min_score", 6),
        city_filter=args.get("city_filter"),
    )
    return json.dumps(result)


RENTAL_GET_LISTING_STATS_TOOL = {
    "name": "rental_get_listing_stats",
    "description": (
        "Summary statistics from the rental scout deduplication database: total "
        "listings seen, counts by source, last-seen timestamp, last-scan metadata."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def handle_rental_get_listing_stats(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read stats from the rental scout dedup database."""
    workspace = rental_service.resolve_workspace()
    return json.dumps(rental_service.get_listing_stats(workspace))


RENTAL_FORMAT_DIGEST_FOR_ORG_TOOL = {
    "name": "rental_format_digest_for_org",
    "description": (
        "Return today's top rental matches formatted as org-mode text, ready to "
        "append to a journal entry or org-roam node."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max listings to include (default 5)"},
            "min_score": {
                "type": "integer",
                "description": "Only listings scoring >= this (default 6)",
            },
        },
        "required": [],
    },
}


def handle_rental_format_digest_for_org(args: dict[str, Any], ctx: ToolContext) -> str:
    """Format the latest rental digest as org-mode text."""
    workspace = rental_service.resolve_workspace()
    return rental_service.format_digest_for_org(
        workspace,
        limit=args.get("limit", 5),
        min_score=args.get("min_score", 6),
    )


RENTAL_CLEAR_SEEN_LISTINGS_TOOL = {
    "name": "rental_clear_seen_listings",
    "description": (
        "Clear the rental scout deduplication database so all listings are "
        "re-evaluated on the next scan. Dry-run unless confirm=True. Use after "
        "changing cities or criteria when the user wants a fresh pass."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "confirm": {
                "type": "boolean",
                "description": "Must be true to actually delete seen listings",
            },
        },
        "required": [],
    },
}


def handle_rental_clear_seen_listings(args: dict[str, Any], ctx: ToolContext) -> str:
    """Clear or dry-run the rental scout dedup database."""
    workspace = rental_service.resolve_workspace()
    result = rental_service.clear_seen_listings(
        workspace,
        confirm=args.get("confirm", False),
    )
    return json.dumps(result)


RENTAL_ADD_JOURNAL_TASK_TOOL = {
    "name": "rental_add_journal_task",
    "description": (
        "Write an @rental-scout task to today's org-roam journal so the "
        "Orchestrator picks it up on its next scan. Use when the user wants "
        "to queue an overnight or deferred rental scout run."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "What the rental scout should do when the task runs",
            },
            "org_journal_dir": {
                "type": "string",
                "description": (
                    "Optional override for journal.org_journal_dir from config"
                ),
            },
        },
        "required": ["task_description"],
    },
}


def handle_rental_add_journal_task(args: dict[str, Any], ctx: ToolContext) -> str:
    """Queue a rental-scout org journal task for the orchestrator."""
    result = rental_service.add_journal_task(
        args["task_description"],
        org_journal_dir=args.get("org_journal_dir"),
    )
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "search_corpus": ToolDefinition(
        schema=SEARCH_CORPUS_TOOL,
        handler=handle_search_corpus,
    ),
    "search_memory": ToolDefinition(
        schema=SEARCH_MEMORY_TOOL,
        handler=handle_search_memory,
    ),
    "ingest_files": ToolDefinition(
        schema=INGEST_FILES_TOOL,
        handler=lambda args, ctx: handle_ingest_files(
            source_path=args["source_path"],
            project_id=args.get("project_id") or ctx.project_id or 0,
            output_dir=ctx.output_dir,
        ),
    ),
    "rental_get_criteria": ToolDefinition(
        schema=RENTAL_GET_CRITERIA_TOOL,
        handler=handle_rental_get_criteria,
    ),
    "rental_update_criteria": ToolDefinition(
        schema=RENTAL_UPDATE_CRITERIA_TOOL,
        handler=handle_rental_update_criteria,
    ),
    "rental_run_scan": ToolDefinition(
        schema=RENTAL_RUN_SCAN_TOOL,
        handler=handle_rental_run_scan,
    ),
    "rental_get_top_matches": ToolDefinition(
        schema=RENTAL_GET_TOP_MATCHES_TOOL,
        handler=handle_rental_get_top_matches,
    ),
    "rental_get_listing_stats": ToolDefinition(
        schema=RENTAL_GET_LISTING_STATS_TOOL,
        handler=handle_rental_get_listing_stats,
    ),
    "rental_format_digest_for_org": ToolDefinition(
        schema=RENTAL_FORMAT_DIGEST_FOR_ORG_TOOL,
        handler=handle_rental_format_digest_for_org,
    ),
    "rental_clear_seen_listings": ToolDefinition(
        schema=RENTAL_CLEAR_SEEN_LISTINGS_TOOL,
        handler=handle_rental_clear_seen_listings,
    ),
    "rental_add_journal_task": ToolDefinition(
        schema=RENTAL_ADD_JOURNAL_TASK_TOOL,
        handler=handle_rental_add_journal_task,
    ),
}


def resolve_tools(names: list[str]) -> list[ToolDefinition]:
    """Resolve tool name strings to ToolDefinitions.

    Unknown names are logged and skipped so registry.py can list tools
    that don't have handlers yet without breaking execution.
    """
    resolved = []
    for name in names:
        defn = TOOL_REGISTRY.get(name)
        if defn:
            resolved.append(defn)
        else:
            logger.debug(
                "Tool '%s' listed in registry but has no definition — skipped.",
                name,
            )
    return resolved
