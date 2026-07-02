from __future__ import annotations

from typing import Any

from netlist_agent.summary import markdown_summary


def _net(name: str, airwires: int = 0, unrouted_mm: float = 0.0) -> dict[str, Any]:
    return {
        "net": name,
        "pads": 2,
        "clusters": 1 + airwires,
        "fully_routed": airwires == 0,
        "routed_length_mm": 1.0,
        "unrouted_length_mm": unrouted_mm,
        "airwires": [
            {"net": name, "from": [0.0, 0.0], "to": [1.0, 0.0], "length_mm": 1.0}
            for _ in range(airwires)
        ],
    }


def _ratsnest(nets: list[dict[str, Any]]) -> dict[str, Any]:
    routed = sum(1 for n in nets if n["fully_routed"])
    return {
        "nets_total": len(nets),
        "nets_fully_routed": routed,
        "completion_pct": round(100 * routed / len(nets), 1) if nets else 100.0,
        "airwire_count": sum(len(n["airwires"]) for n in nets),
        "unrouted_length_mm": round(sum(n["unrouted_length_mm"] for n in nets), 3),
        "nets": nets,
    }


def _erc(message: str = "Component U1 is in the schematic but has no pads on the board") -> dict[str, Any]:
    return {"kind": "missing_component", "message": message, "reference": "U1", "net": None}


def _drc(message: str = "clearance 0.1 mm < required 0.15 mm") -> dict[str, Any]:
    return {"kind": "clearance", "message": message, "x": 1.0, "y": 2.0, "net_a": "GND", "net_b": "VCC"}


def test_all_clear_uses_check_emoji() -> None:
    out = markdown_summary("Board", _ratsnest([_net("GND"), _net("VCC")]))

    assert out.startswith("### ✅ Board")
    assert "Unrouted nets" not in out
    assert "<details>" not in out


def test_partial_routing_uses_warning_emoji() -> None:
    nets = [_net("GND"), _net("VCC"), _net("SDA"), _net("SCL", airwires=1, unrouted_mm=2.0)]
    out = markdown_summary("Board", _ratsnest(nets))  # 75% routed

    assert out.startswith("### ⚠️ Board")


def test_drc_only_uses_warning_emoji() -> None:
    out = markdown_summary("Board", _ratsnest([_net("GND")]), drc_violations=[_drc()])

    assert out.startswith("### ⚠️ Board")


def test_low_completion_uses_cross_emoji() -> None:
    nets = [_net("GND")] + [_net(f"N{i}", airwires=1, unrouted_mm=1.0) for i in range(3)]
    out = markdown_summary("Board", _ratsnest(nets))  # 25% routed

    assert out.startswith("### ❌ Board")


def test_any_erc_issue_uses_cross_emoji() -> None:
    out = markdown_summary("Board", _ratsnest([_net("GND")]), erc_issues=[_erc()])

    assert out.startswith("### ❌ Board")


def test_table_contains_metric_values() -> None:
    nets = [_net("GND"), _net("VCC", airwires=3, unrouted_mm=4.5)]
    out = markdown_summary("Board", _ratsnest(nets), erc_issues=[_erc()], drc_violations=[_drc(), _drc()])

    assert "| Routing | Airwires | Unrouted (mm) | ERC | DRC |" in out
    assert "| 1/2 (50%) | 3 | 4.5 | 1 | 2 |" in out


def test_unrouted_net_lines_with_overflow() -> None:
    nets = [_net("GND")] + [_net(f"NET{i}", airwires=2, unrouted_mm=1.5) for i in range(12)]
    out = markdown_summary("Board", _ratsnest(nets))

    assert "**Unrouted nets**" in out
    assert "- NET0 — 2 airwires, 1.5 mm" in out
    assert sum(1 for line in out.splitlines() if line.startswith("- NET")) == 10
    assert "…and 2 more" in out
    assert "- GND" not in out  # fully routed nets are not listed


def test_single_airwire_is_singular() -> None:
    out = markdown_summary("Board", _ratsnest([_net("SCL", airwires=1, unrouted_mm=2.0)]))

    assert "- SCL — 1 airwire, 2 mm" in out


def test_details_present_only_when_issues_exist() -> None:
    ratsnest = _ratsnest([_net("GND")])

    none_out = markdown_summary("Board", ratsnest)
    empty_out = markdown_summary("Board", ratsnest, erc_issues=[], drc_violations=[])
    erc_out = markdown_summary("Board", ratsnest, erc_issues=[_erc()])
    both_out = markdown_summary("Board", ratsnest, erc_issues=[_erc()], drc_violations=[_drc()])

    assert "<details>" not in none_out
    assert "<details>" not in empty_out
    assert erc_out.count("<details>") == 1
    assert "<summary>ERC issues (1)</summary>" in erc_out
    assert "DRC violations" not in erc_out
    assert both_out.count("<details>") == 2
    assert "<summary>DRC violations (1)</summary>" in both_out


def test_details_truncated_to_twenty_lines() -> None:
    violations = [_drc(f"violation {i}") for i in range(25)]
    out = markdown_summary("Board", _ratsnest([_net("GND")]), drc_violations=violations)

    assert "- violation 19" in out
    assert "- violation 20" not in out
    assert "…and 5 more" in out


def test_pipe_characters_are_escaped() -> None:
    nets = [_net("A|B", airwires=1, unrouted_mm=1.0)]
    out = markdown_summary(
        "Board|Rev1",
        _ratsnest(nets),
        erc_issues=[_erc("bad|pipe message")],
    )

    assert "Board\\|Rev1" in out
    assert "A\\|B" in out
    assert "bad\\|pipe message" in out
    assert "bad|pipe" not in out
