"""Pure-python bridge between the KiCad action plugin and netlist_agent.

Kept free of any pcbnew/wx dependency so it can be imported and tested
outside KiCad. The CLI is invoked as ``python -m netlist_agent.cli`` in a
subprocess (list argv, no shell) so it runs against whatever interpreter
launched KiCad — the one where ``pip install netlist-agent`` landed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def generate_report(board_path: str, output_dir: str) -> str:
    """Run ``netlist-agent report`` on *board_path* and return the HTML path.

    Raises FileNotFoundError if the board does not exist and
    subprocess.CalledProcessError (with captured stderr) if the CLI fails.
    """
    board = Path(board_path)
    if not board.is_file():
        raise FileNotFoundError(f"board file not found: {board}")

    output = Path(output_dir) / "report.html"
    subprocess.run(
        [sys.executable, "-m", "netlist_agent.cli", "report", str(board), "-o", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    return str(output)
