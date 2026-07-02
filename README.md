# Call

## PCB agents: netlisting, ratsnesting, ERC, DRC, autorouting, placement, reporting

This repository contains eight cooperating PCB bots under one CLI. Boards can
be KiCad (`.kicad_pcb`) or Eagle 6+ XML (`.brd`) — both parse into the same
board model.

1. **Netlist agent** — searches GitHub for PCB/PCBA projects, clones each
   candidate shallowly, and extracts components + nets from:
   - KiCad XML netlists (`.net`, `.xml`)
   - BOM CSV files (`bom.csv`, `ibom.csv`, `components.csv`)
2. **Ratsnest agent** — clusters copper (pads, tracks, vias, filled zones)
   per net with union-find and emits the minimum-spanning-tree airwires
   between unconnected clusters — the same shape a PCB editor draws as its
   ratsnest. Reports routing completion and renders an SVG.
3. **ERC bot** — diffs a schematic netlist against the board's pad
   connectivity: missing/extra components and net-membership mismatches.
   `--strict` makes it CI-friendly (exit code 2 on issues).
4. **DRC bot** — copper clearance (segment/pad/via pairs across nets) and
   minimum track-width checks. Also `--strict`-gated for CI.
5. **Autoroute agent** — grid autorouter: A* over the board bounding box
   with clearance-inflated obstacles; `--two-layer` routes F.Cu + B.Cu with
   via insertion, `--rip-up` enables rip-up-and-reroute for congested
   boards, and net-class `trace_width` rules set per-net track widths.
   Writes a routed board copy.
6. **Placement bot** — simulated-annealing component placement that
   minimizes total ratsnest length with an overlap penalty; writes a
   re-placed board copy.
7. **Report bot** — one-shot standalone HTML report combining ratsnest,
   DRC, optional ERC, and the board SVG.
8. **PR comment bot** — `netlist-agent summary` prints a markdown summary,
   and the `pcb-report.yml` workflow posts it as a sticky comment on pull
   requests that touch board files.

DRC and routing honor KiCad legacy `(net_class ...)` blocks: per-net
clearance (the larger of the two nets' classes wins) and trace widths.

## Quick start

```bash
python -m pip install -e .

# Netlisting: mine GitHub for PCB designs
netlist-agent netlist "kicad power supply" --limit 3 --output output/netlists.json

# Ratsnesting: analyze a local board (KiCad or Eagle)
netlist-agent ratsnest path/to/board.kicad_pcb --json output/ratsnest.json --svg output/ratsnest.svg

# ERC: schematic netlist vs board connectivity (exit 2 on issues with --strict)
netlist-agent erc path/to/schematic.net path/to/board.kicad_pcb --strict

# DRC: clearance + track width rules
netlist-agent drc path/to/board.kicad_pcb --clearance 0.15 --strict

# Autoroute the remaining airwires (two-layer with vias, rip-up on congestion)
netlist-agent route path/to/board.kicad_pcb --two-layer --rip-up 2 --output output/routed.kicad_pcb

# Optimize component placement before routing
netlist-agent place path/to/board.kicad_pcb --output output/placed.kicad_pcb --svg output/placed.svg

# Everything at once as a standalone HTML report
netlist-agent report path/to/board.kicad_pcb --netlist path/to/schematic.net -o output/report.html

# Markdown summary for PR comments (used by .github/workflows/pcb-report.yml)
netlist-agent summary path/to/board.kicad_pcb --netlist path/to/schematic.net
```

Optional authentication for higher GitHub API limits:

```bash
export GITHUB_TOKEN=ghp_xxx
```

## Ratsnest output

The console prints routing completion; `--json` writes the full report:

- `nets_total`, `nets_fully_routed`, `completion_pct`
- `airwire_count`, `unrouted_length_mm`
- per net: `pads`, `clusters`, `routed_length_mm`, `airwires[]` with endpoints

`--svg` renders tracks (red = F.Cu, blue = B.Cu), pads, vias, and dashed cyan
airwires with hover tooltips.

## Netlist output schema

Each record contains:

- `repo`: `owner/repo`
- `path`: source file path inside cloned workspace
- `components[]`: reference/value/footprint/library/metadata
- `nets[]`: net name and connected references

## Notes and limits

- Ratsnest connectivity is layer-agnostic (see `netlist_agent/ratsnest.py`
  docstring) and does not yet model copper zones/pours — a poured GND plane
  still shows airwires. Zones are the natural next parser addition.
- More ECAD parsers (Altium, OrCAD, Eagle) can be added in
  `netlist_agent/pcb_extractors.py`.
