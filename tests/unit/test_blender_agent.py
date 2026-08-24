"""Integration tests for Blender agent registration and tool handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from engineering_hub.agents.registry import DEFAULT_AGENT_CONFIGS
from engineering_hub.agents.tools import TOOL_REGISTRY, resolve_tools
from engineering_hub.core.constants import AGENT_PROMPT_FILES, AgentType
from engineering_hub.journaler.delegator import _AGENT_ALIASES

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_blender_tools_registered() -> None:
    for name in ("blender_health", "blender_list_tools", "blender_call_tool"):
        assert name in TOOL_REGISTRY


def test_blender_agent_config_tools_all_resolve() -> None:
    cfg = DEFAULT_AGENT_CONFIGS[AgentType.BLENDER]
    resolved = resolve_tools(cfg.tools)
    assert len(resolved) == len(cfg.tools)


def test_blender_prompt_file_exists() -> None:
    prompt = REPO_ROOT / "prompts" / AGENT_PROMPT_FILES[AgentType.BLENDER]
    assert prompt.is_file()


def test_blender_aliases_resolve() -> None:
    for alias in ("blender", "3d", "blender-mcp"):
        assert _AGENT_ALIASES[alias] == "blender"


@patch("engineering_hub.blender.service.health_check")
def test_blender_health_handler(mock_health: object) -> None:
    mock_health.return_value = {"connected": True, "tool_count": 3}
    handler = TOOL_REGISTRY["blender_health"].handler
    result = handler({}, None)
    assert "connected" in result
    mock_health.assert_called_once()
