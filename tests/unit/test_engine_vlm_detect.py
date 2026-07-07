"""Tests for Journaler mlx-lm vs mlx-vlm backend auto-detection."""

from __future__ import annotations

import json
from pathlib import Path

from engineering_hub.journaler.engine import _detect_vlm


def _write_config(tmp_path: Path, payload: dict) -> str:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    return str(model_dir)


def test_detect_vlm_gemma4_uses_mlx_vlm(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "model_type": "gemma4",
            "vision_config": {"hidden_size": 128},
            "architectures": ["Gemma4ForConditionalGeneration"],
        },
    )
    assert _detect_vlm(path) is True


def test_detect_vlm_qwen3_5_moe_with_vision_config_uses_mlx_lm(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "model_type": "qwen3_5_moe",
            "vision_config": {"hidden_size": 128},
            "architectures": ["Qwen3_5MoeForConditionalGeneration"],
        },
    )
    assert _detect_vlm(path) is False


def test_detect_vlm_text_qwen3_moe(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "model_type": "qwen3_moe",
            "architectures": ["Qwen3MoeForCausalLM"],
        },
    )
    assert _detect_vlm(path) is False


def test_detect_vlm_qwen2_vl(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {"model_type": "qwen2_vl", "architectures": ["Qwen2VLForConditionalGeneration"]},
    )
    assert _detect_vlm(path) is True
