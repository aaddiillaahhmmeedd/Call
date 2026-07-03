import importlib
import py_compile
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "kicad"
sys.path.insert(0, str(PLUGIN_ROOT))

from netlist_agent_plugin import runner  # noqa: E402

# Same shape as tests/test_ratsnest.py: two resistors, one routed net (N1)
# and an unrouted GND — enough for a real ratsnest + DRC report.
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
  (segment (start 101 100) (end 109 100) (width 0.25) (layer "F.Cu") (net 1))
)
"""


@pytest.fixture()
def board_file(tmp_path: Path) -> Path:
    path = tmp_path / "demo.kicad_pcb"
    path.write_text(BOARD.strip(), encoding="utf-8")
    return path


def test_generate_report(board_file: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report_path = runner.generate_report(str(board_file), str(out_dir))

    assert report_path == str(out_dir / "report.html")
    html = Path(report_path).read_text(encoding="utf-8")
    assert board_file.name in html  # report is titled after the board
    assert "<svg" in html


def test_generate_report_missing_board(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        runner.generate_report(str(tmp_path / "nope.kicad_pcb"), str(tmp_path))


def test_package_imports_without_pcbnew(monkeypatch: pytest.MonkeyPatch) -> None:
    # None in sys.modules makes `import pcbnew` raise ImportError, so the
    # guard path is exercised even if a pcbnew module were installed.
    monkeypatch.setitem(sys.modules, "pcbnew", None)
    monkeypatch.delitem(sys.modules, "netlist_agent_plugin", raising=False)
    monkeypatch.delitem(sys.modules, "netlist_agent_plugin.plugin", raising=False)

    package = importlib.import_module("netlist_agent_plugin")
    assert not hasattr(package, "NetlistAgentReportPlugin")  # no registration


def test_plugin_module_compiles() -> None:
    # plugin.py imports pcbnew at the top, so compile it without importing.
    py_compile.compile(str(PLUGIN_ROOT / "netlist_agent_plugin" / "plugin.py"), doraise=True)
