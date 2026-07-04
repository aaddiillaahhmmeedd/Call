"""End-to-end pipeline: parse -> place -> pour -> route -> verify -> outputs.

Exercises the whole toolkit through the library API on one small but
realistic board, the way a user would drive it from placement to
fabrication files. Every stage asserts, and failure messages name the
stage that broke.
"""

from pathlib import Path

from netlist_agent.audit import audit_board
from netlist_agent.autoroute import route_board, write_routed_board
from netlist_agent.drc import check_board
from netlist_agent.erc import compare
from netlist_agent.fab import write_assembly_svg
from netlist_agent.gerber import export_gerbers
from netlist_agent.kicad_pcb import parse_board, width_for_net
from netlist_agent.netlist_export import export_netlist_xml
from netlist_agent.panel import PanelSpec, panelize
from netlist_agent.pcb_extractors import extract_from_file
from netlist_agent.placement import optimize_placement, write_placed_board
from netlist_agent.pour import generate_pour, write_poured_board
from netlist_agent.ratsnest import compute_ratsnest
from netlist_agent.report import render_report
from netlist_agent.silkscreen import write_silkscreen
from netlist_agent.summary import markdown_summary

# 4 footprints (one rotated, two with courtyards, KiCad-7 property style),
# 2 nets (one hierarchical), a net class widening GND, a 40x30 outline, and
# a deliberately scattered starting placement with no routing.
BOARD = """(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "/SIG")
  (net 2 "GND")
  (gr_rect (start 0 0) (end 40 30) (stroke (width 0.1)) (layer "Edge.Cuts"))
  (net_class "Power" "" (trace_width 0.4) (add_net "GND"))
  (footprint "R_0603" (at 8 8) (property "Reference" "R1") (property "Value" "10k")
    (fp_rect (start -1 -0.6) (end 1 0.6) (layer "F.CrtYd"))
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.8) (net 1 "/SIG"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.8) (net 2 "GND")))
  (footprint "R_0603" (at 30 20 90) (property "Reference" "R2") (property "Value" "10k")
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.8) (net 1 "/SIG"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.8) (net 2 "GND")))
  (footprint "R_0603" (at 12 24) (property "Reference" "R3") (property "Value" "1k")
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.8) (net 1 "/SIG"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.8) (net 2 "GND")))
  (footprint "C_0603" (at 22 6) (property "Reference" "C1") (property "Value" "100n")
    (fp_rect (start -1 -0.6) (end 1 0.6) (layer "F.CrtYd"))
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.8) (net 2 "GND"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.8) (net 1 "/SIG")))
)"""


def test_full_pipeline(tmp_path: Path) -> None:
    source = tmp_path / "board.kicad_pcb"
    source.write_text(BOARD, encoding="utf-8")
    board = parse_board(source)
    assert len(board.footprints) == 4, "parse: expected 4 footprints"

    # Placement (R1 fixed) writes back and re-parses.
    placement = optimize_placement(board, iterations=1500, seed=7, fixed={"R1"})
    assert placement.positions["R1"] == (8.0, 8.0), "placement: fixed ref moved"
    placed_file = tmp_path / "placed.kicad_pcb"
    write_placed_board(source, placement, placed_file)
    board = parse_board(placed_file)

    # Thermal GND pour on the back copper.
    zone = generate_pour(board, 2, layer="B.Cu", thermal=True)
    assert zone.polygons, "pour: no polygons generated"
    poured_file = tmp_path / "poured.kicad_pcb"
    write_poured_board(placed_file, [zone], poured_file)
    board = parse_board(poured_file)

    # Two-layer routing with net-class widths and rip-up.
    report = compute_ratsnest(board)
    net_widths = {c: width_for_net(board, c, default=0.25) for c in board.nets if c}
    result = route_board(
        board, report, grid=0.25, layers=("F.Cu", "B.Cu"), net_widths=net_widths, rip_up_retries=3
    )
    assert not result.failed, f"routing: {len(result.failed)} airwires unrouted"
    routed_file = tmp_path / "routed.kicad_pcb"
    write_routed_board(poured_file, result, routed_file)
    board = parse_board(routed_file)

    final = compute_ratsnest(board)
    assert all(n.fully_routed for n in final.nets), "routing: board not fully connected after write-back"

    # Verification: ERC round-trip, DRC, audit.
    netlist_file = tmp_path / "exported.net"
    netlist_file.write_text(export_netlist_xml(board), encoding="utf-8")
    schematic = extract_from_file("local", netlist_file)
    assert schematic is not None
    assert compare(schematic, board) == [], "ERC: exported netlist does not match its own board"
    assert check_board(board) == [], "DRC: unexpected violations on a legal board"
    assert not [f for f in audit_board(board) if f.kind == "antenna_track"], "audit: antenna tracks found"

    # Fabrication and reporting outputs.
    gerbers = export_gerbers(board, tmp_path / "gerbers")
    assert {p.name for p in gerbers} == {
        "board-F_Cu.gbr",
        "board-B_Cu.gbr",
        "board-F_Mask.gbr",
        "board-F_Paste.gbr",
        "board-Edge_Cuts.gbr",
        "board-drill.drl",
    }, "gerber: unexpected output set"

    write_silkscreen(board, tmp_path / "silk.gbr")
    assert (tmp_path / "silk.gbr").exists(), "silkscreen: file not written"

    from netlist_agent.dsn import export_dsn

    dsn = export_dsn(board)
    assert dsn.count("(") == dsn.count(")"), "dsn: unbalanced parentheses"

    assembly_file = tmp_path / "assembly.svg"
    write_assembly_svg(board, assembly_file)

    stats = panelize(routed_file, PanelSpec(rows=1, cols=2), tmp_path / "panel.kicad_pcb")
    assert stats["copies"] == 2, "panel: wrong copy count"

    erc_dicts: list = []
    drc_dicts: list = []
    html = render_report(
        "myboard",
        final.to_dict(),
        erc_dicts,
        drc_dicts,
        assembly_svg=assembly_file.read_text(encoding="utf-8"),
    )
    assert "myboard" in html, "report: title missing"

    summary = markdown_summary("myboard", final.to_dict(), erc_dicts, drc_dicts)
    assert summary.startswith("### ✅"), f"summary: expected all-clear, got {summary.splitlines()[0]!r}"
