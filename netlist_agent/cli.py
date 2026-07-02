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
    place.add_argument("--output", type=Path, default=None, help="Write re-placed .kicad_pcb copy")
    place.add_argument("--svg", type=Path, default=None, help="Render re-placed board as SVG")

    return parser


def _load_board(path: Path):
    if path.suffix.lower() == ".brd":
        from .eagle_brd import parse_eagle_board

        return parse_eagle_board(path)
    return parse_board(path)


def _run_netlist(args: argparse.Namespace) -> None:
    agent = NetlistAgent()
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
    result = route_board(
        board,
        report,
        grid=args.grid,
        clearance=args.clearance,
        layer=args.layer,
        width=args.width,
        layers=("F.Cu", "B.Cu") if args.two_layer else None,
        net_widths=net_widths,
        rip_up_retries=args.rip_up,
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

    html = render_report(
        title=args.board.name,
        ratsnest=ratsnest.to_dict(),
        erc_issues=erc_issues,
        drc_violations=[v.to_dict() for v in violations],
        svg=svg,
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
    result = optimize_placement(
        board, iterations=args.iterations, seed=args.seed, spacing=args.spacing
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


if __name__ == "__main__":
    main()
