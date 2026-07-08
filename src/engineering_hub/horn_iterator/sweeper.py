"""Parametric sweep engine and LVT constraint validator.

Iterates exponential length x mouth width x mouth height over `SweepBounds`,
evaluates each candidate via the geometry/physics engines, and flags violations
of the `LVTConstraints`. Returns plain JSON-serializable dict rows so the agent
tool path and exporters share one data shape (no pandas dependency).
"""

from __future__ import annotations

from typing import Any

from engineering_hub.horn_iterator.config import LVTConstraints, SweepBounds
from engineering_hub.horn_iterator.geometry import HornGeometry
from engineering_hub.horn_iterator.physics import HornPhysics

# Frequency near the cutoff knee used as a secondary rolloff probe.
_KNEE_PROBE_HZ = 450.0


def _frange(start: float, stop: float, step: float) -> list[float]:
    """Inclusive float range; tolerant of floating-point drift at the endpoint."""
    if step <= 0:
        return [start]
    values: list[float] = []
    index = 0
    while True:
        value = start + index * step
        if value > stop + 1e-9:
            break
        values.append(round(value, 6))
        index += 1
    return values


class ParametricSweeper:
    """Sweeps the horn design space and validates against LVT constraints."""

    def __init__(
        self,
        geometry: HornGeometry,
        physics: HornPhysics,
        constraints: LVTConstraints,
        bounds: SweepBounds,
    ) -> None:
        self.geometry = geometry
        self.physics = physics
        self.constraints = constraints
        self.bounds = bounds

    def evaluate_design(
        self,
        l_exp_mm: float,
        mouth_w_mm: float,
        mouth_h_mm: float,
    ) -> dict[str, Any]:
        """Evaluate a single (length, width, height) candidate."""
        c = self.constraints
        b = self.bounds

        mouth_area = self.geometry.calculate_mouth_area(l_exp_mm)
        dims = self.geometry.effective_mouth_dims(mouth_w_mm, mouth_h_mm, b.chop_mm)
        l_total_mm = c.adapter_length_mm + l_exp_mm

        fc_hz = self.physics.cutoff_freq_hz(c.flare_rate_per_m)
        f_min_hz = self.physics.standing_wave_resonance_hz(l_total_mm)
        coverage = self.physics.estimate_coverage(
            dims["width_mm"], dims["height_proj_mm"], b.directivity_eval_hz
        )
        spl_loss_min = self.physics.spl_loss_below_fc_db(c.freq_min_hz, fc_hz)
        spl_loss_knee = self.physics.spl_loss_below_fc_db(_KNEE_PROBE_HZ, fc_hz)

        violations: list[str] = []
        if l_total_mm > c.max_length_mm:
            violations.append("LENGTH_EXCEEDED")
        if dims["width_mm"] > c.max_width_mm:
            violations.append("WIDTH_EXCEEDED")
        if mouth_h_mm > c.max_height_mm:
            violations.append("HEIGHT_EXCEEDED")
        if coverage["theta_h_deg"] < c.fov_min_deg:
            violations.append("FOV_INSUFFICIENT")
        if spl_loss_min > c.max_spl_loss_at_min_freq_db:
            violations.append("LF_ROLLOFF")

        return {
            "l_exp_mm": round(l_exp_mm, 1),
            "l_total_mm": round(l_total_mm, 1),
            "mouth_w_mm": round(mouth_w_mm, 1),
            "mouth_h_mm": round(mouth_h_mm, 1),
            "mouth_h_proj_mm": round(dims["height_proj_mm"], 1),
            "is_chopped": dims["is_chopped"],
            "mouth_area_mm2": round(mouth_area, 1),
            "fc_hz": round(fc_hz, 1),
            "f_min_hz": round(f_min_hz, 1),
            "fov_h_deg": round(coverage["theta_h_deg"], 1),
            "fov_v_deg": round(coverage["theta_v_deg"], 1),
            "spl_loss_min_freq_db": round(spl_loss_min, 2),
            "spl_loss_knee_db": round(spl_loss_knee, 2),
            "violations": violations,
            "status": "PASS" if not violations else "FAIL",
        }

    def run_sweep(self) -> list[dict[str, Any]]:
        """Evaluate the full parameter grid; returns one row per candidate."""
        b = self.bounds
        rows: list[dict[str, Any]] = []
        for l_exp in _frange(b.l_exp_min_mm, b.l_exp_max_mm, b.step_l_mm):
            for width in _frange(b.mouth_w_min_mm, b.mouth_w_max_mm, b.step_wh_mm):
                for height in _frange(b.mouth_h_min_mm, b.mouth_h_max_mm, b.step_wh_mm):
                    rows.append(self.evaluate_design(l_exp, width, height))
        return rows

    @staticmethod
    def filter_valid(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep only PASS rows, sorted by lowest LF rolloff (best bass first)."""
        passing = [row for row in rows if row.get("status") == "PASS"]
        return sorted(passing, key=lambda row: row.get("spl_loss_min_freq_db", 0.0))
