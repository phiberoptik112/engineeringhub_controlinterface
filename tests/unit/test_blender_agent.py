"""Integration tests for Blender agent registration and tool handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from engineering_hub.agents.registry import DEFAULT_AGENT_CONFIGS
from engineering_hub.agents.tools import TOOL_REGISTRY, resolve_tools
from engineering_hub.core.constants import AGENT_PROMPT_FILES, AgentType
from engineering_hub.journaler.delegator import _AGENT_ALIASES

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_blender_tools_registered() -> None:
    for name in ("blender_health", "blender_list_tools", "blender_execute"):
        assert name in TOOL_REGISTRY


def test_blender_agent_config_tools_all_resolve() -> None:
    cfg = DEFAULT_AGENT_CONFIGS[AgentType.BLENDER]
    resolved = resolve_tools(cfg.tools)
    assert len(resolved) == len(cfg.tools)
    assert "blender_execute" in cfg.tools
    assert "blender_call_tool" not in cfg.tools


def test_blender_prompt_file_exists() -> None:
    prompt = REPO_ROOT / "prompts" / AGENT_PROMPT_FILES[AgentType.BLENDER]
    assert prompt.is_file()
    text = prompt.read_text(encoding="utf-8")
    assert "blender_execute" in text
    assert "Lab MCP" in text


def test_blender_aliases_resolve() -> None:
    for alias in ("blender", "3d", "blender-mcp"):
        assert _AGENT_ALIASES[alias] == "blender"


@patch("engineering_hub.blender.service.health_check")
def test_blender_health_handler(mock_health: object) -> None:
    mock_health.return_value = {"connected": True, "blender_version": "5.1.0"}
    handler = TOOL_REGISTRY["blender_health"].handler
    result = handler({}, None)
    assert "connected" in result
    mock_health.assert_called_once()


@patch("engineering_hub.blender.service.execute")
def test_blender_execute_handler(mock_execute: object) -> None:
    mock_execute.return_value = {"success": True, "result": {"ok": True}}
    handler = TOOL_REGISTRY["blender_execute"].handler
    result = handler({"code": "result = {'ok': True}", "strict_json": True}, None)
    assert "success" in result
    mock_execute.assert_called_once_with(
        "result = {'ok': True}",
        strict_json=True,
    )
