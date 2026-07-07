"""Discover MLX models from HF cache, config profiles, and local paths."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from engineering_hub.config.settings import Settings
    from engineering_hub.journaler.model_profiles import JournalerModelSpec

logger = logging.getLogger(__name__)

ModelSource = Literal["profile", "hf_cache", "local_path"]

_MLX_COMMUNITY_PREFIX = "mlx-community/"
_CACHE_DIR_RE = re.compile(r"^models--(.+)$")


@dataclass(frozen=True)
class ModelCatalogEntry:
    """A selectable MLX model in browse/picker UIs."""

    label: str
    load_value: str
    source: ModelSource
    size_bytes: int = 0
    profile_name: str | None = None
    is_active: bool = False


def get_hf_hub_cache_roots() -> list[Path]:
    """Return unique Hugging Face hub cache directories to scan."""
    roots: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)

    env_hub = os.environ.get("HF_HUB_CACHE", "").strip()
    if env_hub:
        _add(Path(env_hub))

    try:
        from huggingface_hub.constants import HF_HOME, HF_HUB_CACHE

        _add(Path(HF_HUB_CACHE))
        hf_home_hub = Path(HF_HOME) / "hub"
        _add(hf_home_hub)
    except Exception:
        _add(Path.home() / ".cache" / "huggingface" / "hub")

    return roots


def cache_dir_name_to_repo_id(cache_dir_name: str) -> str | None:
    """Convert ``models--org--name`` to ``org/name``."""
    name = cache_dir_name.strip()
    match = _CACHE_DIR_RE.match(name)
    if not match:
        return None
    body = match.group(1)
    parts = body.split("--", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


def normalize_model_path_input(raw: str) -> str:
    """Normalize user input into a loadable HF repo id or local snapshot path."""
    text = raw.strip()
    if not text:
        return text

    expanded = Path(text).expanduser()
    if expanded.is_dir():
        return str(expanded.resolve())

    if "/" not in text and text.startswith("models--"):
        repo_id = cache_dir_name_to_repo_id(text)
        if repo_id:
            return repo_id

    path = expanded
    parts = path.parts
    for i, part in enumerate(parts):
        if part.startswith("models--") and i + 2 < len(parts) and parts[i + 1] == "snapshots":
            if path.is_dir():
                return str(path.resolve())
            return text

    for i, part in enumerate(parts):
        if part.startswith("models--"):
            cache_root = Path(*parts[: i + 1])
            if cache_root.is_dir():
                snapshots = cache_root / "snapshots"
                if snapshots.is_dir():
                    rev_dirs = sorted(
                        (d for d in snapshots.iterdir() if d.is_dir()),
                        key=lambda d: d.stat().st_mtime,
                        reverse=True,
                    )
                    if rev_dirs:
                        return str(rev_dirs[0].resolve())
            repo_id = cache_dir_name_to_repo_id(part)
            if repo_id:
                return repo_id

    return text


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1_048_576:
        return f"{size_bytes / 1024:.0f} KB"
    if size_bytes < 1_073_741_824:
        return f"{size_bytes / 1_048_576:.1f} GB"
    return f"{size_bytes / 1_073_741_824:.1f} GB"


def discover_cached_mlx_models() -> list[ModelCatalogEntry]:
    """Scan HF hub caches for mlx-community model repos."""
    entries: list[ModelCatalogEntry] = []
    seen: set[str] = set()

    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        logger.debug("huggingface_hub not installed; skipping cache scan")
        return entries

    for root in get_hf_hub_cache_roots():
        if not root.is_dir():
            continue
        try:
            cache_info = scan_cache_dir(root)
        except Exception as exc:
            logger.debug("scan_cache_dir failed for %s: %s", root, exc)
            continue

        for repo in cache_info.repos:
            if getattr(repo, "repo_type", "model") != "model":
                continue
            repo_id = repo.repo_id
            if not repo_id.startswith(_MLX_COMMUNITY_PREFIX):
                continue
            if repo_id in seen:
                continue
            seen.add(repo_id)

            load_value = repo_id
            short_name = repo_id.split("/", 1)[-1]
            entries.append(
                ModelCatalogEntry(
                    label=short_name,
                    load_value=load_value,
                    source="hf_cache",
                    size_bytes=int(getattr(repo, "size_on_disk", 0) or 0),
                )
            )

    entries.sort(key=lambda e: (-e.size_bytes, e.label.lower()))
    return entries


def discover_configured_profiles(settings: Settings) -> list[ModelCatalogEntry]:
    """Build catalog entries from ``journaler.models`` in config."""
    from engineering_hub.journaler.model_profiles import _legacy_base_spec, _spec_from_profile_dict

    models = getattr(settings, "journaler_models", None) or {}
    if not models:
        return []

    base = _legacy_base_spec(settings)
    entries: list[ModelCatalogEntry] = []
    for name in sorted(models.keys()):
        data = models[name]
        if not isinstance(data, dict):
            continue
        try:
            spec = _spec_from_profile_dict(name, data, base)
        except ValueError:
            continue
        short = spec.model_path.split("/")[-1] if "/" in spec.model_path else spec.model_path
        entries.append(
            ModelCatalogEntry(
                label=short,
                load_value=spec.model_path,
                source="profile",
                profile_name=name,
            )
        )
    return entries


def _is_active_entry(entry: ModelCatalogEntry, active_spec: JournalerModelSpec | None) -> bool:
    if active_spec is None:
        return False
    active_path = normalize_model_path_input(active_spec.model_path)
    entry_path = normalize_model_path_input(entry.load_value)
    if active_path == entry_path:
        return True
    if entry.profile_name and entry.profile_name == active_spec.profile_name:
        return True
    return False


def build_model_catalog(
    settings: Settings,
    active_spec: JournalerModelSpec | None = None,
) -> list[ModelCatalogEntry]:
    """Merge configured profiles and cached mlx-community models."""
    profiles = discover_configured_profiles(settings)
    cached = discover_cached_mlx_models()

    by_load: dict[str, ModelCatalogEntry] = {}
    ordered: list[ModelCatalogEntry] = []

    for entry in profiles:
        key = normalize_model_path_input(entry.load_value)
        if key not in by_load:
            by_load[key] = entry
            ordered.append(entry)

    for entry in cached:
        key = normalize_model_path_input(entry.load_value)
        if key in by_load:
            continue
        by_load[key] = entry
        ordered.append(entry)

    result: list[ModelCatalogEntry] = []
    for entry in ordered:
        is_active = _is_active_entry(entry, active_spec)
        if is_active:
            entry = replace(entry, is_active=True)
        result.append(entry)

    return result


def format_catalog_entry_line(entry: ModelCatalogEntry, width: int = 72) -> str:
    """Format one catalog row for curses/TUI display."""
    parts: list[str] = []
    if entry.is_active:
        parts.append("ACTIVE")
    if entry.profile_name:
        parts.append(f"profile:{entry.profile_name}")
    elif entry.source == "hf_cache":
        parts.append("cached")

    prefix = " ".join(parts)
    name = entry.label
    size = _format_size(entry.size_bytes) if entry.size_bytes else ""
    line = f"{name}"
    if size:
        line += f"  ({size})"
    if prefix:
        line = f"[{prefix}] {line}"
    if len(line) > width:
        line = line[: width - 1] + "…"
    return line


def resolve_catalog_entry_spec(
    entry: ModelCatalogEntry,
    settings: Settings,
    current_defaults: JournalerModelSpec | None = None,
):
    """Resolve a catalog selection to a :class:`JournalerModelSpec`."""
    from engineering_hub.journaler.model_profiles import resolve_journaler_model_spec_for_slash

    if entry.source == "profile" and entry.profile_name:
        return resolve_journaler_model_spec_for_slash(
            settings,
            profile_name=entry.profile_name,
            current_defaults=current_defaults,
        )

    from engineering_hub.journaler.engine import _detect_vlm

    normalized = normalize_model_path_input(entry.load_value)
    spec = resolve_journaler_model_spec_for_slash(
        settings,
        raw_path=normalized,
        current_defaults=current_defaults,
    )
    mlx_backend = "mlx-vlm" if _detect_vlm(spec.model_path) else "mlx-lm"
    return replace(spec, mlx_backend=mlx_backend)
