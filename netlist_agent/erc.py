"""ERC / netlist-diff: compare a schematic netlist against a routed board.

The schematic side is a :class:`~netlist_agent.models.BoardNetlist`
(reference-level nets from ``pcb_extractors``); the board side is a
:class:`~netlist_agent.kicad_pcb.Board` (pad-level net assignments).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .kicad_pcb import Board
from .models import BoardNetlist

_IGNORED_REFERENCES = {"", "?"}


@dataclass(slots=True)
class ErcIssue:
    kind: str  # "missing_component" | "extra_component" | "net_mismatch"
    message: str
    reference: str | None = None
    net: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "reference": self.reference,
            "net": self.net,
        }


def _normalize_net_name(name: str) -> str:
    """Strip the leading KiCad hierarchical "/" prefix; otherwise case-sensitive."""
    return name[1:] if name.startswith("/") else name


def compare(schematic: BoardNetlist, board: Board) -> list[ErcIssue]:
    issues: list[ErcIssue] = []

    schematic_refs = {c.reference for c in schematic.components}
    board_refs = {p.reference for p in board.pads}

    for ref in schematic_refs - board_refs:
        issues.append(
            ErcIssue(
                kind="missing_component",
                message=f"Component {ref} is in the schematic but has no pads on the board",
                reference=ref,
            )
        )

    for ref in board_refs - schematic_refs:
        if ref in _IGNORED_REFERENCES:
            continue
        issues.append(
            ErcIssue(
                kind="extra_component",
                message=f"Component {ref} is on the board but not in the schematic",
                reference=ref,
            )
        )

    # Board connectivity: normalized net name -> set of pad references.
    board_nets: dict[str, set[str]] = {}
    for pad in board.pads:
        if pad.net_code == 0 or not pad.net_name:
            continue
        board_nets.setdefault(_normalize_net_name(pad.net_name), set()).add(pad.reference)

    for net in schematic.nets:
        if not net.net_name:
            continue
        normalized = _normalize_net_name(net.net_name)
        expected = set(net.references)
        actual = board_nets.get(normalized, set())
        if expected == actual:
            continue
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        parts: list[str] = []
        if missing:
            parts.append(f"missing on board: {', '.join(missing)}")
        if extra:
            parts.append(f"extra on board: {', '.join(extra)}")
        issues.append(
            ErcIssue(
                kind="net_mismatch",
                message=f"Net {net.net_name}: {'; '.join(parts)}",
                net=normalized,
            )
        )

    issues.sort(key=lambda i: (i.kind, i.reference or "", i.net or ""))
    return issues
