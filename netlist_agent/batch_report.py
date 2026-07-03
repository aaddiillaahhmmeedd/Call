"""Standalone single-file HTML dashboard for a batch/mining analysis run.

Everything is inlined (CSS plus a small vanilla-JS block) so the output can
be attached to a PR/issue or opened from disk with no external assets. The
page shows headline stat tiles and one card per board with a completion
meter; with JavaScript enabled a search box live-filters the cards by board
path, and without it the page reads as a plain static report. All data
values are escaped with html.escape.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pipeline import BoardResult, RepoAnalysis

# Palette and tile/card look copied from report.py so both pages match.
_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #0b1220;
  color: #e8edf5;
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 32px 24px 64px; }
header h1 { margin: 0 0 4px; font-size: 26px; font-weight: 650; letter-spacing: -0.01em; }
.generated { margin: 0 0 28px; color: #8fa0b8; font-size: 13px; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin: 0 0 24px; }
.tile {
  flex: 1 1 150px;
  background: #121c30;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 14px 18px 16px;
}
.tile .label { font-size: 12px; color: #8fa0b8; letter-spacing: 0.02em; }
.tile .value { margin-top: 2px; font-size: 32px; font-weight: 600; line-height: 1.15; }
.value.ok { color: #34c759; }
.value.bad { color: #e05656; }
h2.repo { margin: 28px 0 12px; font-size: 17px; font-weight: 600; color: #c7d2e4; }
.card {
  background: #121c30;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 20px;
  margin: 0 0 16px;
}
.card h3 { margin: 0 0 10px; font-size: 15px; font-weight: 600; color: #c7d2e4; }
.card h3 .status { margin-right: 6px; }
.bar {
  height: 8px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4px;
  overflow: hidden;
  margin: 0 0 10px;
}
.bar .fill { height: 100%; border-radius: 4px; }
.fill.ok { background: #34c759; }
.fill.warn { background: #fab219; }
.fill.bad { background: #e05656; }
.metrics { margin: 0; color: #8fa0b8; font-size: 13.5px; font-variant-numeric: tabular-nums; }
.error { margin: 0; color: #e05656; font-size: 13.5px; }
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 16px; }
#board-filter {
  padding: 6px 10px;
  background: #0b1220;
  color: #e8edf5;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  font: inherit;
  font-size: 13px;
  min-width: 240px;
}
""".strip()

# Live-filter the board cards by path substring. Static script, no data
# interpolation; the page degrades to the plain static dashboard when
# JavaScript is unavailable.
_SCRIPT = """
(function () {
  var input = document.getElementById("board-filter");
  if (!input) return;
  var filterCards = function () {
    var query = input.value.toLowerCase();
    document.querySelectorAll(".board").forEach(function (card) {
      var path = (card.getAttribute("data-path") || "").toLowerCase();
      card.style.display = path.indexOf(query) === -1 ? "none" : "";
    });
  };
  input.addEventListener("input", filterCards);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { input.value = ""; filterCards(); }
  });
})();
""".strip()


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _num(value: Any) -> str:
    """Format a number compactly (trim trailing zeros on floats)."""
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _tile(label: str, value: str, tone: str = "") -> str:
    cls = f"value {tone}".rstrip()
    return (
        '<div class="tile">'
        f'<div class="label">{_esc(label)}</div>'
        f'<div class="{cls}">{_esc(value)}</div>'
        "</div>"
    )


def _completion(result: BoardResult) -> float:
    assert result.ratsnest is not None
    return float(result.ratsnest.get("completion_pct", 0.0))


def _is_error(result: BoardResult) -> bool:
    return result.error is not None or result.ratsnest is None


def _status_emoji(result: BoardResult) -> str:
    # Thresholds mirror pipeline._status_cell (this pipeline runs no ERC).
    if _is_error(result):
        return "❌"
    completion = _completion(result)
    if completion < 50:
        return "❌"
    if completion >= 100 and not result.drc:
        return "✅"
    return "⚠️"


def _completion_tone(pct: float) -> str:
    if pct >= 99.5:
        return "ok"
    if pct >= 80:
        return "warn"
    return "bad"


def _sorted_boards(results: list[BoardResult]) -> list[BoardResult]:
    """Errors first, then ascending completion; path breaks ties."""

    def key(result: BoardResult) -> tuple[int, float, str]:
        if _is_error(result):
            return (0, 0.0, result.path)
        return (1, _completion(result), result.path)

    return sorted(results, key=key)


def _board_card(result: BoardResult) -> str:
    path = _esc(result.path)
    if _is_error(result):
        body = f'<p class="error">{_esc(result.error or "no data")}</p>'
    else:
        ratsnest = result.ratsnest
        assert ratsnest is not None
        completion = min(100.0, max(0.0, _completion(result)))
        airwires = _num(ratsnest.get("airwire_count", 0))
        unrouted = _num(ratsnest.get("unrouted_length_mm", 0))
        body = (
            f'<div class="bar"><div class="fill {_completion_tone(completion)}"'
            f' style="width:{_num(completion)}%"></div></div>'
            f'<p class="metrics">{_num(completion)}% routed · {airwires} airwires · '
            f"{unrouted} unrouted mm · {len(result.drc)} DRC</p>"
        )
    return (
        f'<section class="card board" data-path="{path}">'
        f'<h3><span class="status">{_status_emoji(result)}</span>{path}</h3>'
        f"{body}</section>"
    )


def _stat_tiles(results: list[BoardResult]) -> str:
    fully_routed = sum(
        1 for result in results if not _is_error(result) and _completion(result) >= 100
    )
    with_drc = sum(1 for result in results if result.drc)
    errors = sum(1 for result in results if result.error is not None)
    return "".join(
        (
            _tile("Boards analyzed", str(len(results))),
            _tile("Fully routed", str(fully_routed)),
            _tile("Boards with DRC violations", str(with_drc), "ok" if not with_drc else "bad"),
            _tile("Errors", str(errors), "ok" if not errors else "bad"),
        )
    )


def render_batch_report(
    title: str,
    results: list[BoardResult],
    repo_results: list[RepoAnalysis] | None = None,
) -> str:
    """Render a fully self-contained HTML dashboard page (no external assets).

    When ``repo_results`` is given, ``results`` is ignored and the cards are
    grouped under one heading per repo (mirroring ``index_markdown``).
    """
    if repo_results is not None:
        boards = [board for analysis in repo_results for board in analysis.boards]
        sections: list[str] = []
        for analysis in repo_results:
            count = len(analysis.netlists)
            noun = "netlist" if count == 1 else "netlists"
            sections.append(f'<h2 class="repo">{_esc(analysis.repo)} ({count} {noun})</h2>')
            sections.extend(_board_card(board) for board in _sorted_boards(analysis.boards))
        cards = "".join(sections)
    else:
        boards = results
        cards = "".join(_board_card(board) for board in _sorted_boards(boards))

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    controls = (
        '<div class="controls">'
        '<input id="board-filter" type="search" placeholder="filter boards…">'
        "</div>"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>\n{_CSS}\n</style>\n</head>\n<body>\n"
        '<div class="wrap">\n'
        f"<header><h1>{_esc(title)}</h1>"
        f'<p class="generated">Generated on {_esc(generated)}</p></header>\n'
        f'<div class="tiles">{_stat_tiles(boards)}</div>\n'
        f"{controls}\n"
        f"{cards}\n"
        "</div>\n"
        f"<script>\n{_SCRIPT}\n</script>\n"
        "</body>\n</html>\n"
    )


def write_batch_report(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
