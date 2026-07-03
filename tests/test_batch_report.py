from pathlib import Path

from netlist_agent.batch_report import render_batch_report, write_batch_report
from netlist_agent.pipeline import BoardResult, RepoAnalysis


def _ratsnest(pct: float, airwires: int = 0, unrouted: float = 0.0) -> dict:
    return {
        "nets_total": 3,
        "nets_fully_routed": int(3 * pct / 100),
        "completion_pct": pct,
        "airwire_count": airwires,
        "unrouted_length_mm": unrouted,
        "nets": [],
    }


def _results() -> list[BoardResult]:
    return [
        BoardResult(path="good.kicad_pcb", ratsnest=_ratsnest(100.0), drc=[]),
        BoardResult(
            path="partial.kicad_pcb",
            ratsnest=_ratsnest(66.7, airwires=2, unrouted=12.5),
            drc=[{"kind": "clearance", "message": "too close", "x": 1, "y": 2}],
        ),
        BoardResult(path="broken<b>.kicad_pcb", ratsnest=None, drc=[], error="bad <b>syntax</b>"),
    ]


def test_cards_statuses_and_escaping() -> None:
    html = render_batch_report("scan", _results())

    assert "scan" in html
    assert html.count('class="card board"') == 3
    assert "✅" in html and "⚠️" in html and "❌" in html
    assert "bad <b>syntax</b>" not in html  # escaped
    assert "bad &lt;b&gt;syntax&lt;/b&gt;" in html
    assert "%" in html  # completion bar / stats render percentages


def test_repo_grouping_ignores_flat_list() -> None:
    results = _results()
    analysis = RepoAnalysis(repo="octo/pcb", netlists=[{"repo": "octo/pcb"}], boards=results[:2])
    html = render_batch_report("mining run", [], repo_results=[analysis])

    assert 'class="repo"' in html
    assert "octo/pcb" in html
    assert html.count('class="card board"') == 2  # only the repo's boards


def test_filter_input_and_script_once() -> None:
    html = render_batch_report("scan", _results())
    assert html.count('id="board-filter"') == 1
    assert html.count("<script>") == 1


def test_write_batch_report_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dash.html"
    html = render_batch_report("scan", _results())
    write_batch_report(target, html)
    assert target.read_text(encoding="utf-8") == html
