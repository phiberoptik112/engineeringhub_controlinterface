"""FastMCP sub-server exposing Blender Lab MCP tools.

Thin MCP layer over :mod:`engineering_hub.blender.service`. Mounted into
the engineering-brain server (``engineering_hub.mcp.server``), so external MCP
clients (Cursor, Claude Desktop) reach these tools via:

    engineering-hub mcp-server

Requires a running Blender 5.1+ session with the Lab MCP add-on listening
(default ``127.0.0.1:9876``).
"""

from __future__ import annotations

import argparse
from typing import Any

from fastmcp import FastMCP

from engineering_hub.blender import service as blender_service

mcp = FastMCP(
    name="blender-lab-mcp",
    instructions=(
        "Tools for inspecting and controlling a live Blender session via the "
        "Lab MCP TCP bridge. Call blender_health first. Use blender_execute to "
        "run bpy Python that assigns a JSON-serializable dict to `result`."
    ),
)


@mcp.tool
def health() -> dict[str, Any]:
    """Check Lab MCP TCP connectivity and return Blender version when online."""
    return blender_service.health_check()


@mcp.tool
def list_tools(refresh: bool = False) -> dict[str, Any]:
    """Return the fixed Hub catalog for Lab MCP (not a remote dcc-mcp tool list)."""
    return blender_service.list_tools(use_cache=not refresh)


@mcp.tool
def execute(code: str, strict_json: bool = True) -> dict[str, Any]:
    """Execute Python inside Blender via Lab MCP.

    Assign a JSON-serializable dict to ``result`` in the code. Example::

        import bpy
        result = {"names": [o.name for o in bpy.data.objects]}
    """
    return blender_service.execute(code, strict_json=strict_json)


def main() -> None:
    parser = argparse.ArgumentParser(description="Blender Lab MCP FastMCP tools")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse"),
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
