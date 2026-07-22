"""Tests for Journaler model profile resolution and chat template compatibility."""

from __future__ import annotations

import pytest

from engineering_hub.config.settings import Settings
from engineering_hub.journaler.constants import DEFAULT_JOURNALER_MLX_MODEL_ID
from engineering_hub.journaler.engine import ConversationalMLXBackend
from engineering_hub.journaler.model_profiles import (
    DEFAULT_THINKING_MAX_TOKENS,
    JournalerChatModelContext,
    JournalerModelSpec,
    _apply_model_set_command,
    effective_generation_max_tokens,
    ensure_spec_model_path,
    journaler_model_display_label,
    model_status_data,
    parse_model_slash_message,
    resolve_journaler_model_spec,
    resolve_journaler_model_spec_for_slash,
)


def test_resolve_legacy_path_only() -> None:
    s = Settings()
    s.journaler_model_path = "mlx-community/custom"
    s.journaler_model_context_window = 8192
    spec = resolve_journaler_model_spec(s)
    assert spec.model_path == "mlx-community/custom"
    assert spec.model_context_window == 8192
    assert spec.profile_name is None


def test_resolve_models_map_default_profile() -> None:
    s = Settings()
    s.journaler_model_profile = "fast"
    s.journaler_models = {
        "fast": {
            "model_path": "mlx-community/fast-model",
            "model_context_window": 16384,
            "enable_thinking": False,
        }
    }
    spec = resolve_journaler_model_spec(s)
    assert spec.model_path == "mlx-community/fast-model"
    assert spec.model_context_window == 16384
    assert spec.enable_thinking is False
    assert spec.profile_name == "fast"


def test_resolve_cli_profile_overrides_named_default() -> None:
    s = Settings()
    s.journaler_model_profile = "fast"
    s.journaler_models = {
        "fast": {"model_path": "hub/fast"},
        "reasoning": {
            "model_path": "hub/reasoning",
            "enable_thinking": True,
            "temp": 0.6,
        },
    }
    spec = resolve_journaler_model_spec(s, cli_profile="reasoning")
    assert spec.model_path == "hub/reasoning"
    assert spec.enable_thinking is True
    assert abs(spec.temp - 0.6) < 1e-6


def test_resolve_cli_model_highest_precedence() -> None:
    s = Settings()
    s.journaler_models = {"fast": {"model_path": "hub/fast"}}
    spec = resolve_journaler_model_spec(
        s, cli_model="hub/override", cli_profile="fast"
    )
    assert spec.model_path == "hub/override"


def test_unknown_profile_raises() -> None:
    s = Settings()
    s.journaler_models = {"a": {"model_path": "hub/a"}}
    with pytest.raises(ValueError, match="Unknown"):
        resolve_journaler_model_spec(s, cli_profile="missing")


def test_ensure_spec_model_path() -> None:
    s = JournalerModelSpec(model_path="")
    out = ensure_spec_model_path(s, DEFAULT_JOURNALER_MLX_MODEL_ID)
    assert out.model_path == DEFAULT_JOURNALER_MLX_MODEL_ID


def test_journaler_model_display_label() -> None:
    assert (
        journaler_model_display_label(
            JournalerModelSpec(model_path="mlx-community/Qwen3.6-35B-A3B-4bit")
        )
        == "Qwen3.6-35B-A3B-4bit"
    )
    assert (
        journaler_model_display_label(
            JournalerModelSpec(
                model_path="mlx-community/gemma-4-31b-it-8bit",
                profile_name="default",
            )
        )
        == "default"
    )


def test_parse_model_slash_message() -> None:
    assert parse_model_slash_message("/model") == ("status", None, None)
    assert parse_model_slash_message("/model path  hub/x  ") == ("path", None, "hub/x")
    assert parse_model_slash_message("/model reasoning") == ("profile", "reasoning", None)


def test_resolve_slash_path() -> None:
    s = Settings()
    defaults = JournalerModelSpec(
        model_path="old",
        model_context_window=32768,
        max_tokens=1000,
        temp=0.5,
        top_p=0.8,
        min_p=0.1,
        repetition_penalty=1.0,
    )
    spec = resolve_journaler_model_spec_for_slash(
        s, raw_path="mlx-community/new", current_defaults=defaults
    )
    assert spec.model_path == "mlx-community/new"
    assert spec.max_tokens == 1000


