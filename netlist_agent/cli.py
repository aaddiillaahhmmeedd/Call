from __future__ import annotations

import argparse
import json
from pathlib import Path

from .kicad_pcb import parse_board
from .orchestrator import NetlistAgent
from .ratsnest import compute_ratsnest
from .svg_render import render_svg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netlist-agent",
        description="PCB agents: extract netlists from GitHub repos and compute board ratsnests.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    netlist = sub.add_parser(
        "netlist",
        help="Discover PCB repositories on GitHub and extract netlist/component data.",
    )
    netlist.add_argument("query", help="GitHub search query (example: kicad stm32 power supply)")
    netlist.add_argument("-n", "--limit", type=int, default=5, help="Number of GitHub repos to scan")
    netlist.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output/netlists.json"),
        help="Output JSON file",
    )
    netlist.add_argument(
        "--analyze",
        action="store_true",
        help="Also run ratsnest + DRC on any board files found in each repo",
    )

    ratsnest = sub.add_parser(
        "ratsnest",
        help="Compute airwires and routing completion for a local .kicad_pcb board.",
    )
    ratsnest.add_argument("board", type=Path, help="Path to a .kicad_pcb file")
    ratsnest.add_argument("--json", type=Path, default=None, help="Write full report as JSON")
    ratsnest.add_argument("--svg", type=Path, default=None, help="Render board + airwires as SVG")

    erc = sub.add_parser(
        "erc",
        help="Diff a schematic netlist against a board's pad connectivity.",
    )
    erc.add_argument("netlist", type=Path, help="Schematic netlist (.net/.xml KiCad export)")
    erc.add_argument("board", type=Path, help="Path to a .kicad_pcb file")
    erc.add_argument("--json", type=Path, default=None, help="Write issues as JSON")
    erc.add_argument("--strict", action="store_true", help="Exit with status 2 if issues found")

    route = sub.add_parser(
        "route",
        help="Autoroute the board's airwires on a grid (single or two layer).",
    )
    route.add_argument("board", type=Path, help="Path to a .kicad_pcb file")
    route.add_argument("--grid", type=float, default=0.25, help="Routing grid in mm")
    route.add_argument("--clearance", type=float, default=0.2, help="Copper clearance in mm")
    route.add_argument("--layer", default="F.Cu", help="Layer for new tracks (single-layer mode)")
    route.add_argument(
        "--two-layer",
        action="store_true",
        help="Route on F.Cu + B.Cu with via insertion",
    )
    route.add_argument(
        "--layers",
        default=None,
        help="Comma-separated routing layer stack (example: F.Cu,In1.Cu,B.Cu); overrides --two-layer",
    )
    route.add_argument(
        "--blind-vias",
        action="store_true",
        help="Allow blind/buried vias (adjacent-layer hops) instead of through vias",
    )
    route.add_argument("--width", type=float, default=0.25, help="Track width in mm")
    route.add_argument(
        "--rip-up", type=int, default=2, help="Rip-up-and-reroute retries per failed airwire"
    )
    route.add_argument("--output", type=Path, default=None, help="Write routed .kicad_pcb copy")
    route.add_argument("--svg", type=Path, default=None, help="Render routed board as SVG")

    drc = sub.add_parser(
        "drc",
        help="Check copper clearance and track-width rules on a board.",
    )
    drc.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    drc.add_argument("--clearance", type=float, default=0.15, help="Minimum copper clearance in mm")
    drc.add_argument("--min-width", type=float, default=0.15, help="Minimum track width in mm")
    drc.add_argument("--json", type=Path, default=None, help="Write violations as JSON")
    drc.add_argument("--strict", action="store_true", help="Exit with status 2 if violations found")

    report = sub.add_parser(
        "report",
        help="One-shot HTML report: ratsnest + DRC (+ ERC when a netlist is given).",
    )
    report.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    report.add_argument("--netlist", type=Path, default=None, help="Schematic netlist for ERC")
    report.add_argument("--clearance", type=float, default=0.15, help="DRC clearance in mm")
    report.add_argument("--min-width", type=float, default=0.15, help="DRC minimum track width in mm")
    report.add_argument(
        "-o", "--output", type=Path, default=Path("output/report.html"), help="Output HTML file"
    )
    report.add_argument(
        "--assembly", action="store_true", help="Include an assembly-drawing panel"
    )

    summary = sub.add_parser(
        "summary",
        help="Print a markdown summary (for PR comments) of ratsnest/ERC/DRC results.",
    )
    summary.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    summary.add_argument("--netlist", type=Path, default=None, help="Schematic netlist for ERC")
    summary.add_argument("--clearance", type=float, default=0.15, help="DRC clearance in mm")

    place = sub.add_parser(
        "place",
        help="Optimize component placement to minimize total ratsnest length.",
    )
    place.add_argument("board", type=Path, help="Path to a .kicad_pcb file")
    place.add_argument("--iterations", type=int, default=5000, help="Annealing iterations")
    place.add_argument("--seed", type=int, default=0, help="Random seed")
    place.add_argument("--spacing", type=float, default=0.5, help="Minimum component spacing in mm")
    place.add_argument(
        "--fixed",
        default="",
        help="Comma-separated references that must not move (example: U1,J1)",
    )
    place.add_argument("--output", type=Path, default=None, help="Write re-placed .kicad_pcb copy")
    place.add_argument("--svg", type=Path, default=None, help="Render re-placed board as SVG")

    pour = sub.add_parser(
        "pour",
        help="Generate a copper pour (filled zone) for a net, avoiding foreign copper.",
    )
    pour.add_argument("board", type=Path, help="Path to a .kicad_pcb file")
    pour.add_argument("--net", required=True, help="Net name to pour (example: GND)")
    pour.add_argument("--layer", default="B.Cu", help="Copper layer for the pour")
    pour.add_argument("--clearance", type=float, default=0.3, help="Clearance to foreign copper in mm")
    pour.add_argument("--grid", type=float, default=0.25, help="Fill grid in mm")
    pour.add_argument(
        "--thermal", action="store_true", help="Connect same-net pads with thermal-relief spokes"
    )
    pour.add_argument(
        "--smooth", action="store_true", help="Trace smoothed region outlines instead of rectangles"
    )
    pour.add_argument("--output", type=Path, default=None, help="Write poured .kicad_pcb copy")
    pour.add_argument("--svg", type=Path, default=None, help="Render poured board as SVG")

    lengths = sub.add_parser(
        "lengths",
        help="Report net lengths and differential-pair skew.",
    )
    lengths.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    lengths.add_argument(
        "--tolerance", type=float, default=0.5, help="Allowed diff-pair skew in mm"
    )
    lengths.add_argument("--json", type=Path, default=None, help="Write report as JSON")

    batch = sub.add_parser(
        "batch",
        help="Analyze every board file under a directory tree.",
    )
    batch.add_argument("root", type=Path, help="Directory to scan for .kicad_pcb/.brd files")
    batch.add_argument("--clearance", type=float, default=0.15, help="DRC clearance in mm")
    batch.add_argument("--json", type=Path, default=None, help="Write full results as JSON")
    batch.add_argument("--markdown", type=Path, default=None, help="Write index as markdown")
    batch.add_argument("--html", type=Path, default=None, help="Write interactive HTML dashboard")

    audit = sub.add_parser(
        "audit",
        help="Flag suspicious board issues: antenna tracks, single-pad nets, duplicates.",
    )
    audit.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    audit.add_argument("--json", type=Path, default=None, help="Write findings as JSON")
    audit.add_argument("--strict", action="store_true", help="Exit with status 2 on findings")

    gerber = sub.add_parser(
        "gerber",
        help="Export copper layers as Gerber RS-274X plus an Excellon drill file.",
    )
    gerber.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    gerber.add_argument(
        "-o", "--output", type=Path, default=Path("output/gerbers"), help="Output directory"
    )
    gerber.add_argument(
        "--layers", default="F.Cu,B.Cu", help="Comma-separated copper layers to export"
    )

    export = sub.add_parser(
        "export",
        help="Export a KiCad XML netlist or grouped BOM CSV from a board.",
    )
    export.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    export.add_argument("--netlist", type=Path, default=None, help="Write XML netlist here")
    export.add_argument("--bom", type=Path, default=None, help="Write BOM CSV here")

    dsn = sub.add_parser(
        "dsn",
        help="Export a Specctra DSN file for external autorouters (freerouting).",
    )
    dsn.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    dsn.add_argument(
        "-o", "--output", type=Path, default=Path("output/board.dsn"), help="Output DSN file"
    )

    ses = sub.add_parser(
        "ses",
        help="Import a Specctra session (.ses) from an external autorouter into the board.",
    )
    ses.add_argument("board", type=Path, help="Path to the .kicad_pcb the DSN was exported from")
    ses.add_argument("session", type=Path, help="Path to the .ses session file")
    ses.add_argument(
        "-o", "--output", type=Path, default=Path("output/routed.kicad_pcb"), help="Output board"
    )

    impedance = sub.add_parser(
        "impedance",
        help="Estimate per-net characteristic impedance (IPC-2141 microstrip).",
    )
    impedance.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    impedance.add_argument("--er", type=float, default=4.5, help="Substrate relative permittivity")
    impedance.add_argument("--height", type=float, default=0.2, help="Dielectric height in mm")
    impedance.add_argument("--target", type=float, default=None, help="Target impedance in ohms")
    impedance.add_argument(
        "--tolerance", type=float, default=10.0, help="Allowed deviation from target in percent"
    )
    impedance.add_argument("--json", type=Path, default=None, help="Write report as JSON")

    teardrops = sub.add_parser(
        "teardrops",
        help="Add teardrop reinforcement where tracks enter pads.",
    )
    teardrops.add_argument("board", type=Path, help="Path to a .kicad_pcb file")
    teardrops.add_argument("--length", type=float, default=1.0, help="Wedge length as pad-radius ratio")
    teardrops.add_argument("--width", type=float, default=0.9, help="Base half-width as pad-radius ratio")
    teardrops.add_argument("--output", type=Path, default=None, help="Write teardropped board copy")

    panel = sub.add_parser(
        "panel",
        help="Panelize a board N x M with spacing and frame rails.",
    )
    panel.add_argument("board", type=Path, help="Path to a .kicad_pcb file")
    panel.add_argument("--rows", type=int, default=2, help="Panel rows")
    panel.add_argument("--cols", type=int, default=2, help="Panel columns")
    panel.add_argument("--gap", type=float, default=3.0, help="Gap between copies in mm")
    panel.add_argument("--rail", type=float, default=5.0, help="Frame rail height in mm (0 = none)")
    panel.add_argument(
        "-o", "--output", type=Path, default=Path("output/panel.kicad_pcb"), help="Output board"
    )

    assembly = sub.add_parser(
        "assembly",
        help="Render an assembly drawing (courtyards, references, pin-1 marks) as SVG.",
    )
    assembly.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    assembly.add_argument(
        "-o", "--output", type=Path, default=Path("output/assembly.svg"), help="Output SVG"
    )

    silk = sub.add_parser(
        "silk",
        help="Export a silkscreen Gerber with stroke-font reference designators.",
    )
    silk.add_argument("board", type=Path, help="Path to a .kicad_pcb or Eagle .brd file")
    silk.add_argument("--height", type=float, default=1.0, help="Text height in mm")
    silk.add_argument(
        "-o", "--output", type=Path, default=Path("output/board-F_SilkS.gbr"), help="Output Gerber"
    )

    return parser


