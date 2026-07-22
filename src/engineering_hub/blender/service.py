"""Blender MCP client — health checks, tool listing, and proxied tool calls.

Talks to a running Blender MCP server (default ``http://127.0.0.1:8765/mcp``)
via the fastmcp ``Client``. Optional allowlist/denylist filters tool names
before invocation. All public functions fail gracefully when Blender is offline
or integration is disabled in config.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from engineering_hub.config.loader import find_config_file
from engineering_hub.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class _ToolCache:
    fetched_at: float
    tools: list[dict[str, Any]]


_tool_cache: _ToolCache | None = None


def _load_settings() -> Settings:
    config_path = find_config_file()
    return Settings.from_yaml(config_path) if config_path else Settings()


def resolve_blender_mcp_url(settings: Settings | None = None) -> str:
    """Return the configured Blender MCP endpoint URL."""
    settings = settings or _load_settings()
    return settings.blender_mcp_url


def filter_tool_names(names: list[str], settings: Settings) -> list[str]:
    """Apply configured allowlist/denylist to remote tool names."""
    denylist = settings.blender_tool_denylist or []
    allowlist = settings.blender_tool_allowlist
    filtered: list[str] = []
    for name in names:
        if denylist and name in denylist:
            continue
        if allowlist is not None and name not in allowlist:
            continue
        filtered.append(name)
    return filtered


def _auth_headers(settings: Settings) -> dict[str, str]:
    token = (settings.blender_auth_token or "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _build_client(settings: Settings) -> Client:
    url = resolve_blender_mcp_url(settings)
    timeout = settings.blender_connect_timeout_s
    headers = _auth_headers(settings)
    if headers:
        transport = StreamableHttpTransport(url, headers=headers)
        return Client(transport, timeout=timeout)
    return Client(url, timeout=timeout)


def _run_async(coro):
    """Run an async coroutine from synchronous agent tool handlers."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _tool_allowed(name: str, settings: Settings) -> bool:
    return name in filter_tool_names([name], settings)


def _serialize_tool(tool: Any) -> dict[str, Any]:
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        description = getattr(tool, "description", "") or ""
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", {})
        return {
            "name": name,
            "description": description if isinstance(description, str) else str(description),
            "inputSchema": schema if isinstance(schema, dict) else {},
        }

    if hasattr(tool, "model_dump"):
        try:
            data = tool.model_dump(mode="json")
        except TypeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"]:
            return {
                "name": data["name"],
                "description": data.get("description") or "",
                "inputSchema": data.get("inputSchema", {}),
            }
    return {
        "name": str(getattr(tool, "name", "")),
        "description": getattr(tool, "description", "") or "",
        "inputSchema": getattr(tool, "inputSchema", {}),
    }


def _extract_text(result: Any) -> str:
    if hasattr(result, "model_dump"):
        data = result.model_dump(mode="json")
    else:
        data = _serialize_call_result(result)

    parts: list[str] = []
    for block in data.get("content", []):
        if isinstance(block, dict):
            text = block.get("text")
            if text:
                parts.append(str(text))
        else:
            parts.append(str(block))
    if data.get("structuredContent") is not None:
        parts.append(str(data["structuredContent"]))
    return "\n".join(parts).strip()


def _serialize_call_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")

    content: list[Any] = []
    for block in result.content:
        if hasattr(block, "model_dump"):
            content.append(block.model_dump(mode="json"))
        else:
            content.append(str(block))
    payload: dict[str, Any] = {
        "content": content,
        "isError": bool(result.isError),
    }
    if result.structuredContent is not None:
        payload["structuredContent"] = result.structuredContent
    return payload


def _cache_valid(settings: Settings) -> bool:
    global _tool_cache
    if _tool_cache is None:
        return False
    ttl = settings.blender_tools_cache_ttl_s
    return (time.monotonic() - _tool_cache.fetched_at) < ttl


async def _async_fetch_tools(settings: Settings) -> list[dict[str, Any]]:
    async with _build_client(settings) as client:
        remote_tools = await client.list_tools()
        serialized = [_serialize_tool(tool) for tool in remote_tools]
        allowed_names = filter_tool_names([t["name"] for t in serialized], settings)
        allowed = {name for name in allowed_names}
        return [tool for tool in serialized if tool["name"] in allowed]