def test_apply_chat_template_safe_falls_back_without_enable_thinking() -> None:
    backend = object.__new__(ConversationalMLXBackend)
    backend._enable_thinking = True

    class _Tok:
        pass

    def apply_template(
        messages: list[dict[str, str]], **kwargs: object
    ) -> str:
        if kwargs.get("enable_thinking") is not None:
            raise TypeError("unexpected keyword")
        return "prompt"

    tokenizer = _Tok()
    tokenizer.apply_chat_template = apply_template
    backend._tokenizer = tokenizer

    messages = [{"role": "user", "content": "hi"}]
    prompt = backend._apply_chat_template_safe(messages)
    assert prompt == "prompt"


def test_journaler_model_display_label() -> None:
    assert (
        journaler_model_display_label(
            JournalerModelSpec(model_path="mlx-community/Qwen3.6-35B-A3B-4bit")
        )
        == "Qwen3.6-35B-A3B-4bit"
    )
    assert (
        journaler_model_display_label(
            JournalerModelSpec(
                model_path="mlx-community/gemma-4-31b-it-8bit",
                profile_name="default",
            )
        )
        == "default"
    )


def test_journaler_chat_model_context_dataclass() -> None:
    s = Settings()
    spec = JournalerModelSpec(model_path="hub/x")
    ctx = JournalerChatModelContext(s, spec)
    assert ctx.spec.model_path == "hub/x"


def test_effective_generation_max_tokens_non_thinking() -> None:
    spec = JournalerModelSpec(model_path="hub/x", max_tokens=4096, enable_thinking=False)
    assert effective_generation_max_tokens(spec) == 4096


def test_effective_generation_max_tokens_thinking_floor() -> None:
    spec = JournalerModelSpec(model_path="hub/x", max_tokens=4096, enable_thinking=True)
    assert effective_generation_max_tokens(spec) == DEFAULT_THINKING_MAX_TOKENS


def test_effective_generation_max_tokens_thinking_override() -> None:
    spec = JournalerModelSpec(
        model_path="hub/x",
        max_tokens=4096,
        enable_thinking=True,
        thinking_max_tokens=8192,
    )
    assert effective_generation_max_tokens(spec) == 8192


def test_effective_generation_max_tokens_thinking_high_base() -> None:
    spec = JournalerModelSpec(model_path="hub/x", max_tokens=20000, enable_thinking=True)
    assert effective_generation_max_tokens(spec) == 20000


def test_resolve_profile_thinking_max_tokens() -> None:
    s = Settings()
    s.journaler_model_profile = "reasoning"
    s.journaler_models = {
        "reasoning": {
            "model_path": "hub/reasoning",
            "enable_thinking": True,
            "thinking_max_tokens": 8192,
        }
    }
    spec = resolve_journaler_model_spec(s)
    assert spec.thinking_max_tokens == 8192
    assert effective_generation_max_tokens(spec) == 8192


def test_model_status_shows_effective_when_thinking_on() -> None:
    spec = JournalerModelSpec(model_path="hub/x", max_tokens=4096, enable_thinking=True)
    rows = {name: value for name, value, _ in model_status_data(spec)}
    assert rows["max_tokens"] == "4096"
    assert rows["effective max_tokens"] == f"{DEFAULT_THINKING_MAX_TOKENS:,}"


def test_apply_model_set_thinking_on_updates_engine_max_tokens() -> None:
    class _Engine:
        def __init__(self) -> None:
            self._backend = type("_B", (), {"set_enable_thinking": lambda self, v: None})()
            self.updated: int | None = None

        def update_max_tokens(self, value: int) -> None:
            self.updated = value

    spec = JournalerModelSpec(model_path="hub/x", max_tokens=4096, enable_thinking=False)
    engine = _Engine()
    new_spec, msg = _apply_model_set_command("thinking", "on", spec, engine)
    assert new_spec.enable_thinking is True
    assert engine.updated == DEFAULT_THINKING_MAX_TOKENS
    assert "effective max_tokens" in msg


def test_apply_model_set_max_tokens_while_thinking() -> None:
    class _Engine:
        def __init__(self) -> None:
            self.updated: int | None = None

        def update_max_tokens(self, value: int) -> None:
            self.updated = value

    spec = JournalerModelSpec(model_path="hub/x", max_tokens=4096, enable_thinking=True)
    engine = _Engine()
    new_spec, msg = _apply_model_set_command("max_tokens", "8192", spec, engine)
    assert new_spec.thinking_max_tokens == 8192
    assert new_spec.max_tokens == 8192
    assert engine.updated == 8192
    assert "effective" in msg
