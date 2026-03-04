"""
Minimal HTTP MCP server exposing Engineering Hub memory tools.

Start:   engineering-hub mcp-server
Connect: http://127.0.0.1:3456  (local only by default)

Claude Desktop config (~/.config/claude/claude_desktop_config.json):
{
  "mcpServers": {
    "engineering-brain": {
      "type": "http",
      "url": "http://127.0.0.1:3456",
      "headers": { "x-hub-key": "YOUR_KEY" }
    }
  }
}

Set ENGINEERING_HUB_MCP_KEY env var to a secret of your choice.
"""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from engineering_hub.config.loader import find_config_file
from engineering_hub.config.settings import Settings
from engineering_hub.memory import MemoryService

logger = logging.getLogger(__name__)

MCP_KEY = os.environ.get("ENGINEERING_HUB_MCP_KEY", "local-dev-key")

TOOLS = [
    {
        "name": "search_brain",
        "description": (
            "Semantic search over all Engineering Hub memories: agent task outputs, "
            "research findings, journal notes, and manually captured notes. "
            "Use when you need context from previous work on a topic or project."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "project_id": {
                    "type": "integer",
                    "description": "Optional: restrict to one project",
                },
                "k": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                    "default": 5,
                },
                "threshold": {
                    "type": "number",
                    "description": "Min similarity 0-1 (default 0.35)",
                    "default": 0.35,
                },
            },
        },
    },
    {
        "name": "browse_recent",
        "description": (
            "Browse the most recently stored memories. "
            "Useful for 'what did I work on lately' or reviewing recent agent outputs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many to return (default 10)",
                    "default": 10,
                },
                "project_id": {
                    "type": "integer",
                    "description": "Optional project filter",
                },
                "source": {
                    "type": "string",
                    "description": "Filter by source type",
                    "enum": ["task_output", "journal_entry", "agent_message", "manual"],
                },
            },
        },
    },
    {
        "name": "capture_note",
        "description": (
            "Store a thought, observation, or note in Engineering Hub memory "
            "for future retrieval by you or any agent."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The text to remember",
                },
                "project_id": {
                    "type": "integer",
                    "description": "Optional project association",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags",
                },
            },
        },
    },
    {
        "name": "get_stats",
        "description": "Summary statistics about the Engineering Hub memory database.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class MCPHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing a minimal MCP-compatible tool server."""

    memory: MemoryService = None  # type: ignore[assignment]

    def log_message(self, fmt, *args):
        logger.debug("MCP %s", fmt % args)

    def _authenticated(self) -> bool:
        return self.headers.get("x-hub-key", "") == MCP_KEY

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            stats = self.memory.get_stats() if self.memory else {}
            self._send_json({"status": "ok", "memory": stats})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authenticated():
            self._send_json({"error": "unauthorized"}, 401)
            return

        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/tools/list":
            self._send_json({"tools": TOOLS})
        elif path == "/tools/call":
            self._dispatch_tool(body)
        else:
            self._send_json({"error": "unknown endpoint"}, 404)

    def _dispatch_tool(self, body: dict):
        name = body.get("name", "")
        args = body.get("arguments", {})

        try:
            if name == "search_brain":
                results = self.memory.search(
                    query=args["query"],
                    k=args.get("k", 5),
                    threshold=args.get("threshold", 0.35),
                    project_id=args.get("project_id"),
                )
                text = self.memory.format_for_context(results)
                if not text:
                    text = "No relevant memories found for that query."
                self._send_json({"content": [{"type": "text", "text": text}]})

            elif name == "browse_recent":
                rows = self.memory.browse_recent(
                    limit=args.get("limit", 10),
                    project_id=args.get("project_id"),
                    source=args.get("source"),
                )
                if rows:
                    lines = []
                    for r in rows:
                        date_str = (r.get("created_at") or "")[:10]
                        lines.append(
                            f"**[{r['source']}] {date_str}**\n"
                            f"{r['content'][:300]}"
                        )
                    text = "\n\n".join(lines)
                else:
                    text = "No recent memories found."
                self._send_json({"content": [{"type": "text", "text": text}]})

            elif name == "capture_note":
                rid = self.memory.capture(
                    content=args["content"],
                    source="manual",
                    project_id=args.get("project_id"),
                    tags=args.get("tags", []),
                )
                self._send_json({
                    "content": [{"type": "text", "text": f"Stored as memory #{rid}."}]
                })

            elif name == "get_stats":
                stats = self.memory.get_stats()
                self._send_json({
                    "content": [{"type": "text", "text": json.dumps(stats, indent=2)}]
                })

            else:
                self._send_json({"error": f"unknown tool: {name}"}, 400)

        except Exception as e:
            logger.error("Tool error (%s): %s", name, e, exc_info=True)
            self._send_json({"error": str(e)}, 500)


def run_server(
    host: str = "127.0.0.1",
    port: int = 3456,
    workspace_dir: Path | None = None,
):
    """Start the MCP server. Loads settings from workspace config."""
    config_path = find_config_file()
    settings = Settings.from_yaml(config_path) if config_path else Settings()
    ws = workspace_dir or settings.workspace_dir

    memory = MemoryService.from_workspace(
        workspace_dir=ws,
        ollama_host=settings.ollama_host,
        ollama_model=settings.ollama_embed_model,
        enabled=settings.memory_enabled,
        search_k=settings.memory_search_k,
        search_threshold=settings.memory_search_threshold,
    )

    MCPHandler.memory = memory

    server = HTTPServer((host, port), MCPHandler)
    logger.info(f"MCP server on http://{host}:{port}")
    logger.info(f"Memory DB: {ws / 'memory.db'}")
    logger.info(f"Auth key: {MCP_KEY}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        memory.db.close()
        logger.info("MCP server stopped")
