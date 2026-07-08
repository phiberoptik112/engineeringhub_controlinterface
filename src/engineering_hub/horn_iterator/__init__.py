"""Horn Iterator — parametric exponential-horn sweep and LVT validation.

Core logic lives in :mod:`engineering_hub.horn_iterator.service` (a facade over
the ``config``/``geometry``/``physics``/``sweeper``/``export`` modules). It is
exposed to journaler agents via TOOL_REGISTRY entries in ``agents/tools.py``
(``/agent horn-iterator``), and to users directly via the ``engineering-hub
horn`` CLI and the ``/horn`` slash command — all of which compute locally with
no LLM or external API required.
"""

from engineering_hub.horn_iterator.config import LVTConstraints, SweepBounds
from engineering_hub.horn_iterator.service import (
    evaluate_design,
    export_results,
    format_defaults_report,
    format_sweep_report,
    get_defaults,
    resolve_output_dir,
    run_sweep,
    run_sweep_and_export,
)

__all__ = [
    "LVTConstraints",
    "SweepBounds",
    "evaluate_design",
    "export_results",
    "format_defaults_report",
    "format_sweep_report",
    "get_defaults",
    "resolve_output_dir",
    "run_sweep",
    "run_sweep_and_export",
]