def _load_board(path: Path):
    if path.suffix.lower() == ".brd":
        from .eagle_brd import parse_eagle_board

        return parse_eagle_board(path)
    return parse_board(path)


def _run_netlist(args: argparse.Namespace) -> None:
    agent = NetlistAgent()
    if args.analyze:
        results = agent.run_full(query=args.query, limit=args.limit)
        agent.save_analysis(results, args.output)
        boards = sum(len(r.boards) for r in results)
        netlists = sum(len(r.netlists) for r in results)
        print(
            f"Analyzed {len(results)} repos: {netlists} netlists/BOMs, "
            f"{boards} boards -> {args.output}"
        )
        return
    netlists = agent.run(query=args.query, limit=args.limit)
    agent.save_json(netlists, args.output)
    print(f"Extracted {len(netlists)} netlists/BOM datasets -> {args.output}")


def _run_ratsnest(args: argparse.Namespace) -> None:
    board = _load_board(args.board)
    report = compute_ratsnest(board)
    summary = report.to_dict()

    print(
        f"{args.board.name}: {summary['nets_fully_routed']}/{summary['nets_total']} nets routed "
        f"({summary['completion_pct']}%), {summary['airwire_count']} airwires, "
        f"{summary['unrouted_length_mm']} mm unrouted"
    )
    for net in report.nets:
        if not net.fully_routed:
            print(f"  [ ] {net.net_name}: {len(net.airwires)} airwires, {net.unrouted_length:.2f} mm")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Report -> {args.json}")
    if args.svg:
        render_svg(board, report, args.svg)
        print(f"Ratsnest SVG -> {args.svg}")


