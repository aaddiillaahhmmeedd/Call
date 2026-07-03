"""Characteristic-impedance estimation per net (IPC-2141 approximations).

Closed-form microstrip/stripline formulas; the microstrip expression is
calibrated for roughly 0.1 < w/h < 3.0 and er < ~15 — outside that range
the numbers are indicative only. All dimensions in mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .kicad_pcb import Board


@dataclass(slots=True)
class Stackup:
    er: float = 4.5  # substrate relative permittivity (FR-4)
    height_mm: float = 0.2  # dielectric height to reference plane
    thickness_mm: float = 0.035  # copper thickness (1 oz)


@dataclass(slots=True)
class NetImpedance:
    net_code: int
    net_name: str
    widths_mm: list[float]
    z0_ohms: dict[str, float]
    target: float | None = None
    tolerance_pct: float = 10.0
    within_tolerance: bool | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "net": self.net_name,
            "widths_mm": self.widths_mm,
            "z0_ohms": {w: round(z, 2) for w, z in self.z0_ohms.items()},
        }
        if self.target is not None:
            out["target_ohms"] = self.target
            out["within_tolerance"] = self.within_tolerance
        return out


def microstrip_z0(width_mm: float, stackup: Stackup) -> float:
    """IPC-2141 surface microstrip: Z0 = 87/sqrt(er+1.41) * ln(5.98h/(0.8w+t))."""
    return (
        87.0
        / math.sqrt(stackup.er + 1.41)
        * math.log(5.98 * stackup.height_mm / (0.8 * width_mm + stackup.thickness_mm))
    )


def stripline_z0(width_mm: float, stackup: Stackup) -> float:
    """IPC-2141 symmetric stripline: Z0 = 60/sqrt(er) * ln(4h/(0.67*pi*(0.8w+t)))."""
    return (
        60.0
        / math.sqrt(stackup.er)
        * math.log(
            4.0 * stackup.height_mm / (0.67 * math.pi * (0.8 * width_mm + stackup.thickness_mm))
        )
    )


def suggest_width(target_z0: float, stackup: Stackup, lo: float = 0.05, hi: float = 5.0) -> float:
    """Bisect microstrip_z0 (monotonically decreasing in width) toward target_z0."""
    if microstrip_z0(lo, stackup) <= target_z0:
        return lo
    if microstrip_z0(hi, stackup) >= target_z0:
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2
        z = microstrip_z0(mid, stackup)
        if abs(z - target_z0) < 0.01:
            return mid
        if z > target_z0:
            lo = mid  # too narrow -> impedance too high -> widen
        else:
            hi = mid
    return (lo + hi) / 2


def estimate_board(
    board: Board,
    stackup: Stackup | None = None,
    target: float | None = None,
    tolerance_pct: float = 10.0,
) -> list[NetImpedance]:
    """One NetImpedance per net with tracks; microstrip Z0 per distinct width."""
    stackup = stackup or Stackup()
    results: list[NetImpedance] = []
    for net_code, net_name in sorted(board.nets.items()):
        if net_code == 0:
            continue
        widths = sorted({s.width for s in board.segments if s.net_code == net_code and s.width > 0})
        if not widths:
            continue
        z0 = {f"{w:g}": microstrip_z0(w, stackup) for w in widths}
        within: bool | None = None
        if target is not None:
            within = all(abs(z - target) <= target * tolerance_pct / 100 for z in z0.values())
        results.append(
            NetImpedance(
                net_code=net_code,
                net_name=net_name,
                widths_mm=widths,
                z0_ohms=z0,
                target=target,
                tolerance_pct=tolerance_pct,
                within_tolerance=within,
            )
        )
    return results