async def _async_health_check(settings: Settings) -> dict[str, Any]:
    url = resolve_blender_mcp_url(settings)
    if not settings.blender_enabled:
        return {
            "enabled": False,
            "connected": False,
            "url": url,
        }

    try:
        async with _build_client(settings) as client:
            remote_tools = await client.list_tools()
            serialized = [_serialize_tool(tool) for tool in remote_tools]
            allowed_names = filter_tool_names([t["name"] for t in serialized], settings)
            allowed = {name for name in allowed_names}
            filtered = [tool for tool in serialized if tool["name"] in allowed]
            sample = [tool["name"] for tool in filtered[:8]]
            return {
                "enabled": True,
                "connected": True,
                "url": url,
                "tool_count": len(serialized),
                "filtered_tool_count": len(filtered),
                "sample_tools": sample,
            }
    except Exception as exc:
        logger.warning("blender health_check failed: %s", exc)
        return {
            "enabled": True,
            "connected": False,
            "url": url,
            "error": str(exc),
        }


async def _async_list_tools(settings: Settings, *, use_cache: bool) -> dict[str, Any]:
    global _tool_cache

    if not settings.blender_enabled:
        return {"error": "Blender integration is disabled (blender.enabled=false)"}

    if use_cache and _cache_valid(settings):
        assert _tool_cache is not None
        return {
            "status": "ok",
            "cached": True,
            "count": len(_tool_cache.tools),
            "tools": list(_tool_cache.tools),
        }

    try:
        tools = await _async_fetch_tools(settings)
        _tool_cache = _ToolCache(fetched_at=time.monotonic(), tools=tools)
        return {
            "status": "ok",
            "cached": False,
            "count": len(tools),
            "tools": tools,
        }
    except Exception as exc:
        logger.warning("blender list_tools failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _async_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
    settings: Settings,
) -> dict[str, Any]:
    if not settings.blender_enabled:
        return {
            "success": False,
            "error": "Blender integration is disabled (blender.enabled=false)",
        }

    if not _tool_allowed(name, settings):
        return {
            "success": False,
            "error": f"Tool {name!r} is blocked by blender tool allowlist/denylist",
        }

    try:
        async with _build_client(settings) as client:
            result = await client.call_tool(name, arguments or {})
            text = _extract_text(result)
            payload = _serialize_call_result(result)
            return {
                "success": not bool(payload.get("isError")),
                "tool": name,
                "text": text,
                "result": payload,
            }
    except Exception as exc:
        logger.warning("blender call_tool(%s) failed: %s", name, exc)
        return {"success": False, "tool": name, "error": str(exc)}


def health_check(settings: Settings | None = None) -> dict[str, Any]:
    """Ping the Blender MCP server and return reachability metadata."""
    settings = settings or _load_settings()
    return _run_async(_async_health_check(settings))


def list_tools(
    settings: Settings | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """List tools exposed by the Blender MCP server."""
    settings = settings or _load_settings()
    return _run_async(_async_list_tools(settings, use_cache=use_cache))


def call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Invoke a Blender MCP tool by name with optional JSON arguments."""
    settings = settings or _load_settings()
    return _run_async(_async_call_tool(name, arguments, settings))


def format_status_report(settings: Settings | None = None) -> str:
    """Human-readable status for ``/blender status`` slash commands."""
    settings = settings or _load_settings()
    health = health_check(settings)
    lines = [
        "Blender MCP Status:",
        f"  Enabled: {health.get('enabled', settings.blender_enabled)}",
        f"  URL: {health.get('url', resolve_blender_mcp_url(settings))}",
        f"  Connected: {health.get('connected', False)}",
    ]
    if health.get("tool_count") is not None:
        lines.append(f"  Tool count: {health['tool_count']}")
    if health.get("sample_tools"):
        lines.append("  Sample tools:")
        for name in health["sample_tools"]:
            lines.append(f"    - {name}")
    if health.get("error"):
        lines.append(f"  Error: {health['error']}")

    if not settings.blender_enabled:
        lines.append(
            "  Hint: set blender.enabled: true in config.yaml to enable integration."
        )
    elif not health.get("connected"):
        lines.append(
            "  Hint: start the Blender MCP add-on/server, then retry /blender status."
        )

    return "\n".join(lines)


def format_status_message(settings: Settings | None = None) -> str:
    """Alias for :func:`format_status_report` (slash-command handlers)."""
    return format_status_report(settings)
