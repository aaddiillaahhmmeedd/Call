from __future__ import annotations

from netlist_agent.erc import ErcIssue, compare
from netlist_agent.kicad_pcb import Board, Pad
from netlist_agent.models import BoardNetlist, Component, NetConnection


def _pad(reference: str, net_code: int, net_name: str, pad_name: str = "1") -> Pad:
    return Pad(
        reference=reference,
        pad_name=pad_name,
        x=0.0,
        y=0.0,
        net_code=net_code,
        net_name=net_name,
        radius=0.5,
    )


def _schematic(components: list[Component], nets: list[NetConnection]) -> BoardNetlist:
    return BoardNetlist(repo="org/repo", path="board.net", components=components, nets=nets)


def test_clean_match_returns_no_issues() -> None:
    schematic = _schematic(
        components=[Component(reference="R1"), Component(reference="C1")],
        nets=[NetConnection(net_name="GND", references=["C1", "R1"])],
    )
    board = Board(
        nets={0: "", 1: "GND"},
        pads=[_pad("R1", 1, "GND"), _pad("C1", 1, "GND")],
    )

    assert compare(schematic, board) == []


def test_missing_component() -> None:
    schematic = _schematic(
        components=[Component(reference="R1"), Component(reference="U1")],
        nets=[],
    )
    board = Board(nets={1: "GND"}, pads=[_pad("R1", 1, "GND")])

    issues = compare(schematic, board)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.kind == "missing_component"
    assert issue.reference == "U1"
    assert "U1" in issue.message
    assert issue.to_dict()["kind"] == "missing_component"


def test_extra_component_skips_placeholder_references() -> None:
    schematic = _schematic(components=[Component(reference="R1")], nets=[])
    board = Board(
        nets={1: "GND"},
        pads=[
            _pad("R1", 1, "GND"),
            _pad("MH1", 1, "GND"),  # genuinely extra
            _pad("?", 1, "GND"),  # placeholder reference: ignored
            _pad("", 1, "GND"),  # empty reference: ignored
        ],
    )

    issues = compare(schematic, board)
    assert [i.kind for i in issues] == ["extra_component"]
    assert issues[0].reference == "MH1"


def test_net_mismatch_reports_missing_and_extra_references() -> None:
    schematic = _schematic(
        components=[Component(reference="R1"), Component(reference="C1"), Component(reference="C2")],
        nets=[NetConnection(net_name="VCC", references=["C1", "R1"])],
    )
    board = Board(
        nets={1: "VCC"},
        pads=[_pad("R1", 1, "VCC"), _pad("C2", 1, "VCC")],  # C1 missing, C2 extra
    )

    issues = [i for i in compare(schematic, board) if i.kind == "net_mismatch"]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.net == "VCC"
    assert "missing on board: C1" in issue.message
    assert "extra on board: C2" in issue.message


def test_hierarchical_net_name_normalization() -> None:
    # Schematic uses the hierarchical "/SIG" name; the board plain "SIG".
    schematic = _schematic(
        components=[Component(reference="R1"), Component(reference="R2")],
        nets=[NetConnection(net_name="/SIG", references=["R1", "R2"])],
    )
    board = Board(
        nets={1: "SIG"},
        pads=[_pad("R1", 1, "SIG"), _pad("R2", 1, "SIG")],
    )

    assert compare(schematic, board) == []

    # But matching stays case-sensitive after prefix stripping.
    board_wrong_case = Board(
        nets={1: "sig"},
        pads=[_pad("R1", 1, "sig"), _pad("R2", 1, "sig")],
    )
    issues = compare(schematic, board_wrong_case)
    assert [i.kind for i in issues] == ["net_mismatch"]


def test_empty_net_names_and_net_code_zero_are_ignored() -> None:
    schematic = _schematic(
        components=[Component(reference="R1")],
        nets=[NetConnection(net_name="", references=["R1"])],
    )
    board = Board(nets={0: ""}, pads=[_pad("R1", 0, "")])

    assert compare(schematic, board) == []


def test_issues_sorted_deterministically() -> None:
    schematic = _schematic(
        components=[Component(reference="R1"), Component(reference="U1")],
        nets=[NetConnection(net_name="GND", references=["R1", "U1"])],
    )
    board = Board(nets={1: "GND"}, pads=[_pad("X9", 1, "GND")])

    issues = compare(schematic, board)
    assert [i.kind for i in issues] == [
        "extra_component",
        "missing_component",
        "missing_component",
        "net_mismatch",
    ]
    assert issues == sorted(issues, key=lambda i: (i.kind, i.reference or "", i.net or ""))
    assert all(isinstance(i, ErcIssue) for i in issues)