def _run_erc(args: argparse.Namespace) -> None:
    from .erc import compare
    from .pcb_extractors import extract_from_file

    schematic = extract_from_file("local", args.netlist)
    if schematic is None:
        raise SystemExit(f"{args.netlist}: could not parse a netlist from this file")
    board = _load_board(args.board)
    issues = compare(schematic, board)

    if not issues:
        print(f"{args.board.name}: ERC clean — board matches {args.netlist.name}")
    for issue in issues:
        print(f"  [{issue.kind}] {issue.message}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([i.to_dict() for i in issues], indent=2), encoding="utf-8"
        )
        print(f"Issues -> {args.json}")
    if issues and args.strict:
        raise SystemExit(2)


def _run_route(args: argparse.Namespace) -> None:
    from .autoroute import route_board, write_routed_board
    from .kicad_pcb import width_for_net

    board = _load_board(args.board)
    report = compute_ratsnest(board)
    net_widths = None
    if getattr(board, "net_classes", None):
        net_widths = {
            code: width_for_net(board, code, default=args.width) for code in board.nets if code
        }
    stack: tuple[str, ...] | None = None
    if args.layers:
        stack = tuple(layer.strip() for layer in args.layers.split(",") if layer.strip())
    elif args.two_layer:
        stack = ("F.Cu", "B.Cu")
    result = route_board(
        board,
        report,
        grid=args.grid,
        clearance=args.clearance,
        layer=args.layer,
        width=args.width,
        layers=stack,
        net_widths=net_widths,
        rip_up_retries=args.rip_up,
        via_span="blind" if args.blind_vias else "through",
    )
    total_len = sum(s.length for s in result.segments)
    print(
        f"{args.board.name}: routed {len(result.routed)}/{len(result.routed) + len(result.failed)} "
        f"airwires, {len(result.segments)} new segments, {total_len:.2f} mm of track"
    )
    for airwire in result.failed:
        print(f"  [failed] {airwire.net_name}: {airwire.length:.2f} mm airwire")

    if args.output:
        write_routed_board(args.board, result, args.output)
        print(f"Routed board -> {args.output}")
    if args.svg:
        board.segments.extend(result.segments)
        render_svg(board, compute_ratsnest(board), args.svg)
        print(f"Routed SVG -> {args.svg}")


