"""Unit tests for OpenAIMLXServerBackend (mocked HTTP — no mlx_lm.server required)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from engineering_hub.agents.mlx_server import (
    OpenAIMLXServerBackend,
    _normalize_arguments,
)
from engineering_hub.core.exceptions import LLMBackendError
from engineering_hub.journaler.model_profiles import (
    JournalerModelSpec,
    build_journaler_mlx_backend,
)
from engineering_hub.config.settings import Settings


def _backend(**kwargs: object) -> OpenAIMLXServerBackend:
    defaults = {
        "base_url": "http://127.0.0.1:8081/v1",
        "model": "mlx-community/test-model",
    }
    defaults.update(kwargs)
    return OpenAIMLXServerBackend(**defaults)  # type: ignore[arg-type]


def test_normalize_arguments_json_string() -> None:
    assert _normalize_arguments('{"a": 1}') == {"a": 1}
    assert _normalize_arguments({"b": 2}) == {"b": 2}
    assert _normalize_arguments("not-json") == {"_raw": "not-json"}


@patch("engineering_hub.agents.mlx_server.requests.post")
def test_complete_with_tools_parses_openai_tool_calls(mock_post: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "blender_health",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
            }
        ]
    }
    mock_post.return_value = mock_resp

    result = _backend().complete_with_tools(
        system="sys",
        messages=[{"role": "user", "content": "check blender"}],
        tools=[
            {
                "name": "blender_health",
                "description": "health",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        max_tokens=256,
    )
    assert result.stop_reason == "tool_use"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "blender_health"
    assert result.tool_calls[0].arguments == {}


@patch("engineering_hub.agents.mlx_server.requests.post")
def test_chat_composes_reasoning_and_content(mock_post: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "reasoning": "step by step",
                    "content": "final answer",
                },
            }
        ]
    }
    mock_post.return_value = mock_resp

    text = _backend().chat(
        [{"role": "user", "content": "hi"}],
        max_tokens=64,
    )
    assert "<think>" in text
    assert "step by step" in text
    assert "final answer" in text


@patch("engineering_hub.agents.mlx_server.requests.post")
def test_connection_error_is_actionable(mock_post: MagicMock) -> None:
    import requests

    mock_post.side_effect = requests.ConnectionError("refused")
    with pytest.raises(LLMBackendError) as exc:
        _backend().chat([{"role": "user", "content": "hi"}], max_tokens=16)
    assert "mlx_lm.server" in str(exc.value)


def test_factory_uses_server_when_enabled() -> None:
    settings = Settings(
        mlx_server_enabled=True,
        mlx_server_base_url="http://127.0.0.1:8081/v1",
    )
    spec = JournalerModelSpec(model_path="mlx-community/Qwen3-test")
    with patch(
        "engineering_hub.agents.mlx_server.probe_mlx_server",
        return_value=True,
    ):
        backend = build_journaler_mlx_backend(spec, settings=settings)
    assert isinstance(backend, OpenAIMLXServerBackend)
    assert backend.model == "mlx-community/Qwen3-test"


def test_factory_falls_back_when_server_unreachable() -> None:
    settings = Settings(
        mlx_server_enabled=True,
        mlx_server_base_url="http://127.0.0.1:8081/v1",
    )
    spec = JournalerModelSpec(model_path="mlx-community/Qwen3-test")
    with patch(
        "engineering_hub.agents.mlx_server.probe_mlx_server",
        return_value=False,
    ), patch(
        "engineering_hub.journaler.engine.ConversationalMLXBackend.__init__",
        return_value=None,
    ) as mock_init:
        backend = build_journaler_mlx_backend(spec, settings=settings)
        mock_init.assert_called_once()
        assert not isinstance(backend, OpenAIMLXServerBackend)


def test_factory_keeps_in_process_for_vlm() -> None:
    settings = Settings(mlx_server_enabled=True)
    spec = JournalerModelSpec(
        model_path="mlx-community/gemma-vlm",
        mlx_backend="mlx-vlm",
    )
    # Avoid actually loading VLM weights — detect path selection only.
    with patch(
        "engineering_hub.journaler.engine.ConversationalMLXBackend.__init__",
        return_value=None,
    ) as mock_init:
        backend = build_journaler_mlx_backend(spec, settings=settings)
        mock_init.assert_called_once()
        assert not isinstance(backend, OpenAIMLXServerBackend)


@patch("engineering_hub.agents.mlx_server.requests.post")
def test_stream_generate_yields_content_deltas(mock_post: MagicMock) -> None:
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        "data: [DONE]",
    ]
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.iter_lines.return_value = iter(lines)
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_post.return_value = mock_resp

    chunks = list(_backend().stream_generate([{"role": "user", "content": "hi"}], 32))
    assert "".join(chunks) == "Hello"
