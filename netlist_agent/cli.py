from __future__ import annotations

import argparse
from pathlib import Path

from .orchestrator import NetlistAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netlist-agent",
        description="Discover PCB repositories on GitHub and extract netlist/component data.",
    )
    parser.add_argument("query", help="GitHub search query (example: kicad stm32 power supply)")
    parser.add_argument("-n", "--limit", type=int, default=5, help="Number of GitHub repos to scan")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output/netlists.json"),
        help="Output JSON file",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    agent = NetlistAgent()
    netlists = agent.run(query=args.query, limit=args.limit)
    agent.save_json(netlists, args.output)
    print(f"Extracted {len(netlists)} netlists/BOM datasets -> {args.output}")


if __name__ == "__main__":
    main()
