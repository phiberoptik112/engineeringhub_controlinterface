"""Tests for TOOL_USE agents failing closed when tools are unavailable."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from engineering_hub.agents.registry import ModelClass
from engineering_hub.agents.worker import AgentWorker
from engineering_hub.core.constants import AgentType
from engineering_hub.core.models import ParsedTask, TaskStatus


class _NoToolsBackend:
    """Backend that advertises complete_with_tools but cannot run them."""

    def complete_with_tools(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError(
            "MLX backend does not support tool calling; "
            "enable mlx_server.enabled and run mlx_lm.server"
        )

    def complete(self, system: str, user_message: str, max_tokens: int) -> str:
        return "should-not-be-used"


def test_tool_use_agent_fails_when_backend_lacks_tools(tmp_path: Path) -> None:
    worker = AgentWorker(
        backend=_NoToolsBackend(),  # type: ignore[arg-type]
        output_dir=tmp_path,
        prompts_dir=Path("prompts"),
    )
    cfg = worker._registry.get_config(AgentType.BLENDER)
    assert cfg is not None
    assert cfg.model_class == ModelClass.TOOL_USE
    assert cfg.tools

    task = ParsedTask(
        agent="blender",
        status=TaskStatus.PENDING,
        project_id=None,
        description="summarize scene",
        start_line=0,
        end_line=0,
        raw_block="@blender: summarize scene",
    )

    with patch.object(worker, "_prompt_loader") as mock_loader:
        mock_loader.get_prompt.return_value = "system"
        result = worker.execute(task, context="")

    assert result.success is False
    assert result.error_message is not None
    assert "mlx_server.enabled" in result.error_message
    assert "tool" in result.error_message.lower()
