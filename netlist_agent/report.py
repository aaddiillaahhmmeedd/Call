"""Standalone single-file HTML report for a PCB analysis run.

Everything is inlined (CSS, board SVG) so the output can be attached to a
PR/issue or opened from disk with no external assets and no JavaScript.
All data values are escaped with html.escape; the ``svg`` argument is the
one trusted input — it is markup this package rendered itself.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
.value.warn { color: #fab219; }
.value.bad { color: #e05656; }
.card {
  background: #121c30;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 20px;
  margin: 0 0 24px;
}
.card h2 { margin: 0 0 12px; font-size: 15px; font-weight: 600; color: #c7d2e4; }
.svg-panel svg { display: block; max-width: 100%; height: auto; border-radius: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th {
  padding: 8px 10px;
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: #8fa0b8;
  border-bottom: 1px solid rgba(255, 255, 255, 0.14);
}
td { padding: 8px 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.06); }
tr:last-child td { border-bottom: none; }
th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.status-ok { color: #34c759; white-space: nowrap; }
td.status-bad { color: #e05656; white-space: nowrap; }
.empty { margin: 0; color: #8fa0b8; font-size: 13.5px; }
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


def _completion_tone(pct: float) -> str:
    if pct >= 99.5:
        return "ok"
    if pct >= 80:
        return "warn"
    return "bad"


def _net_rows(nets: list[dict[str, Any]]) -> str:
    def fully_routed(net: dict[str, Any]) -> bool:
        if "fully_routed" in net:
            return bool(net["fully_routed"])
        return not net.get("airwires")

    rows: list[str] = []
    for net in sorted(nets, key=fully_routed):  # unrouted (False) first
        airwire_count = len(net.get("airwires") or [])
        if fully_routed(net):
            status_cls, status = "status-ok", "✓ routed"
        else:
            noun = "airwire" if airwire_count == 1 else "airwires"
            status_cls, status = "status-bad", f"✗ {airwire_count} {noun}"
        rows.append(
            "<tr>"
            f"<td>{_esc(net.get('net', ''))}</td>"
            f'<td class="num">{_esc(net.get("pads", 0))}</td>'
            f'<td class="num">{_esc(net.get("clusters", 0))}</td>'
            f'<td class="num">{_esc(_num(net.get("routed_length_mm", 0)))}</td>'
            f'<td class="num">{_esc(_num(net.get("unrouted_length_mm", 0)))}</td>'
            f'<td class="{status_cls}">{_esc(status)}</td>'
            "</tr>"
        )
    return "".join(rows)


def _issue_section(title: str, rows: list[dict[str, Any]], with_location: bool) -> str:
    body = [f'<section class="card"><h2>{_esc(title)}</h2>']
    if not rows:
        body.append('<p class="empty">None found</p>')
    else:
        location_th = "<th>Location</th>" if with_location else ""
        body.append(f"<table><thead><tr><th>Kind</th><th>Message</th>{location_th}</tr></thead><tbody>")
        for row in rows:
            location_td = ""
            if with_location:
                location = f"{_num(row.get('x', 0))}, {_num(row.get('y', 0))}"
                location_td = f'<td class="num">{_esc(location)}</td>'
            body.append(
                "<tr>"
                f"<td>{_esc(row.get('kind', ''))}</td>"
                f"<td>{_esc(row.get('message', ''))}</td>"
                f"{location_td}"
                "</tr>"
            )
        body.append("</tbody></table>")
    body.append("</section>")
    return "".join(body)


def render_report(
    title: str,
    ratsnest: dict[str, Any],
    erc_issues: list[dict[str, Any]],
    drc_violations: list[dict[str, Any]],
    svg: str | None = None,
) -> str:
    """Render a fully self-contained HTML report page (no external assets)."""
    completion = float(ratsnest.get("completion_pct", 0.0))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    tiles = "".join(
        (
            _tile("Routing completion", f"{_num(completion)}%", _completion_tone(completion)),
            _tile("Airwires", _num(ratsnest.get("airwire_count", 0))),
            _tile("Unrouted mm", _num(ratsnest.get("unrouted_length_mm", 0))),
            _tile("ERC issues", str(len(erc_issues)), "ok" if not erc_issues else "bad"),
            _tile("DRC violations", str(len(drc_violations)), "ok" if not drc_violations else "bad"),
        )
    )

    svg_panel = (
        f'<section class="card svg-panel"><h2>Board</h2>{svg}</section>' if svg else ""
    )

    nets: list[dict[str, Any]] = ratsnest.get("nets", [])
    nets_table = (
        '<section class="card"><h2>Nets</h2>'
        "<table><thead><tr>"
        '<th>Net</th><th class="num">Pads</th><th class="num">Clusters</th>'
        '<th class="num">Routed (mm)</th><th class="num">Unrouted (mm)</th><th>Status</th>'
        f"</tr></thead><tbody>{_net_rows(nets)}</tbody></table></section>"
        if nets
        else '<section class="card"><h2>Nets</h2><p class="empty">None found</p></section>'
    )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>\n{_CSS}\n</style>\n</head>\n<body>\n"
        '<div class="wrap">\n'
        f"<header><h1>{_esc(title)}</h1>"
        f'<p class="generated">Generated on {_esc(generated)}</p></header>\n'
        f'<div class="tiles">{tiles}</div>\n'
        f"{svg_panel}\n"
        f"{nets_table}\n"
        f"{_issue_section('ERC issues', erc_issues, with_location=False)}\n"
        f"{_issue_section('DRC violations', drc_violations, with_location=True)}\n"
        "</div>\n</body>\n</html>\n"
    )


def write_report(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
