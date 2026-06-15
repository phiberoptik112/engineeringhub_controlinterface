"""Tests for Journaler MLX model catalog discovery and normalization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engineering_hub.config.settings import Settings
from engineering_hub.journaler.model_catalog import (
    ModelCatalogEntry,
    build_model_catalog,
    cache_dir_name_to_repo_id,
    discover_configured_profiles,
    get_hf_hub_cache_roots,
    normalize_model_path_input,
)
from engineering_hub.journaler.model_profiles import JournalerModelSpec


def test_cache_dir_name_to_repo_id() -> None:
    assert (
        cache_dir_name_to_repo_id("models--mlx-community--Qwen3.6-35B-A3B-4bit")
        == "mlx-community/Qwen3.6-35B-A3B-4bit"
    )
    assert cache_dir_name_to_repo_id("not-a-cache-dir") is None


def test_normalize_model_path_input_cache_dir_name() -> None:
    assert (
        normalize_model_path_input("models--mlx-community--Qwen3.6-35B-A3B-4bit")
        == "mlx-community/Qwen3.6-35B-A3B-4bit"
    )


def test_normalize_model_path_input_repo_id_unchanged() -> None:
    assert (
        normalize_model_path_input("mlx-community/Qwen3-32B-8bit")
        == "mlx-community/Qwen3-32B-8bit"
    )


def test_normalize_model_path_input_local_dir(tmp_path: Path) -> None:
    model_dir = tmp_path / "my-model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    assert normalize_model_path_input(str(model_dir)) == str(model_dir.resolve())


def test_normalize_model_path_input_snapshot_path(tmp_path: Path) -> None:
    snap = (
        tmp_path
        / "models--mlx-community--demo"
        / "snapshots"
        / "abc123"
    )
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    assert normalize_model_path_input(str(snap)) == str(snap.resolve())


def test_get_hf_hub_cache_roots_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-hub"
    custom.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(custom))
    roots = get_hf_hub_cache_roots()
    assert custom.resolve() in roots


def test_discover_configured_profiles() -> None:
    s = Settings()
    s.journaler_models = {
        "fast": {"model_path": "mlx-community/fast"},
        "reasoning": {"model_path": "mlx-community/reasoning", "enable_thinking": True},
    }
    entries = discover_configured_profiles(s)
    names = {e.profile_name for e in entries}
    assert names == {"fast", "reasoning"}
    assert all(e.source == "profile" for e in entries)


def test_build_model_catalog_dedupes_profile_and_cache() -> None:
    s = Settings()
    s.journaler_models = {
        "default": {"model_path": "mlx-community/gemma-4-31b-it-8bit"},
    }
    active = JournalerModelSpec(
        model_path="mlx-community/gemma-4-31b-it-8bit",
        profile_name="default",
    )
    cached_entry = ModelCatalogEntry(
        label="gemma-4-31b-it-8bit",
        load_value="mlx-community/gemma-4-31b-it-8bit",
        source="hf_cache",
        size_bytes=1000,
    )
    other_entry = ModelCatalogEntry(
        label="Qwen3.6-35B-A3B-4bit",
        load_value="mlx-community/Qwen3.6-35B-A3B-4bit",
        source="hf_cache",
        size_bytes=2000,
    )
    with patch(
        "engineering_hub.journaler.model_catalog.discover_cached_mlx_models",
        return_value=[cached_entry, other_entry],
    ):
        catalog = build_model_catalog(s, active)
    load_values = [e.load_value for e in catalog]
    assert load_values.count("mlx-community/gemma-4-31b-it-8bit") == 1
    assert "mlx-community/Qwen3.6-35B-A3B-4bit" in load_values
    active_entries = [e for e in catalog if e.is_active]
    assert len(active_entries) == 1
    assert active_entries[0].profile_name == "default"


def test_resolve_slash_path_normalizes_cache_dir_name() -> None:
    from engineering_hub.journaler.model_profiles import resolve_journaler_model_spec_for_slash

    s = Settings()
    defaults = JournalerModelSpec(model_path="old")
    spec = resolve_journaler_model_spec_for_slash(
        s,
        raw_path="models--mlx-community--Qwen3.6-35B-A3B-4bit",
        current_defaults=defaults,
    )
    assert spec.model_path == "mlx-community/Qwen3.6-35B-A3B-4bit"


def test_discover_cached_mlx_models_filters_prefix(tmp_path: Path) -> None:
    from engineering_hub.journaler.model_catalog import discover_cached_mlx_models

    repo_mlx = MagicMock()
    repo_mlx.repo_type = "model"
    repo_mlx.repo_id = "mlx-community/demo"
    repo_mlx.size_on_disk = 100
    repo_mlx.revisions = []

    repo_other = MagicMock()
    repo_other.repo_type = "model"
    repo_other.repo_id = "sentence-transformers/all-MiniLM-L6-v2"
    repo_other.size_on_disk = 50
    repo_other.revisions = []

    cache_info = MagicMock()
    cache_info.repos = frozenset([repo_mlx, repo_other])

    hub_root = tmp_path / "hub"
    hub_root.mkdir()
    with patch(
        "engineering_hub.journaler.model_catalog.get_hf_hub_cache_roots",
        return_value=[hub_root],
    ), patch(
        "huggingface_hub.scan_cache_dir",
        return_value=cache_info,
    ):
        entries = discover_cached_mlx_models()

    assert len(entries) == 1
    assert entries[0].load_value == "mlx-community/demo"