def _run_drc(args: argparse.Namespace) -> None:
    from .drc import check_board

    board = _load_board(args.board)
    violations = check_board(board, clearance=args.clearance, min_track_width=args.min_width)

    if not violations:
        print(f"{args.board.name}: DRC clean at {args.clearance} mm clearance")
    for v in violations:
        print(f"  [{v.kind}] {v.message}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([v.to_dict() for v in violations], indent=2), encoding="utf-8"
        )
        print(f"Violations -> {args.json}")
    if violations and args.strict:
        raise SystemExit(2)


def _run_report(args: argparse.Namespace) -> None:
    import tempfile

    from .drc import check_board
    from .report import render_report, write_report

    board = _load_board(args.board)
    ratsnest = compute_ratsnest(board)
    violations = check_board(board, clearance=args.clearance, min_track_width=args.min_width)

    erc_issues: list[dict] = []
    if args.netlist:
        from .erc import compare
        from .pcb_extractors import extract_from_file

        schematic = extract_from_file("local", args.netlist)
        if schematic is None:
            raise SystemExit(f"{args.netlist}: could not parse a netlist from this file")
        erc_issues = [i.to_dict() for i in compare(schematic, board)]

    with tempfile.TemporaryDirectory(prefix="netlist-agent-") as tmp:
        svg_path = Path(tmp) / "board.svg"
        render_svg(board, ratsnest, svg_path)
        svg = svg_path.read_text(encoding="utf-8")

    assembly_svg = None
    if getattr(args, "assembly", False):
        from .fab import render_assembly_svg

        assembly_svg = render_assembly_svg(board)

    html = render_report(
        title=args.board.name,
        ratsnest=ratsnest.to_dict(),
        erc_issues=erc_issues,
        drc_violations=[v.to_dict() for v in violations],
        svg=svg,
        assembly_svg=assembly_svg,
    )
    write_report(args.output, html)
    print(f"Report -> {args.output}")


