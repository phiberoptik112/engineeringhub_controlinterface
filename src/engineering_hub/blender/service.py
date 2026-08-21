"""Blender Lab MCP client — health checks, tool catalog, and bpy execute.

Talks to the official Blender Lab MCP add-on (default ``127.0.0.1:9876``)
over TCP with null-byte-delimited JSON. Lab has no remote tool catalog;
agents run ``bpy`` via :func:`execute`. All public functions fail gracefully
when Blender is offline or integration is disabled in config.
"""

from __future__ import annotations

import logging
from typing import Any

from engineering_hub.blender.lab_client import LabMCPError, execute as lab_execute
from engineering_hub.config.loader import find_config_file
from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)

_HEALTH_PING_CODE = (
    "import bpy\n"
    'result = {"ok": True, "version": bpy.app.version_string}\n'
)

# Fixed Hub-side catalog (Lab has no remote list_tools).
_LOCAL_TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "blender_health",
        "description": (
            "Ping the Lab MCP TCP bridge and return Blender version when connected."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "blender_list_tools",
        "description": (
            "Return this fixed Hub catalog describing Lab MCP execute usage. "
            "There is no remote dcc-mcp tool list."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "description": "Ignored for Lab MCP (catalog is local).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "blender_execute",
        "description": (
            "Execute Python inside the live Blender session via Lab MCP. "
            "Assign a JSON-serializable dict to `result`. Example:\n"
            "  import bpy\n"
            "  result = {'objects': [o.name for o in bpy.data.objects]}\n"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python source executed in Blender (sets `result`)",
                },
                "strict_json": {
                    "type": "boolean",
                    "description": (
                        "When true (default), `result` must be JSON-serializable. "
                        "Set false only for exploratory LLM-generated code."
                    ),
                },
            },
            "required": ["code"],
        },
    },
]


def _load_settings() -> Settings:
    config_path = find_config_file()
    return Settings.from_yaml(config_path) if config_path else Settings()


def resolve_blender_endpoint(settings: Settings | None = None) -> str:
    """Return ``host:port`` for the configured Lab MCP bridge."""
    settings = settings or _load_settings()
    return f"{settings.blender_host}:{settings.blender_port}"


def health_check(settings: Settings | None = None) -> dict[str, Any]:
    """Ping the Lab MCP bridge and return reachability metadata."""
    settings = settings or _load_settings()
    endpoint = resolve_blender_endpoint(settings)
    if not settings.blender_enabled:
        return {
            "enabled": False,
            "connected": False,
            "endpoint": endpoint,
            "host": settings.blender_host,
            "port": settings.blender_port,
        }

    try:
        response = lab_execute(
            _HEALTH_PING_CODE,
            host=settings.blender_host,
            port=settings.blender_port,
            timeout_s=settings.blender_connect_timeout_s,
            strict_json=True,
        )
        if response.get("status") != "ok":
            message = response.get("message") or str(response)
            return {
                "enabled": True,
                "connected": False,
                "endpoint": endpoint,
                "host": settings.blender_host,
                "port": settings.blender_port,
                "error": message,
            }
        result = response.get("result") or {}
        return {
            "enabled": True,
            "connected": True,
            "endpoint": endpoint,
            "host": settings.blender_host,
            "port": settings.blender_port,
            "blender_version": result.get("version"),
            "sample_tools": [t["name"] for t in _LOCAL_TOOL_CATALOG],
            "tool_count": len(_LOCAL_TOOL_CATALOG),
        }
    except LabMCPError as exc:
        logger.warning("blender health_check failed: %s", exc)
        return {
            "enabled": True,
            "connected": False,
            "endpoint": endpoint,
            "host": settings.blender_host,
            "port": settings.blender_port,
            "error": str(exc),
        }


def list_tools(
    settings: Settings | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Return the fixed Hub-side Lab MCP tool catalog."""
    settings = settings or _load_settings()
    if not settings.blender_enabled:
        return {"error": "Blender integration is disabled (blender.enabled=false)"}

    # use_cache is accepted for API compatibility; catalog is local and static.
    _ = use_cache
    return {
        "status": "ok",
        "cached": True,
        "backend": "lab-mcp",
        "count": len(_LOCAL_TOOL_CATALOG),
        "tools": list(_LOCAL_TOOL_CATALOG),
        "note": (
            "Lab MCP has no remote tool catalog. Use blender_execute with bpy code "
            "that assigns a dict to `result`."
        ),
    }


def execute(
    code: str,
    *,
    strict_json: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Execute Python inside Blender via Lab MCP and return a structured payload."""
    settings = settings or _load_settings()
    if not settings.blender_enabled:
        return {
            "success": False,
            "error": "Blender integration is disabled (blender.enabled=false)",
        }

    code = (code or "").strip()
    if not code:
        return {"success": False, "error": "code must be a non-empty string"}

    try:
        response = lab_execute(
            code,
            host=settings.blender_host,
            port=settings.blender_port,
            timeout_s=settings.blender_connect_timeout_s,
            strict_json=strict_json,
        )
    except LabMCPError as exc:
        logger.warning("blender execute failed: %s", exc)
        return {"success": False, "error": str(exc)}

    status = response.get("status")
    success = status == "ok"
    text_parts: list[str] = []
    if response.get("stdout"):
        text_parts.append(str(response["stdout"]))
    if response.get("stderr"):
        text_parts.append(str(response["stderr"]))
    if not success and response.get("message"):
        text_parts.append(str(response["message"]))
    result = response.get("result")
    if result is not None:
        text_parts.append(str(result))

    payload: dict[str, Any] = {
        "success": success,
        "status": status,
        "text": "\n".join(text_parts).strip(),
        "result": result,
        "response": response,
    }
    if not success:
        payload["error"] = response.get("message") or "Lab MCP execute returned an error"
    return payload


def format_status_report(settings: Settings | None = None) -> str:
    """Human-readable status for ``/blender status`` slash commands."""
    settings = settings or _load_settings()
    health = health_check(settings)
    endpoint = health.get("endpoint", resolve_blender_endpoint(settings))
    lines = [
        "Blender MCP Status:",
        f"  Enabled: {health.get('enabled', settings.blender_enabled)}",
        f"  Backend: lab-mcp (TCP)",
        f"  Endpoint: {endpoint}",
        f"  Connected: {health.get('connected', False)}",
    ]
    if health.get("blender_version"):
        lines.append(f"  Blender version: {health['blender_version']}")
    if health.get("sample_tools"):
        lines.append("  Hub tools:")
        for name in health["sample_tools"]:
            lines.append(f"    - {name}")
    if health.get("error"):
        lines.append(f"  Error: {health['error']}")

    if not settings.blender_enabled:
        lines.append(
            "  Hint: blender.enabled is false in config — set true to enable integration."
        )
    elif not health.get("connected"):
        lines.append(
            "  Hint: start Blender 5.1+ with the Lab MCP add-on "
            f"(default {settings.blender_host}:{settings.blender_port}), "
            "then retry /blender status."
        )

    return "\n".join(lines)


def format_status_message(settings: Settings | None = None) -> str:
    """Alias for :func:`format_status_report` (slash-command handlers)."""
    return format_status_report(settings)
