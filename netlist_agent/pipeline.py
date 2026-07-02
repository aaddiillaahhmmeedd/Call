"""Batch analysis pipeline: analyze every board file under a directory tree.

Recursively discovers KiCad (``.kicad_pcb``) and Eagle (``.brd``) boards,
runs the ratsnest and DRC engines on each, and renders the results as
JSON-friendly dicts or a compact GFM index table. A board that fails to
parse becomes an error entry instead of aborting the batch.

``analyze_repo_dir`` additionally combines netlist extraction (KiCad XML
netlists and BOM CSVs) with the board analysis for one mined repository.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .drc import check_board
from .eagle_brd import is_eagle_board, parse_eagle_board
from .kicad_pcb import parse_board
from .pcb_extractors import extract_from_file
from .ratsnest import compute_ratsnest

_SKIP_DIRS = {"__pycache__", "node_modules", ".git"}
_BOARD_SUFFIXES = (".kicad_pcb", ".brd")


@dataclass(slots=True)
class BoardResult:
    """Analysis outcome for one board file (or one parse failure)."""

    path: str                     # relative to the analyzed root
    ratsnest: dict[str, Any] | None
    drc: list[dict[str, Any]]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ratsnest": self.ratsnest,
            "drc": self.drc,
            "error": self.error,
        }


def _board_files(root: Path) -> list[Path]:
    """All board files under root, skipping hidden and tooling directories."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
        files.extend(Path(dirpath) / name for name in filenames if name.endswith(_BOARD_SUFFIXES))
    return files


def _analyze_board(path: Path, rel_path: str, clearance: float) -> BoardResult:
    try:
        if path.suffix == ".brd":
            if not is_eagle_board(path):
                raise ValueError(f"{path}: not an Eagle board file")
            board = parse_eagle_board(path)
        else:
            board = parse_board(path)
        ratsnest = compute_ratsnest(board).to_dict()
        drc = [v.to_dict() for v in check_board(board, clearance=clearance)]
    except Exception as exc:  # one bad board must not abort the batch
        return BoardResult(path=rel_path, ratsnest=None, drc=[], error=str(exc))
    return BoardResult(path=rel_path, ratsnest=ratsnest, drc=drc)


def analyze_tree(root: Path, clearance: float = 0.15) -> list[BoardResult]:
    """Analyze every board file under ``root``; results sorted by relative path."""
    results = [
        _analyze_board(path, path.relative_to(root).as_posix(), clearance)
        for path in _board_files(root)
    ]
    results.sort(key=lambda result: result.path)
    return results


@dataclass(slots=True)
class RepoAnalysis:
    """Combined netlist extraction and board analysis for one repository."""

    repo: str                          # owner/name
    netlists: list[dict[str, Any]]     # BoardNetlist.to_dict() results
    boards: list[BoardResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "netlists": self.netlists,
            "boards": [board.to_dict() for board in self.boards],
        }


def analyze_repo_dir(repo: str, root: Path, clearance: float = 0.15) -> RepoAnalysis:
    """Extract netlists and analyze boards under one repository checkout.

    Netlist and board paths are recorded relative to ``root``; both lists
    are sorted by path.
    """
    netlists: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
        for name in filenames:
            file = Path(dirpath) / name
            parsed = extract_from_file(repo, file)
            if parsed:
                parsed.path = file.relative_to(root).as_posix()
                # Same shape as BoardNetlist.to_dict(), which slots=True breaks.
                netlists.append(asdict(parsed))
    netlists.sort(key=lambda netlist: netlist["path"])
    return RepoAnalysis(
        repo=repo,
        netlists=netlists,
        boards=analyze_tree(root, clearance=clearance),
    )


def index_json(results: list[BoardResult]) -> list[dict[str, Any]]:
    """JSON-serializable form of a batch run."""
    return [result.to_dict() for result in results]


def _escape(value: Any) -> str:
    """Escape markdown table delimiters in untrusted text."""
    return str(value).replace("|", "\\|")


def _status_cell(result: BoardResult) -> str:
    # Thresholds mirror summary._status_emoji (this pipeline runs no ERC).
    if result.error is not None or result.ratsnest is None:
        return f"❌ {_escape(result.error or 'no data')}"
    completion = float(result.ratsnest.get("completion_pct", 0.0))
    if completion < 50:
        return "❌"
    if completion >= 100 and not result.drc:
        return "✅"
    return "⚠️"


def _table_lines(results: list[BoardResult]) -> list[str]:
    """GFM table (header plus one row per board) as a list of lines."""
    lines = [
        "| Board | Nets | Routed | Airwires | DRC | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        ratsnest = result.ratsnest
        if ratsnest is None:
            nets = routed = airwires = drc = "—"
        else:
            nets = str(ratsnest.get("nets_total", 0))
            routed = str(ratsnest.get("nets_fully_routed", 0))
            airwires = str(ratsnest.get("airwire_count", 0))
            drc = str(len(result.drc))
        lines.append(
            f"| {_escape(result.path)} | {nets} | {routed} | {airwires} | {drc} "
            f"| {_status_cell(result)} |"
        )
    return lines


def index_markdown(
    results: list[BoardResult],
    *,
    repo_results: list[RepoAnalysis] | None = None,
) -> str:
    """Compact GFM index table for a batch run, one row per board.

    When ``repo_results`` is given, ``results`` is ignored and one H3
    section per repo is rendered instead, each with its netlist count
    and its own boards table.
    """
    if repo_results is not None:
        noun = "repo" if len(repo_results) == 1 else "repos"
        lines = [f"## PCB analysis ({len(repo_results)} {noun})"]
        for analysis in repo_results:
            count = len(analysis.netlists)
            netlist_noun = "netlist" if count == 1 else "netlists"
            lines.append("")
            lines.append(f"### {_escape(analysis.repo)} ({count} {netlist_noun})")
            lines.append("")
            lines.extend(_table_lines(analysis.boards))
        return "\n".join(lines) + "\n"

    noun = "board" if len(results) == 1 else "boards"
    lines = [f"## PCB analysis ({len(results)} {noun})", "", *_table_lines(results)]
    return "\n".join(lines) + "\n"