def _run_summary(args: argparse.Namespace) -> None:
    from .drc import check_board
    from .summary import markdown_summary

    board = _load_board(args.board)
    ratsnest = compute_ratsnest(board)
    violations = [v.to_dict() for v in check_board(board, clearance=args.clearance)]

    erc_issues = None
    if args.netlist:
        from .erc import compare
        from .pcb_extractors import extract_from_file

        schematic = extract_from_file("local", args.netlist)
        if schematic is None:
            raise SystemExit(f"{args.netlist}: could not parse a netlist from this file")
        erc_issues = [i.to_dict() for i in compare(schematic, board)]

    print(markdown_summary(args.board.name, ratsnest.to_dict(), erc_issues, violations))


def _run_place(args: argparse.Namespace) -> None:
    from .placement import optimize_placement, write_placed_board

    board = _load_board(args.board)
    fixed = {ref.strip() for ref in args.fixed.split(",") if ref.strip()} or None
    result = optimize_placement(
        board, iterations=args.iterations, seed=args.seed, spacing=args.spacing, fixed=fixed
    )
    summary = result.to_dict()
    print(
        f"{args.board.name}: ratsnest length {result.initial_length:.2f} -> "
        f"{result.final_length:.2f} mm ({summary.get('improvement_pct', 0)}% better) "
        f"in {result.iterations} iterations"
    )

    if args.output:
        write_placed_board(args.board, result, args.output)
        print(f"Placed board -> {args.output}")
        if args.svg:
            placed = _load_board(args.output)
            render_svg(placed, compute_ratsnest(placed), args.svg)
            print(f"Placed SVG -> {args.svg}")
    elif args.svg:
        raise SystemExit("--svg requires --output (the SVG renders the re-placed board file)")


def _run_pour(args: argparse.Namespace) -> None:
    from .pour import generate_pour, write_poured_board

    board = _load_board(args.board)
    matches = [code for code, name in board.nets.items() if name.lstrip("/") == args.net.lstrip("/")]
    if not matches:
        raise SystemExit(f"{args.board.name}: no net named {args.net!r}")
    zone = generate_pour(
        board,
        matches[0],
        layer=args.layer,
        clearance=args.clearance,
        grid=args.grid,
        thermal=args.thermal,
        smooth=args.smooth,
    )
    print(
        f"{args.board.name}: poured {args.net} on {args.layer} — "
        f"{len(zone.polygons)} polygon(s), {sum(len(p) for p in zone.polygons)} vertices"
    )
    if args.output:
        write_poured_board(args.board, [zone], args.output)
        print(f"Poured board -> {args.output}")
        if args.svg:
            poured = _load_board(args.output)
            render_svg(poured, compute_ratsnest(poured), args.svg)
            print(f"Poured SVG -> {args.svg}")
    elif args.svg:
        board.zones.append(zone)
        render_svg(board, compute_ratsnest(board), args.svg)
        print(f"Poured SVG -> {args.svg}")


