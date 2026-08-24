"""Blender MCP integration — remote scene inspection and acoustic visualization.

Core logic lives in :mod:`engineering_hub.blender.service`; it is exposed to
journaler agents via TOOL_REGISTRY entries in ``agents/tools.py`` and, when
``blender.enabled`` is true, to external MCP clients via a proxy mounted into
``engineering_hub.mcp.server``.
"""

from engineering_hub.blender.service import (
    call_tool,
    filter_tool_names,
    format_status_message,
    format_status_report,
    health_check,
    list_tools,
    resolve_blender_mcp_url,
)

__all__ = [
    "call_tool",
    "filter_tool_names",
    "format_status_message",
    "format_status_report",
    "health_check",
    "list_tools",
    "resolve_blender_mcp_url",
]
