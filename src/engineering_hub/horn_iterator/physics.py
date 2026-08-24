"""Acoustic physics for the horn iterator.

Analytical approximations for cutoff frequency, quarter-wave standing-wave
resonance, low-frequency SPL rolloff below cutoff, and a mouth-aperture
directivity estimate. These are first-order engineering approximations intended
for parametric screening, not a substitute for a BEM/Akabak simulation.
"""

from __future__ import annotations

import math
from typing import Any

SPEED_OF_SOUND_M_S = 343.0

# Exponential-horn cutoff: fc = c * m / (4*pi). Named for readability.
_CUTOFF_DENOMINATOR = 4.0 * math.pi


class HornPhysics:
    """First-order acoustic estimators for an exponential horn."""

    def __init__(self, speed_of_sound_m_s: float = SPEED_OF_SOUND_M_S) -> None:
        self.c = speed_of_sound_m_s

    def cutoff_freq_hz(self, flare_rate_per_m: float) -> float:
        """Exponential horn cutoff frequency fc = c * m / (4*pi)."""
        return (self.c * flare_rate_per_m) / _CUTOFF_DENOMINATOR

    def standing_wave_resonance_hz(self, l_total_mm: float) -> float:
        """Lowest quarter-wave resonance f ~= c / (4 * L), L in metres."""
        if l_total_mm <= 0:
            return 0.0
        return self.c / (4.0 * (l_total_mm / 1000.0))

    def spl_loss_below_fc_db(self, f_hz: float, fc_hz: float) -> float:
        """Approximate SPL loss below cutoff (12 dB/octave with a knee).

        Returns 0 at/above cutoff; below cutoff applies a 12 dB/octave rolloff
        with a -3 dB knee correction near fc.
        """
        if f_hz <= 0 or fc_hz <= 0 or f_hz >= fc_hz:
            return 0.0
        return max(0.0, 12.0 * math.log2(fc_hz / f_hz) - 3.0)

    def coverage_angle_deg(
        self,
        aperture_mm: float,
        f_hz: float,
        k: float = 1.0,
    ) -> float:
        """Estimate full coverage angle from an aperture dimension.

        Uses theta = 2 * asin(min(1, k * lambda / aperture)) so the beamwidth
        saturates at 180 deg once the wavelength meets/exceeds the aperture,
        rather than the unbounded ratio in the original blueprint. ``k`` is an
        empirical directivity constant (1.0 ~ first null of a uniform aperture).
        """
        if aperture_mm <= 0 or f_hz <= 0:
            return 180.0
        wavelength_m = self.c / f_hz
        ratio = k * wavelength_m / (aperture_mm / 1000.0)
        ratio = min(1.0, max(0.0, ratio))
        return 2.0 * math.degrees(math.asin(ratio))

    def estimate_coverage(
        self,
        mouth_w_mm: float,
        mouth_h_mm: float,
        f_hz: float,
        k: float = 1.0,
    ) -> dict[str, Any]:
        """Return horizontal/vertical coverage angles for a mouth aperture."""
        return {
            "theta_h_deg": self.coverage_angle_deg(mouth_w_mm, f_hz, k),
            "theta_v_deg": self.coverage_angle_deg(mouth_h_mm, f_hz, k),
            "eval_freq_hz": f_hz,
        }
