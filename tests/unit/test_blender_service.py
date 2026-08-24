"""Unit tests for Blender MCP service (mocked — no Blender required)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engineering_hub.blender import service as blender_service
from engineering_hub.config.settings import Settings


@pytest.fixture(autouse=True)
def _clear_tool_cache() -> None:
    blender_service._tool_cache = None


def _settings(**overrides: object) -> Settings:
    base = {
        "blender_enabled": True,
        "blender_mcp_url": "http://127.0.0.1:8765/mcp",
        "blender_tool_denylist": ["run_python_script"],
    }
    base.update(overrides)
    return Settings(**base)


def test_resolve_blender_mcp_url_from_settings() -> None:
    settings = _settings(blender_mcp_url="http://localhost:8400/mcp")
    assert blender_service.resolve_blender_mcp_url(settings) == "http://localhost:8400/mcp"


def test_filter_tool_names_respects_denylist() -> None:
    settings = _settings()
    names = blender_service.filter_tool_names(
        ["get_scene_info", "run_python_script", "list_objects"],
        settings,
    )
    assert names == ["get_scene_info", "list_objects"]


def test_filter_tool_names_respects_allowlist() -> None:
    settings = _settings(blender_tool_allowlist=["get_scene_info"])
    names = blender_service.filter_tool_names(
        ["get_scene_info", "list_objects"],
        settings,
    )
    assert names == ["get_scene_info"]


@patch("engineering_hub.blender.service._build_client")
def test_health_check_connected(mock_build_client: MagicMock) -> None:
    tool_a = MagicMock()
    tool_a.name = "get_scene_info"
    tool_b = MagicMock()
    tool_b.name = "run_python_script"
    client = AsyncMock()
    client.list_tools.return_value = [tool_a, tool_b]
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    mock_build_client.return_value = client

    result = blender_service.health_check(settings=_settings())
    assert result["connected"] is True
    assert result["tool_count"] == 2
    assert result["filtered_tool_count"] == 1
    assert "get_scene_info" in result["sample_tools"]


@patch("engineering_hub.blender.service._build_client")
def test_health_check_offline(mock_build_client: MagicMock) -> None:
    client = AsyncMock()
    client.list_tools.side_effect = ConnectionError("refused")
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    mock_build_client.return_value = client

    result = blender_service.health_check(settings=_settings())
    assert result["connected"] is False
    assert "refused" in result["error"]


@patch("engineering_hub.blender.service._build_client")
def test_list_tools_filters_and_caches(mock_build_client: MagicMock) -> None:
    tool = MagicMock()
    tool.name = "list_objects"
    tool.description = "List scene objects"
    tool.inputSchema = {"type": "object", "properties": {}}
    client = AsyncMock()
    client.list_tools.return_value = [tool]
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    mock_build_client.return_value = client

    settings = _settings()
    first = blender_service.list_tools(settings=settings)
    second = blender_service.list_tools(settings=settings)

    assert first["cached"] is False
    assert second["cached"] is True
    assert first["tools"][0]["name"] == "list_objects"
    assert client.list_tools.await_count == 1


@patch("engineering_hub.blender.service._build_client")
def test_call_tool_success(mock_build_client: MagicMock) -> None:
    result_obj = MagicMock()
    result_obj.model_dump.return_value = {
        "isError": False,
        "content": [{"type": "text", "text": "Scene has 3 objects"}],
    }
    client = AsyncMock()
    client.call_tool.return_value = result_obj
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    mock_build_client.return_value = client

    payload = blender_service.call_tool(
        "get_scene_info",
        {"detail": True},
        settings=_settings(),
    )
    assert payload["success"] is True
    assert "3 objects" in payload["text"]


def test_call_tool_blocked_by_denylist() -> None:
    payload = blender_service.call_tool(
        "run_python_script",
        {"code": "print(1)"},
        settings=_settings(),
    )
    assert payload["success"] is False
    assert "blocked" in payload["error"]


def test_health_check_disabled() -> None:
    result = blender_service.health_check(settings=_settings(blender_enabled=False))
    assert result["enabled"] is False
    assert result["connected"] is False
