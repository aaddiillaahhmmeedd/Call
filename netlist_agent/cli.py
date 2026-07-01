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
        help="Autoroute the board's airwires on a grid (single layer, v1).",
    )
    route.add_argument("board", type=Path, help="Path to a .kicad_pcb file")
    route.add_argument("--grid", type=float, default=0.25, help="Routing grid in mm")
    route.add_argument("--clearance", type=float, default=0.2, help="Copper clearance in mm")
    route.add_argument("--layer", default="F.Cu", help="Layer for new tracks")
    route.add_argument("--width", type=float, default=0.25, help="Track width in mm")
    route.add_argument("--output", type=Path, default=None, help="Write routed .kicad_pcb copy")
    route.add_argument("--svg", type=Path, default=None, help="Render routed board as SVG")

    return parser


def _run_netlist(args: argparse.Namespace) -> None:
    agent = NetlistAgent()
    netlists = agent.run(query=args.query, limit=args.limit)
    agent.save_json(netlists, args.output)
    print(f"Extracted {len(netlists)} netlists/BOM datasets -> {args.output}")


def _run_ratsnest(args: argparse.Namespace) -> None:
    board = parse_board(args.board)
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
    board = parse_board(args.board)
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

    board = parse_board(args.board)
    report = compute_ratsnest(board)
    result = route_board(
        board,
        report,
        grid=args.grid,
        clearance=args.clearance,
        layer=args.layer,
        width=args.width,
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


if __name__ == "__main__":
    main()
