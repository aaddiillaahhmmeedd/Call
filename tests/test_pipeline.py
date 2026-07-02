import json
from pathlib import Path

import pytest

from netlist_agent.pipeline import analyze_tree, index_json, index_markdown

# Same shape as the tests/test_ratsnest.py fixture: two nets, N1 routed,
# GND (3 pads, no copper) needing 2 airwires -> 50% completion.
BOARD = """
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "N1")
  (net 2 "GND")
  (footprint "R_0603" (at 100 100)
    (property "Reference" "R1" (at 0 -1))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 2 "GND"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 1 "N1"))
  )
  (footprint "R_0603" (at 110 100)
    (property "Reference" "R2" (at 0 -1))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "N1"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 2 "GND"))
  )
  (footprint "C_0603" (at 105 110 90)
    (fp_text reference "C1" (at 0 -1))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 2 "GND"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 0 ""))
  )
  (segment (start 101 100) (end 109 100) (width 0.25) (layer "F.Cu") (net 1))
)
"""

# One net, fully routed, no DRC violations.
ROUTED_BOARD = """
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "SIG")
  (footprint "R_0603" (at 10 10)
    (property "Reference" "R1" (at 0 -1))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "SIG"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 1 "SIG"))
  )
  (segment (start 9 10) (end 11 10) (width 0.25) (layer "F.Cu") (net 1))
)
"""

VALID_PATHS = ["demo.kicad_pcb", "sub/nested.kicad_pcb"]
ERROR_PATHS = ["broken.kicad_pcb", "fake.brd"]


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    (tmp_path / "demo.kicad_pcb").write_text(BOARD.strip(), encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.kicad_pcb").write_text(ROUTED_BOARD.strip(), encoding="utf-8")
    (tmp_path / "broken.kicad_pcb").write_text("garbage text | not a board", encoding="utf-8")
    (tmp_path / "fake.brd").write_text("PADS-POWERPCB-V9.0\n*BOARD*\n", encoding="utf-8")
    for skipped in (".hidden", "__pycache__", "node_modules"):
        directory = tmp_path / skipped
        directory.mkdir()
        (directory / "skipped.kicad_pcb").write_text(BOARD.strip(), encoding="utf-8")
    return tmp_path


def test_analyze_tree_finds_and_sorts_boards(tree: Path) -> None:
    results = analyze_tree(tree)
    assert [r.path for r in results] == sorted(VALID_PATHS + ERROR_PATHS)


def test_valid_boards_have_reports(tree: Path) -> None:
    by_path = {r.path: r for r in analyze_tree(tree)}
    for path in VALID_PATHS:
        result = by_path[path]
        assert result.error is None
        assert isinstance(result.ratsnest, dict)
        assert isinstance(result.drc, list)

    demo = by_path["demo.kicad_pcb"]
    assert demo.ratsnest is not None
    assert demo.ratsnest["nets_total"] == 2
    assert demo.ratsnest["completion_pct"] == 50.0

    nested = by_path["sub/nested.kicad_pcb"]
    assert nested.ratsnest is not None
    assert nested.ratsnest["completion_pct"] == 100.0
    assert nested.drc == []


def test_unparsable_files_become_error_entries(tree: Path) -> None:
    by_path = {r.path: r for r in analyze_tree(tree)}
    for path in ERROR_PATHS:
        result = by_path[path]
        assert result.error
        assert result.ratsnest is None
        assert result.drc == []
    assert "not an Eagle board file" in (by_path["fake.brd"].error or "")


def test_hidden_and_tooling_dirs_skipped(tree: Path) -> None:
    paths = [r.path for r in analyze_tree(tree)]
    assert not any("skipped" in path for path in paths)


def test_index_json_round_trips(tree: Path) -> None:
    payload = index_json(analyze_tree(tree))
    assert json.loads(json.dumps(payload)) == payload
    assert {entry["path"] for entry in payload} == set(VALID_PATHS + ERROR_PATHS)
    assert all(entry.keys() == {"path", "ratsnest", "drc", "error"} for entry in payload)


def test_index_markdown_table(tree: Path) -> None:
    results = analyze_tree(tree)
    markdown = index_markdown(results)
    lines = markdown.splitlines()

    assert lines[0] == "## PCB analysis (4 boards)"
    assert "| Board | Nets | Routed | Airwires | DRC | Status |" in lines
    rows = [line for line in lines if line.startswith("| ") and not line.startswith("| Board") and not line.startswith("| ---")]
    assert len(rows) == len(results)
    for result in results:
        assert f"| {result.path} |" in markdown

    error_rows = [row for row in rows if "❌" in row]
    assert len(error_rows) == len(ERROR_PATHS)
    assert any("not an Eagle board file" in row for row in error_rows)
    assert "✅" in markdown  # sub/nested.kicad_pcb is fully routed and clean
