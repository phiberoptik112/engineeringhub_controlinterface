"""Horn contour and mouth aperture geometry.

Pure geometry for an exponential horn: mouth area from the flare equation and
the chopped/unchopped vertical aperture tradeoff that the LVT mouth uses to fit
the mechanical envelope.
"""

from __future__ import annotations

import math
from typing import Any


class HornGeometry:
    """Exponential horn geometry calculator (areas in mm^2, lengths in mm)."""

    def __init__(self, throat_area_mm2: float, flare_rate_per_m: float) -> None:
        self.throat_area_mm2 = throat_area_mm2
        self.flare_rate_per_m = flare_rate_per_m

    def calculate_mouth_area(self, l_exp_mm: float) -> float:
        """Mouth area S_M = S_T * exp(m * L) with L in metres."""
        return self.throat_area_mm2 * math.exp(
            self.flare_rate_per_m * (l_exp_mm / 1000.0)
        )

    def effective_mouth_dims(
        self,
        mouth_w_mm: float,
        mouth_h_mm: float,
        chop_mm: float = 14.0,
    ) -> dict[str, Any]:
        """Return the projected aperture after chopping the mouth top.

        ``chop_mm`` is the explicit amount of vertical aperture removed (the LVT
        mouth chops ~14 mm, taking 80 mm down to 66 mm projected). This replaces
        the implicit projection factor in the original blueprint so the geometry
        is traceable.
        """
        chop = max(chop_mm, 0.0)
        height_proj_mm = max(mouth_h_mm - chop, 0.0)
        return {
            "width_mm": mouth_w_mm,
            "height_proj_mm": height_proj_mm,
            "is_chopped": chop > 0.0,
            "chop_mm": chop,
        }
