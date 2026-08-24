"""Integration tests for horn-iterator agent registration and tool handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from engineering_hub.agents.registry import DEFAULT_AGENT_CONFIGS
from engineering_hub.agents.tools import TOOL_REGISTRY, resolve_tools
from engineering_hub.core.constants import AGENT_PROMPT_FILES, AgentType
from engineering_hub.journaler.delegator import _AGENT_ALIASES

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_horn_tools_registered() -> None:
    for name in (
        "horn_get_defaults",
        "horn_evaluate_design",
        "horn_run_sweep",
        "horn_export_results",
    ):
        assert name in TOOL_REGISTRY


def test_horn_agent_config_tools_all_resolve() -> None:
    cfg = DEFAULT_AGENT_CONFIGS[AgentType.HORN_ITERATOR]
    resolved = resolve_tools(cfg.tools)
    assert len(resolved) == len(cfg.tools)


def test_horn_prompt_file_exists() -> None:
    prompt = REPO_ROOT / "prompts" / AGENT_PROMPT_FILES[AgentType.HORN_ITERATOR]
    assert prompt.is_file()


def test_horn_skill_file_exists() -> None:
    assert (REPO_ROOT / "skills" / "horn-iterator.yaml").is_file()


def test_horn_aliases_resolve() -> None:
    for alias in ("horn", "horn-iterator", "horn-sweep", "waveguide"):
        assert _AGENT_ALIASES[alias] == "horn-iterator"


@patch("engineering_hub.horn_iterator.service.get_defaults")
def test_horn_get_defaults_handler(mock_defaults: object) -> None:
    mock_defaults.return_value = {"constraints": {"freq_min_hz": 250.0}, "bounds": {}}
    handler = TOOL_REGISTRY["horn_get_defaults"].handler
    result = handler({}, None)
    assert "freq_min_hz" in result
    mock_defaults.assert_called_once()


def test_horn_evaluate_design_handler() -> None:
    handler = TOOL_REGISTRY["horn_evaluate_design"].handler
    result = handler(
        {"l_exp_mm": 130, "mouth_w_mm": 120, "mouth_h_mm": 80}, None
    )
    payload = json.loads(result)
    assert payload["status"] in ("PASS", "FAIL")
    assert "fc_hz" in payload