def _run_lengths(args: argparse.Namespace) -> None:
    from .length_match import check_pairs, net_lengths

    board = _load_board(args.board)
    lengths = net_lengths(board)
    pairs = check_pairs(board, tolerance=args.tolerance)

    for length in lengths:
        print(
            f"  {length.net_name}: {length.routed_length:.2f} mm routed"
            + (f", {length.unrouted_length:.2f} mm unrouted" if length.unrouted_length else "")
        )
    for pair in pairs:
        status = "ok" if pair.skew <= pair.tolerance else "SKEW"
        print(
            f"  [{status}] {pair.base_name}: {pair.pos_net}={pair.pos_length:.2f} mm "
            f"{pair.neg_net}={pair.neg_length:.2f} mm (skew {pair.skew:.3f} mm)"
        )
    if not pairs:
        print("  no differential pairs detected")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "nets": [l.to_dict() for l in lengths],
                    "pairs": [p.to_dict() for p in pairs],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Report -> {args.json}")


def _run_batch(args: argparse.Namespace) -> None:
    from .pipeline import analyze_tree, index_json, index_markdown

    results = analyze_tree(args.root, clearance=args.clearance)
    print(index_markdown(results))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(index_json(results), indent=2), encoding="utf-8")
        print(f"Results -> {args.json}")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(index_markdown(results), encoding="utf-8")
        print(f"Index -> {args.markdown}")
    if args.html:
        from .batch_report import render_batch_report, write_batch_report

        write_batch_report(args.html, render_batch_report(str(args.root), results))
        print(f"Dashboard -> {args.html}")


def _run_audit(args: argparse.Namespace) -> None:
    from .audit import audit_board

    board = _load_board(args.board)
    findings = audit_board(board)

    if not findings:
        print(f"{args.board.name}: audit clean")
    for finding in findings:
        print(f"  [{finding.kind}] {finding.message}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([f.to_dict() for f in findings], indent=2), encoding="utf-8"
        )
        print(f"Findings -> {args.json}")
    if findings and args.strict:
        raise SystemExit(2)


def _run_dsn(args: argparse.Namespace) -> None:
    from .dsn import write_dsn

    board = _load_board(args.board)
    write_dsn(board, args.output, name=args.board.stem)
    print(f"Specctra DSN -> {args.output}")


def _run_gerber(args: argparse.Namespace) -> None:
    from .gerber import export_gerbers

    board = _load_board(args.board)
    layers = tuple(layer.strip() for layer in args.layers.split(",") if layer.strip())
    written = export_gerbers(board, args.output, layers=layers)
    for path in written:
        print(f"  {path}")
    print(f"Exported {len(written)} fabrication files -> {args.output}")


def _run_export(args: argparse.Namespace) -> None:
    from .netlist_export import export_bom_csv, export_netlist_xml

    if not args.netlist and not args.bom:
        raise SystemExit("export: pass --netlist and/or --bom")
    board = _load_board(args.board)
    if args.netlist:
        args.netlist.parent.mkdir(parents=True, exist_ok=True)
        args.netlist.write_text(export_netlist_xml(board), encoding="utf-8")
        print(f"Netlist XML -> {args.netlist}")
    if args.bom:
        args.bom.parent.mkdir(parents=True, exist_ok=True)
        args.bom.write_text(export_bom_csv(board), encoding="utf-8")
        print(f"BOM CSV -> {args.bom}")


