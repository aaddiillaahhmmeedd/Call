"""GitHub-flavored markdown summary of a PCB analysis run.

Produces a compact PR-comment body: a status header, a metrics table,
the unrouted nets, and collapsible ERC/DRC issue lists. Pipe characters
in user-supplied text are escaped so markdown table cells cannot break.
"""

from __future__ import annotations

from typing import Any

_MAX_UNROUTED_NETS = 10
_MAX_ISSUE_LINES = 20


def _escape(value: Any) -> str:
    """Escape markdown table delimiters in untrusted text."""
    return str(value).replace("|", "\\|")


def _num(value: Any) -> str:
    """Format a number compactly (trim trailing zeros on floats)."""
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _status_emoji(completion: float, erc_count: int, drc_count: int) -> str:
    if completion < 50 or erc_count:
        return "❌"
    if completion >= 100 and not drc_count:
        return "✅"
    return "⚠️"


def _unrouted_section(nets: list[dict[str, Any]]) -> list[str]:
    unrouted = [n for n in nets if not n.get("fully_routed", True)]
    if not unrouted:
        return []
    lines = ["", "**Unrouted nets**", ""]
    for net in unrouted[:_MAX_UNROUTED_NETS]:
        count = len(net.get("airwires") or [])
        noun = "airwire" if count == 1 else "airwires"
        lines.append(
            f"- {_escape(net.get('net', ''))} — {count} {noun}, "
            f"{_num(net.get('unrouted_length_mm', 0))} mm"
        )
    overflow = len(unrouted) - _MAX_UNROUTED_NETS
    if overflow > 0:
        lines.append(f"- …and {overflow} more")
    return lines


def _details_section(label: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = ["", "<details>", f"<summary>{label} ({len(rows)})</summary>", ""]
    for row in rows[:_MAX_ISSUE_LINES]:
        lines.append(f"- {_escape(row.get('message', ''))}")
    overflow = len(rows) - _MAX_ISSUE_LINES
    if overflow > 0:
        lines.append(f"- …and {overflow} more")
    lines.extend(["", "</details>"])
    return lines


def markdown_summary(
    title: str,
    ratsnest: dict[str, Any],
    erc_issues: list[dict[str, Any]] | None = None,
    drc_violations: list[dict[str, Any]] | None = None,
) -> str:
    """Render a GitHub-flavored markdown summary suitable for a PR comment.

    ``ratsnest`` is :meth:`~netlist_agent.ratsnest.RatsnestReport.to_dict`;
    ``erc_issues`` / ``drc_violations`` are lists of ``ErcIssue.to_dict()`` /
    ``DrcViolation.to_dict()`` (``None`` or empty omits that section).
    """
    erc = erc_issues or []
    drc = drc_violations or []
    completion = float(ratsnest.get("completion_pct", 0.0))
    routed = ratsnest.get("nets_fully_routed", 0)
    total = ratsnest.get("nets_total", 0)

    lines = [
        f"### {_status_emoji(completion, len(erc), len(drc))} {_escape(title)}",
        "",
        "| Routing | Airwires | Unrouted (mm) | ERC | DRC |",
        "| --- | --- | --- | --- | --- |",
        f"| {routed}/{total} ({_num(completion)}%) "
        f"| {_num(ratsnest.get('airwire_count', 0))} "
        f"| {_num(ratsnest.get('unrouted_length_mm', 0))} "
        f"| {len(erc)} | {len(drc)} |",
    ]
    lines.extend(_unrouted_section(ratsnest.get("nets", [])))
    if erc:
        lines.extend(_details_section("ERC issues", erc))
    if drc:
        lines.extend(_details_section("DRC violations", drc))
    return "\n".join(lines) + "\n"
