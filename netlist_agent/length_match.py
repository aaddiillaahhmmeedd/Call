"""Net length analysis and differential-pair skew checking.

Builds on the ratsnest: per-net routed copper length plus remaining airwire
length, then pairs up differential nets by naming convention and reports the
routed-length skew of each pair against a tolerance.

Pair detection is suffix-based. Suffix rules are tried in order (longest
first, so ``_P``/``_N`` beats ``P``/``N``), matched case-insensitively, and a
leading "/" on net names (KiCad hierarchical sheets) is stripped before
matching. Names that consist of nothing but the suffix never pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .kicad_pcb import Board
from .ratsnest import compute_ratsnest

# Ordered (positive, negative) suffix rules; the first matching rule wins.
_SUFFIX_RULES: list[tuple[str, str]] = [("_P", "_N"), ("P", "N"), ("+", "-"), ("_H", "_L")]


@dataclass(slots=True)
class NetLength:
    net_code: int
    net_name: str
    routed_length: float  # sum of segment lengths, mm
    unrouted_length: float  # airwire length from the ratsnest, mm

    def to_dict(self) -> dict[str, Any]:
        return {
            "net": self.net_name,
            "routed_mm": round(self.routed_length, 3),
            "unrouted_mm": round(self.unrouted_length, 3),
            "total_mm": round(self.routed_length + self.unrouted_length, 3),
        }


@dataclass(slots=True)
class PairReport:
    base_name: str
    pos_net: str
    neg_net: str
    pos_length: float  # routed length, mm
    neg_length: float
    skew: float  # abs(pos_length - neg_length)
    tolerance: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.base_name,
            "pos_net": self.pos_net,
            "neg_net": self.neg_net,
            "pos_mm": round(self.pos_length, 3),
            "neg_mm": round(self.neg_length, 3),
            "skew_mm": round(self.skew, 3),
            "tolerance_mm": self.tolerance,
            "matched": self.skew <= self.tolerance,
        }


def _strip_name(name: str) -> str:
    return name[1:] if name.startswith("/") else name


def net_lengths(board: Board) -> list[NetLength]:
    """Routed and unrouted length for every net in the ratsnest, report order."""
    return [
        NetLength(
            net_code=net.net_code,
            net_name=net.net_name,
            routed_length=net.routed_length,
            unrouted_length=net.unrouted_length,
        )
        for net in compute_ratsnest(board).nets
    ]


def find_diff_pairs(board: Board) -> list[tuple[str, str, str]]:
    """Differential pairs as (base_name, pos_name, neg_name), sorted by base_name.

    Suffixes match case-insensitively; reported names have any leading "/"
    stripped and otherwise keep their original case. Both members must exist
    as board nets, and a net joins at most one pair.
    """
    names = [_strip_name(name) for code, name in sorted(board.nets.items()) if code != 0 and name]
    by_lower: dict[str, str] = {}
    for name in names:
        by_lower.setdefault(name.lower(), name)

    pairs: list[tuple[str, str, str]] = []
    used: set[str] = set()  # lowercase names already in a pair
    for name in names:
        lower = name.lower()
        if lower in used:
            continue
        for pos_suffix, neg_suffix in _SUFFIX_RULES:
            if not lower.endswith(pos_suffix.lower()):
                continue
            base = name[: len(name) - len(pos_suffix)]
            if base:  # a net that is nothing but the suffix never pairs
                neg_lower = base.lower() + neg_suffix.lower()
                neg_name = by_lower.get(neg_lower)
                if neg_name is not None and neg_lower not in used:
                    pairs.append((base, name, neg_name))
                    used.add(lower)
                    used.add(neg_lower)
            break  # first (longest) matching suffix rule wins
    pairs.sort(key=lambda pair: pair[0])
    return pairs


def check_pairs(board: Board, tolerance: float = 0.5) -> list[PairReport]:
    """Routed-length skew for every detected differential pair, sorted by base_name."""
    routed = {_strip_name(n.net_name): n.routed_length for n in net_lengths(board)}
    reports: list[PairReport] = []
    for base_name, pos_net, neg_net in find_diff_pairs(board):
        pos_length = routed.get(pos_net, 0.0)
        neg_length = routed.get(neg_net, 0.0)
        reports.append(
            PairReport(
                base_name=base_name,
                pos_net=pos_net,
                neg_net=neg_net,
                pos_length=pos_length,
                neg_length=neg_length,
                skew=abs(pos_length - neg_length),
                tolerance=tolerance,
            )
        )
    return reports
