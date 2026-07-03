"""KiCad pcbnew action plugin: run the netlist_agent HTML report on the open board.

This module imports pcbnew at the top, so it is only importable inside
KiCad — the package ``__init__`` guards the import. All logic that does
not need pcbnew lives in :mod:`.runner` so it stays testable outside KiCad.
"""

from __future__ import annotations

import subprocess
import tempfile
import webbrowser
from pathlib import Path

import pcbnew

from .runner import generate_report


def _message(text: str, caption: str = "Netlist Agent") -> None:
    """Show a message box inside KiCad; fall back to stderr without wx."""
    try:
        import wx

        wx.MessageBox(text, caption)
    except ImportError:
        import sys

        print(f"{caption}: {text}", file=sys.stderr)


class NetlistAgentReportPlugin(pcbnew.ActionPlugin):
    def defaults(self) -> None:
        self.name = "Netlist Agent: Board Report"
        self.category = "Analysis"
        self.description = (
            "Generate a standalone HTML report (ratsnest + DRC + board SVG) "
            "for the current board and open it in a browser."
        )
        self.show_toolbar_button = True

    def Run(self) -> None:
        board = pcbnew.GetBoard()
        board_path = board.GetFileName()
        if not board_path:
            _message("Save the board to a file first, then run the report again.")
            return

        # Work on a temp copy so the report reflects unsaved edits without
        # touching the file on disk. The directory is left for the browser;
        # the OS cleans its temp area.
        tmp = Path(tempfile.mkdtemp(prefix="netlist-agent-kicad-"))
        board_copy = tmp / Path(board_path).name
        pcbnew.SaveBoard(str(board_copy), board)

        try:
            report_path = generate_report(str(board_copy), str(tmp))
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip() or str(exc)
            _message(
                "Report generation failed — is netlist-agent installed for "
                f"KiCad's python?\n\n{detail}"
            )
            return

        webbrowser.open(Path(report_path).as_uri())
