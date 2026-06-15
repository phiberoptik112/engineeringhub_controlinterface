"""Tests for Journaler MLX backend (mlx-lm vs mlx-vlm) auto-detection."""

from __future__ import annotations

import json
from pathlib import Path

from engineering_hub.journaler.engine import _detect_vlm


def test_detect_vlm_qwen3_5_moe_with_vision_config_is_not_vlm(tmp_path: Path) -> None:
    """Qwen3.6 ships vision_config but journaler chat uses mlx-lm for it."""
    model_dir = tmp_path / "qwen36"
    model_dir.mkdir()
    config = {
        "model_type": "qwen3_5_moe",
        "architectures": ["Qwen3_5MoeForConditionalGeneration"],
        "vision_config": {"model_type": "qwen3_5_moe", "hidden_size": 1152},
    }
    (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    assert _detect_vlm(str(model_dir)) is False


def test_detect_vlm_gemma4_is_vlm(tmp_path: Path) -> None:
    model_dir = tmp_path / "gemma4"
    model_dir.mkdir()
    config = {
        "model_type": "gemma4",
        "architectures": ["Gemma4ForConditionalGeneration"],
        "vision_config": {"hidden_size": 1152},
    }
    (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    assert _detect_vlm(str(model_dir)) is True


def test_detect_vlm_qwen3_moe_causal_is_not_vlm(tmp_path: Path) -> None:
    model_dir = tmp_path / "qwen3"
    model_dir.mkdir()
    config = {
        "model_type": "qwen3_moe",
        "architectures": ["Qwen3MoeForCausalLM"],
    }
    (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    assert _detect_vlm(str(model_dir)) is False
