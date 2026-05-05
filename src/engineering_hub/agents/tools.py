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
