from pathlib import Path

from netlist_agent.pcb_extractors import extract_from_file


def test_parse_kicad_netlist(tmp_path: Path) -> None:
    net = tmp_path / "board.net"
    net.write_text(
        """
<export>
  <components>
    <comp ref="R1"><value>10k</value><footprint>R_0603</footprint></comp>
    <comp ref="C1"><value>100n</value></comp>
  </components>
  <nets>
    <net name="GND"><node ref="R1"/><node ref="C1"/></net>
  </nets>
</export>
""".strip(),
        encoding="utf-8",
    )

    parsed = extract_from_file("org/repo", net)
    assert parsed is not None
    assert len(parsed.components) == 2
    assert parsed.nets[0].net_name == "GND"
    assert parsed.nets[0].references == ["C1", "R1"]


def test_parse_bom_csv(tmp_path: Path) -> None:
    bom = tmp_path / "bom.csv"
    bom.write_text("Reference,Value,Footprint\nR1 R2,10k,R_0603\n", encoding="utf-8")

    parsed = extract_from_file("org/repo", bom)
    assert parsed is not None
    assert len(parsed.components) == 2
    assert {c.reference for c in parsed.components} == {"R1", "R2"}
