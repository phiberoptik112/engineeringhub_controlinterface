"""Public facade for the horn iterator parametric sweep.

Plain functions over the geometry/physics/sweeper engines, mirroring the
rental-scout/blender service pattern: JSON-serializable returns, explicit
arguments, settings-derived defaults, and fail-soft error dicts. Consumed by
``agents/tools.py`` (agent tools), the ``engineering-hub horn`` CLI, and the
``/horn`` slash command.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any

from engineering_hub.config.loader import find_config_file
from engineering_hub.config.settings import Settings
from engineering_hub.horn_iterator import config as horn_config
from engineering_hub.horn_iterator.export import (
    export_results,
    format_defaults_report,
    format_sweep_report,
)
from engineering_hub.horn_iterator.geometry import HornGeometry
from engineering_hub.horn_iterator.physics import HornPhysics
from engineering_hub.horn_iterator.sweeper import ParametricSweeper

logger = logging.getLogger(__name__)

OUTPUT_SUBDIR = "horn_iterator"


def _load_settings() -> Settings:
    config_path = find_config_file()
    return Settings.from_yaml(config_path) if config_path else Settings()


def _build_engines(
    settings: Settings | None,
    overrides: dict[str, Any] | None = None,
) -> tuple[horn_config.LVTConstraints, horn_config.SweepBounds, ParametricSweeper]:
    constraints, bounds = horn_config.from_settings(settings)
    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            if hasattr(bounds, key):
                setattr(bounds, key, float(value))
            elif hasattr(constraints, key):
                setattr(constraints, key, float(value))
    # The exponential flare starts at the diffraction slot (S_T), not the
    # driver adapter throat (S1), so mouth area uses slot_area_mm2.
    geometry = HornGeometry(constraints.slot_area_mm2, constraints.flare_rate_per_m)
    physics = HornPhysics()
    sweeper = ParametricSweeper(geometry, physics, constraints, bounds)
    return constraints, bounds, sweeper


def resolve_output_dir(
    settings: Settings | None = None,
    output_dir: Path | str | None = None,
) -> Path:
    """Resolve the directory for sweep exports, creating it if needed.

    Priority: explicit argument > ``horn_iterator.output_dir`` config >
    ``{workspace_dir}/horn_iterator``.
    """
    if output_dir is None:
        settings = settings or _load_settings()
        configured = getattr(settings, "horn_iterator_output_dir", None)
        if configured is not None:
            output_dir = configured
        else:
            output_dir = Path(settings.workspace_dir) / OUTPUT_SUBDIR
    path = Path(output_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_defaults(settings: Settings | None = None) -> dict[str, Any]:
    """Return the configured constraints and sweep bounds as plain dicts."""
    settings = settings or _load_settings()
    constraints, bounds = horn_config.from_settings(settings)
    return {"constraints": constraints.to_dict(), "bounds": bounds.to_dict()}


def evaluate_design(
    l_exp_mm: float,
    mouth_w_mm: float,
    mouth_h_mm: float,
    settings: Settings | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a single horn design against the LVT constraints."""
    settings = settings or _load_settings()
    _, _, sweeper = _build_engines(settings, overrides)
    return sweeper.evaluate_design(l_exp_mm, mouth_w_mm, mouth_h_mm)


def run_sweep(
    settings: Settings | None = None,
    overrides: dict[str, Any] | None = None,
    valid_only: bool = False,
) -> dict[str, Any]:
    """Run the full parametric sweep and return rows plus valid designs.

    ``overrides`` may set any ``SweepBounds`` or ``LVTConstraints`` field
    (e.g. ``{"step_l_mm": 5, "flare_rate_per_m": 15}``).
    """
    settings = settings or _load_settings()
    constraints, bounds, sweeper = _build_engines(settings, overrides)
    rows = sweeper.run_sweep()
    valid = ParametricSweeper.filter_valid(rows)
    return {
        "count": len(rows),
        "valid_count": len(valid),
        "rows": [] if valid_only else rows,
        "valid": valid,
        "constraints": constraints.to_dict(),
        "bounds": bounds.to_dict(),
    }


def run_sweep_and_export(
    settings: Settings | None = None,
    overrides: dict[str, Any] | None = None,
    fmt: str = "csv",
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run a sweep then export all rows; returns the sweep result plus export info."""
    settings = settings or _load_settings()
    result = run_sweep(settings=settings, overrides=overrides)
    out_dir = resolve_output_dir(settings=settings, output_dir=output_dir)
    export = export_results(result["rows"], out_dir, fmt=fmt)
    result["export"] = export
    return result


_SLASH_USAGE = (
    "Usage: /horn [defaults | sweep] "
    "[--export csv|org] [--step-l MM] [--step-wh MM] [--valid-only]"
)


def handle_slash_command(message: str, settings: Settings | None = None) -> str:
    """Handle the ``/horn`` slash command for chat/TUI/HTTP surfaces.

    Computes locally (no LLM). Subcommands: ``defaults`` (default) shows the
    configured constraints/bounds; ``sweep`` runs the parametric sweep with
    optional ``--export`` and step overrides.
    """
    settings = settings or _load_settings()
    try:
        parts = shlex.split(message)
    except ValueError:
        parts = message.split()
    # Drop the leading "/horn" token.
    args = parts[1:] if parts else []
    sub = args[0].lower() if args and not args[0].startswith("-") else "defaults"

    if sub == "defaults":
        return format_defaults_report(get_defaults(settings))

    if sub != "sweep":
        return _SLASH_USAGE

    flags = args[1:]
    overrides: dict[str, Any] = {}
    export_fmt: str | None = None
    valid_only = False
    index = 0
    while index < len(flags):
        token = flags[index]
        if token == "--export":
            index += 1
            export_fmt = flags[index] if index < len(flags) else "csv"
        elif token == "--step-l":
            index += 1
            if index < len(flags):
                overrides["step_l_mm"] = float(flags[index])
        elif token == "--step-wh":
            index += 1
            if index < len(flags):
                overrides["step_wh_mm"] = float(flags[index])
        elif token == "--valid-only":
            valid_only = True
        index += 1

    if export_fmt:
        result = run_sweep_and_export(
            settings=settings, overrides=overrides, fmt=export_fmt
        )
    else:
        result = run_sweep(settings=settings, overrides=overrides, valid_only=valid_only)

    report = format_sweep_report(result)
    export = result.get("export")
    if export:
        if export.get("success"):
            report += f"\n\nExported {export['rows']} rows to `{export['path']}`"
        else:
            report += f"\n\nExport failed: {export.get('error')}"
    return report


__all__ = [
    "evaluate_design",
    "export_results",
    "format_defaults_report",
    "format_sweep_report",
    "get_defaults",
    "handle_slash_command",
    "resolve_output_dir",
    "run_sweep",
    "run_sweep_and_export",
]
