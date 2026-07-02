from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from netlist_agent.report import render_report, write_report

_TRICKY_NET = 'N<1>&"x"'
_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect fill="#001023"/></svg>'


def _ratsnest() -> dict[str, Any]:
    return {
        "nets_total": 2,
        "nets_fully_routed": 1,
        "completion_pct": 50.0,
        "airwire_count": 1,
        "unrouted_length_mm": 3.25,
        "nets": [
            {
                "net": "GND",
                "pads": 4,
                "clusters": 1,
                "fully_routed": True,
                "routed_length_mm": 12.5,
                "unrouted_length_mm": 0.0,
                "airwires": [],
            },
            {
                "net": _TRICKY_NET,
                "pads": 2,
                "clusters": 2,
                "fully_routed": False,
                "routed_length_mm": 1.0,
                "unrouted_length_mm": 3.25,
                "airwires": [
                    {"net": _TRICKY_NET, "from": [0.0, 0.0], "to": [1.0, 3.0], "length_mm": 3.25}
                ],
            },
        ],
    }


def _erc_issues() -> list[dict[str, Any]]:
    return [
        {
            "kind": "missing_component",
            "message": "U1 present in schematic but not on board",
            "reference": "U1",
            "net": None,
        }
    ]


def _drc_violations() -> list[dict[str, Any]]:
    return [
        {
            "kind": "clearance",
            "message": "Track too close to pad",
            "x": 10.5,
            "y": 20.25,
            "net_a": "GND",
            "net_b": _TRICKY_NET,
        }
    ]


def test_report_contains_title_svg_and_net_names() -> None:
    out = render_report("My Board Report", _ratsnest(), _erc_issues(), _drc_violations(), svg=_SVG)

    assert "My Board Report" in out
    assert _SVG in out  # trusted markup embedded as-is
    assert "GND" in out
    assert html.escape(_TRICKY_NET) in out


def test_data_values_are_escaped_not_raw() -> None:
    out = render_report("t", _ratsnest(), _erc_issues(), _drc_violations())

    assert _TRICKY_NET not in out
    assert "N&lt;1&gt;&amp;&quot;x&quot;" in out


def test_stat_numbers_and_status_present() -> None:
    out = render_report("t", _ratsnest(), _erc_issues(), _drc_violations(), svg=_SVG)

    assert "50%" in out  # completion tile
    assert "3.25" in out  # unrouted mm
    assert "✓ routed" in out
    assert "✗ 1 airwire" in out
    assert "10.5, 20.25" in out  # DRC location
    assert "missing_component" in out
    assert "clearance" in out


def test_unrouted_nets_sorted_first() -> None:
    out = render_report("t", _ratsnest(), [], [])

    assert out.index(html.escape(_TRICKY_NET)) < out.index("<td>GND</td>")


def test_empty_erc_and_drc_show_none_found() -> None:
    out = render_report("t", _ratsnest(), [], [])

    assert out.count("None found") == 2
    assert "ERC issues" in out
    assert "DRC violations" in out


def test_no_svg_omits_board_panel() -> None:
    out = render_report("t", _ratsnest(), [], [], svg=None)

    assert 'class="card svg-panel"' not in out
    assert "<h2>Board</h2>" not in out


def test_write_report_creates_parents_and_round_trips(tmp_path: Path) -> None:
    out = render_report("Round Trip", _ratsnest(), _erc_issues(), _drc_violations(), svg=_SVG)
    target = tmp_path / "deep" / "nested" / "report.html"

    write_report(target, out)

    assert target.parent.is_dir()
    assert target.read_text(encoding="utf-8") == out


def test_interactive_net_highlighting() -> None:
    from netlist_agent.kicad_pcb import Board, Pad
    from netlist_agent.ratsnest import compute_ratsnest
    from netlist_agent.svg_render import render_svg

    name = 'N<1>&"x"'
    board = Board(
        nets={0: "", 1: name},
        pads=[
            Pad(reference="R1", pad_name="1", x=0.0, y=0.0, net_code=1, net_name=name, radius=0.4),
            Pad(reference="R2", pad_name="1", x=5.0, y=0.0, net_code=1, net_name=name, radius=0.4),
        ],
    )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        svg_path = Path(tmp) / "board.svg"
        render_svg(board, compute_ratsnest(board), svg_path)
        svg = svg_path.read_text(encoding="utf-8")

    escaped = html.escape(name, quote=True)
    assert f"data-net={html.escape(name, quote=True)!r}".replace("'", '"') or True
    assert "data-net=" in svg
    assert name not in svg  # raw specials never appear unescaped

    report = render_report(
        title="board",
        ratsnest={
            "completion_pct": 0.0,
            "airwire_count": 1,
            "unrouted_length_mm": 5.0,
            "nets": [{"net": name, "pads": 2, "clusters": 2, "airwires": [{}]}],
        },
        erc_issues=[],
        drc_violations=[],
        svg=svg,
    )
    assert report.count("<script>") == 1
    assert f'class="net-row" data-net="{escaped}"' in report
    # The row's attribute value matches the SVG's data-net value byte for byte.
    assert f"data-net=\"{escaped}\"" in svg


def test_no_svg_means_no_script() -> None:
    report = render_report(
        title="board",
        ratsnest={"completion_pct": 100.0, "airwire_count": 0, "unrouted_length_mm": 0, "nets": []},
        erc_issues=[],
        drc_violations=[],
        svg=None,
    )
    assert "<script>" not in report
