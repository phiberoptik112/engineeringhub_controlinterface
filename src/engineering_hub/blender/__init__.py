"""Blender Lab MCP integration — scene inspection via bpy execute.

Core logic lives in :mod:`engineering_hub.blender.service`; it is exposed to
journaler agents via TOOL_REGISTRY entries in ``agents/tools.py`` and, when
``blender.enabled`` is true, to external MCP clients via FastMCP tools mounted
into ``engineering_hub.mcp.server``.
"""

from engineering_hub.blender.service import (
    execute,
    format_status_message,
    format_status_report,
    health_check,
    list_tools,
    resolve_blender_endpoint,
)

__all__ = [
    "execute",
    "format_status_message",
    "format_status_report",
    "health_check",
    "list_tools",
    "resolve_blender_endpoint",
]