def _run_ses(args: argparse.Namespace) -> None:
    from .ses import apply_ses

    args.output.parent.mkdir(parents=True, exist_ok=True)
    routes = apply_ses(args.board, args.session, args.output)
    print(
        f"{args.session.name}: imported {len(routes.segments)} segments, "
        f"{len(routes.vias)} vias -> {args.output}"
    )


def _run_impedance(args: argparse.Namespace) -> None:
    from .impedance import Stackup, estimate_board, suggest_width

    board = _load_board(args.board)
    stackup = Stackup(er=args.er, height_mm=args.height)
    results = estimate_board(board, stackup, target=args.target, tolerance_pct=args.tolerance)

    for net in results:
        z_parts = ", ".join(f"{w} mm -> {z:.1f} ohm" for w, z in net.z0_ohms.items())
        marker = ""
        if args.target is not None:
            marker = " [ok]" if net.within_tolerance else " [OFF TARGET]"
        print(f"  {net.net_name}: {z_parts}{marker}")
    if args.target is not None:
        width = suggest_width(args.target, stackup)
        print(f"  suggested width for {args.target:g} ohm: {width:.3f} mm")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([n.to_dict() for n in results], indent=2), encoding="utf-8"
        )
        print(f"Report -> {args.json}")


def _run_teardrops(args: argparse.Namespace) -> None:
    from .teardrop import generate_teardrops, write_teardropped_board

    board = _load_board(args.board)
    teardrops = generate_teardrops(board, length_ratio=args.length, width_ratio=args.width)
    print(f"{args.board.name}: {len(teardrops)} teardrops generated")
    if args.output and teardrops:
        write_teardropped_board(args.board, teardrops, args.output)
        print(f"Teardropped board -> {args.output}")


def _run_silk(args: argparse.Namespace) -> None:
    from .silkscreen import write_silkscreen

    board = _load_board(args.board)
    write_silkscreen(board, args.output, text_height=args.height)
    print(f"Silkscreen Gerber -> {args.output}")


def _run_panel(args: argparse.Namespace) -> None:
    from .panel import PanelSpec, panelize

    args.output.parent.mkdir(parents=True, exist_ok=True)
    stats = panelize(
        args.board,
        PanelSpec(rows=args.rows, cols=args.cols, gap_mm=args.gap, rail_mm=args.rail),
        args.output,
    )
    print(
        f"{args.board.name}: {stats['copies']} copies at pitch "
        f"{stats['pitch_mm'][0]:g} x {stats['pitch_mm'][1]:g} mm -> {args.output}"
    )


def _run_assembly(args: argparse.Namespace) -> None:
    from .fab import write_assembly_svg

    board = _load_board(args.board)
    write_assembly_svg(board, args.output)
    print(f"Assembly drawing -> {args.output}")


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "netlist":
        _run_netlist(args)
    elif args.command == "ratsnest":
        _run_ratsnest(args)
    elif args.command == "erc":
        _run_erc(args)
    elif args.command == "route":
        _run_route(args)
    elif args.command == "drc":
        _run_drc(args)
    elif args.command == "report":
        _run_report(args)
    elif args.command == "summary":
        _run_summary(args)
    elif args.command == "place":
        _run_place(args)
    elif args.command == "pour":
        _run_pour(args)
    elif args.command == "lengths":
        _run_lengths(args)
    elif args.command == "batch":
        _run_batch(args)
    elif args.command == "gerber":
        _run_gerber(args)
    elif args.command == "export":
        _run_export(args)
    elif args.command == "dsn":
        _run_dsn(args)
    elif args.command == "audit":
        _run_audit(args)
    elif args.command == "ses":
        _run_ses(args)
    elif args.command == "impedance":
        _run_impedance(args)
    elif args.command == "teardrops":
        _run_teardrops(args)
    elif args.command == "panel":
        _run_panel(args)
    elif args.command == "assembly":
        _run_assembly(args)
    elif args.command == "silk":
        _run_silk(args)


if __name__ == "__main__":
    main()
