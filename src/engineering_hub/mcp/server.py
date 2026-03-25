"""
MCP server exposing Engineering Hub memory tools.

Transports:
  stdio (default) — for Claude Desktop / Cursor MCP config
  http            — for remote or multi-client access

Start:
  engineering-hub mcp-server                     # stdio
  engineering-hub mcp-server --transport http     # HTTP on :8000

Claude Desktop config (~/.config/claude/claude_desktop_config.json):
{
  "mcpServers": {
    "engineering-brain": {
      "command": "/path/to/.venv/bin/engineering-hub",
      "args": ["mcp-server"]
    }
  }
}

Cursor MCP config (.cursor/mcp.json):
{
  "mcpServers": {
    "engineering-brain": {
      "command": "/path/to/.venv/bin/engineering-hub",
      "args": ["mcp-server"]
    }
  }
}
"""

import json
import logging
from pathlib import Path

from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan

from engineering_hub.config.loader import find_config_file
from engineering_hub.config.settings import Settings
from engineering_hub.memory import MemoryService

logger = logging.getLogger(__name__)


@lifespan
async def memory_lifespan(server):
    """Initialize MemoryService at server startup, close DB on shutdown."""
    config_path = find_config_file()
    settings = Settings.from_yaml(config_path) if config_path else Settings()

    memory = MemoryService.from_workspace(
        workspace_dir=settings.workspace_dir,
        ollama_host=settings.ollama_host,
        ollama_model=settings.ollama_embed_model,
        enabled=settings.memory_enabled,
        search_k=settings.memory_search_k,
        search_threshold=settings.memory_search_threshold,
    )

    logger.info("Memory DB: %s", settings.workspace_dir / "memory.db")

    try:
        yield {"memory": memory, "settings": settings}
    finally:
        memory.db.close()
        logger.info("Memory database closed")


mcp = FastMCP(
    "engineering-brain",
    instructions=(
        "Engineering Hub memory tools. Use search_brain to find relevant past work, "
        "browse_recent to see latest activity, capture_note to store observations, "
        "and get_stats for a database overview."
    ),
    lifespan=memory_lifespan,
)


@mcp.tool
def search_brain(
    query: str,
    ctx: Context,
    project_id: int | None = None,
    k: int = 5,
    threshold: float = 0.35,
) -> str:
    """Semantic search over all Engineering Hub memories: agent task outputs,
    research findings, journal notes, and manually captured notes.
    Use when you need context from previous work on a topic or project."""
    memory: MemoryService = ctx.lifespan_context["memory"]
    results = memory.search(
        query=query,
        k=k,
        threshold=threshold,
        project_id=project_id,
    )
    text = memory.format_for_context(results)
    return text or "No relevant memories found for that query."


@mcp.tool
def browse_recent(
    ctx: Context,
    limit: int = 10,
    project_id: int | None = None,
    source: str | None = None,
) -> str:
    """Browse the most recently stored memories.
    Useful for 'what did I work on lately' or reviewing recent agent outputs.
    source filter options: task_output, journal_entry, agent_message, manual"""
    memory: MemoryService = ctx.lifespan_context["memory"]
    rows = memory.browse_recent(
        limit=limit,
        project_id=project_id,
        source=source,
    )
    if not rows:
        return "No recent memories found."

    lines = []
    for r in rows:
        date_str = (r.get("created_at") or "")[:10]
        lines.append(f"**[{r['source']}] {date_str}**\n{r['content'][:300]}")
    return "\n\n".join(lines)


@mcp.tool
def capture_note(
    content: str,
    ctx: Context,
    project_id: int | None = None,
    tags: list[str] | None = None,
) -> str:
    """Store a thought, observation, or note in Engineering Hub memory
    for future retrieval by you or any agent."""
    memory: MemoryService = ctx.lifespan_context["memory"]
    rid = memory.capture(
        content=content,
        source="manual",
        project_id=project_id,
        tags=tags or [],
    )
    if rid is not None:
        return f"Stored as memory #{rid}."
    return "Memory capture is currently disabled or failed."


@mcp.tool
def get_stats(ctx: Context) -> str:
    """Summary statistics about the Engineering Hub memory database."""
    memory: MemoryService = ctx.lifespan_context["memory"]
    stats = memory.get_stats()
    return json.dumps(stats, indent=2)


def run_server(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    workspace_dir: Path | None = None,
):
    """Start the MCP server with the specified transport.

    transport: 'stdio' (default, for Claude Desktop/Cursor) or 'http' (network).
    """
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "http":
        mcp.run(transport="http", host=host, port=port)
    else:
        raise ValueError(f"Unknown transport: {transport!r}. Use 'stdio' or 'http'.")
