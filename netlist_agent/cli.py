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


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "netlist":
        _run_netlist(args)
    elif args.command == "ratsnest":
        _run_ratsnest(args)


if __name__ == "__main__":
    main()
