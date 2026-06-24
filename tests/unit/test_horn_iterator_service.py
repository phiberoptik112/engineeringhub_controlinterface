"""Unit tests for the horn iterator compute package (no external deps)."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from engineering_hub.horn_iterator import service as horn_service
from engineering_hub.horn_iterator.config import LVTConstraints, SweepBounds
from engineering_hub.horn_iterator.export import export_results, format_sweep_report
from engineering_hub.horn_iterator.geometry import HornGeometry
from engineering_hub.horn_iterator.physics import HornPhysics
from engineering_hub.horn_iterator.sweeper import ParametricSweeper


def test_cutoff_freq_matches_blueprint() -> None:
    physics = HornPhysics()
    # m = 17.8 /m -> fc = c*m/(4*pi) ~= 486 Hz (blueprint quotes ~485 Hz).
    fc = physics.cutoff_freq_hz(17.8)
    assert fc == pytest.approx(485.9, abs=1.0)


def test_calculate_mouth_area() -> None:
    geo = HornGeometry(throat_area_mm2=1050.0, flare_rate_per_m=17.8)
    # S_M = S_T * exp(m * L); L = 0 returns the throat area.
    assert geo.calculate_mouth_area(0.0) == pytest.approx(1050.0)
    expected = 1050.0 * math.exp(17.8 * (130.0 / 1000.0))
    assert geo.calculate_mouth_area(130.0) == pytest.approx(expected)


def test_effective_mouth_dims_chop() -> None:
    geo = HornGeometry(throat_area_mm2=1050.0, flare_rate_per_m=17.8)
    dims = geo.effective_mouth_dims(120.0, 80.0, chop_mm=14.0)
    assert dims["height_proj_mm"] == pytest.approx(66.0)
    assert dims["is_chopped"] is True
    unchopped = geo.effective_mouth_dims(120.0, 80.0, chop_mm=0.0)
    assert unchopped["height_proj_mm"] == pytest.approx(80.0)
    assert unchopped["is_chopped"] is False


def test_directivity_saturates_at_180() -> None:
    physics = HornPhysics()
    # Wavelength much larger than aperture -> full 180 deg, never beyond.
    wide = physics.coverage_angle_deg(aperture_mm=10.0, f_hz=200.0)
    assert wide == pytest.approx(180.0)
    # Small wavelength relative to aperture -> a narrow beam (< 180).
    narrow = physics.coverage_angle_deg(aperture_mm=200.0, f_hz=8000.0)
    assert 0.0 < narrow < 180.0


def test_spl_loss_zero_above_cutoff() -> None:
    physics = HornPhysics()
    assert physics.spl_loss_below_fc_db(1000.0, 486.0) == 0.0
    loss = physics.spl_loss_below_fc_db(250.0, 486.0)
    assert loss > 0.0


def test_run_sweep_flags_violations_with_default_constraints() -> None:
    result = horn_service.run_sweep()
    assert result["count"] > 0
    # The default LVT envelope is intentionally tight; every row carries a status.
    assert all(row["status"] in ("PASS", "FAIL") for row in result["rows"])
    assert all("violations" in row for row in result["rows"])


def test_run_sweep_yields_pass_when_constraints_relaxed() -> None:
    # Relax the envelope + rolloff tolerance so some designs pass.
    overrides = {
        "max_length_mm": 400.0,
        "max_spl_loss_at_min_freq_db": 20.0,
        "fov_min_deg": 0.0,
    }
    result = horn_service.run_sweep(overrides=overrides)
    assert result["valid_count"] > 0
    assert result["valid_count"] == len(result["valid"])
    # filter_valid sorts by LF rolloff ascending.
    losses = [row["spl_loss_min_freq_db"] for row in result["valid"]]
    assert losses == sorted(losses)


def test_evaluate_single_design() -> None:
    row = horn_service.evaluate_design(130.0, 120.0, 80.0)
    assert row["l_total_mm"] == pytest.approx(182.0)
    assert row["fc_hz"] == pytest.approx(485.9, abs=1.0)
    assert "LENGTH_EXCEEDED" in row["violations"]


def test_export_csv_round_trip(tmp_path: Path) -> None:
    constraints = LVTConstraints()
    bounds = SweepBounds(step_l_mm=30.0, step_wh_mm=20.0)
    sweeper = ParametricSweeper(
        HornGeometry(constraints.slot_area_mm2, constraints.flare_rate_per_m),
        HornPhysics(),
        constraints,
        bounds,
    )
    rows = sweeper.run_sweep()
    out = export_results(rows, tmp_path, fmt="csv")
    assert out["success"] is True
    written = Path(out["path"])
    assert written.exists()
    with open(written, newline="", encoding="utf-8") as handle:
        reader = list(csv.DictReader(handle))
    assert len(reader) == len(rows)
    assert "fc_hz" in reader[0]


def test_export_rejects_empty_rows(tmp_path: Path) -> None:
    out = export_results([], tmp_path, fmt="csv")
    assert out["success"] is False


def test_format_sweep_report_contains_summary() -> None:
    result = horn_service.run_sweep()
    report = format_sweep_report(result, limit=5)
    assert "Horn Parametric Sweep" in report
    assert "Candidates evaluated" in report
