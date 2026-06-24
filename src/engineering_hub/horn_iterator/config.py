"""Configuration dataclasses for the horn iterator parametric sweep.

`LVTConstraints` captures the LVT alert-system design targets (frequency band,
SPL, field of view, mechanical envelope) and the fixed horn geometry inputs.
`SweepBounds` captures the parameter ranges and step sizes the sweeper iterates
over. Both expose sensible defaults derived from the N4v3 horn blueprint and can
be overridden from :class:`~engineering_hub.config.settings.Settings` via
:func:`from_settings`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class LVTConstraints:
    """LVT design targets and fixed horn inputs (mm / Hz / dB unless noted)."""

    freq_min_hz: float = 250.0
    freq_max_hz: float = 12_000.0
    target_spl_min_db: float = 100.0
    target_spl_max_db: float = 105.0
    fov_min_deg: float = 130.0
    fov_target_deg: float = 180.0
    max_width_mm: float = 150.0
    max_height_mm: float = 80.0
    max_length_mm: float = 160.0
    max_weight_lb: float = 10.0
    throat_area_mm2: float = 506.0
    slot_area_mm2: float = 1050.0
    flare_rate_per_m: float = 17.8
    adapter_length_mm: float = 52.0
    # Largest acceptable rolloff at freq_min_hz before a design is rejected.
    max_spl_loss_at_min_freq_db: float = 6.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SweepBounds:
    """Parameter ranges (inclusive) and step sizes for the sweep."""

    l_exp_min_mm: float = 65.0
    l_exp_max_mm: float = 130.0
    mouth_w_min_mm: float = 80.0
    mouth_w_max_mm: float = 140.0
    mouth_h_min_mm: float = 60.0
    mouth_h_max_mm: float = 90.0
    step_l_mm: float = 15.0
    step_wh_mm: float = 10.0
    # Frequency at which directivity / FOV is evaluated (voice intelligibility band).
    directivity_eval_hz: float = 2000.0
    # Vertical material chopped off the mouth top (80 -> 66 mm projected).
    chop_mm: float = 14.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def from_settings(settings: Any | None) -> tuple[LVTConstraints, SweepBounds]:
    """Build constraints/bounds, applying any ``horn_iterator_*`` overrides.

    ``settings`` may be any object (typically
    :class:`~engineering_hub.config.settings.Settings`); only attributes that are
    present and non-``None`` override the blueprint defaults, so this stays
    decoupled from the concrete settings schema.
    """
    constraints = LVTConstraints()
    bounds = SweepBounds()

    if settings is None:
        return constraints, bounds

    overrides = {
        "flare_rate_per_m": "horn_iterator_flare_rate_per_m",
        "throat_area_mm2": "horn_iterator_throat_area_mm2",
        "slot_area_mm2": "horn_iterator_slot_area_mm2",
        "adapter_length_mm": "horn_iterator_adapter_length_mm",
    }
    for field_name, attr in overrides.items():
        value = getattr(settings, attr, None)
        if value is not None:
            setattr(constraints, field_name, float(value))

    return constraints, bounds
