# Call

## Netlist Agent for PCBAs on GitHub

This repository now includes a starter **netlist agent** that:

1. Searches GitHub repositories for PCB/PCBA projects.
2. Clones each candidate repo shallowly (`--depth 1`).
3. Extracts component + net information from common files:
   - KiCad XML netlist (`.net`, `.xml`)
   - BOM CSV files (`bom.csv`, `ibom.csv`, `components.csv`)
4. Produces normalized JSON output.

## Quick start

```bash
python -m pip install -e .
netlist-agent "kicad power supply" --limit 3 --output output/netlists.json
```

Optional authentication for higher GitHub API limits:

```bash
export GITHUB_TOKEN=ghp_xxx
```

## Output schema

Each record contains:

- `repo`: `owner/repo`
- `path`: source file path inside cloned workspace
- `components[]`: reference/value/footprint/library/metadata
- `nets[]`: net name and connected references

## Notes

- This is a strong baseline for a fuller autonomous "netlisting" pipeline.
- You can add more parsers for Altium, OrCAD, Eagle, etc., in `netlist_agent/pcb_extractors.py`.
