"""Deterministic fuzz/property tests for the board parsers.

Contract under test: on arbitrary malformed input, tokenize returns a list,
parse_sexpr/parse_board raise at most ValueError, the Eagle parser raises at
most ValueError or ElementTree.ParseError, and the netlist extractors return
a result or None without raising. Everything is seeded — failures reproduce.
"""

import random
import string
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from netlist_agent.eagle_brd import parse_eagle_board
from netlist_agent.kicad_pcb import parse_board, parse_sexpr, tokenize
from netlist_agent.pcb_extractors import extract_from_file
from test_ratsnest import BOARD

_CHARS = string.printable + '""()\\' + "µΩ→"

EAGLE = """<?xml version="1.0"?>
<eagle version="9.6"><drawing><board>
<libraries><library name="l"><packages><package name="p">
<smd name="1" x="1" y="0" dx="0.8" dy="0.8"/></package></packages></library></libraries>
<elements><element name="R1" library="l" package="p" x="10" y="10"/></elements>
<signals><signal name="N"><contactref element="R1" pad="1"/>
<wire x1="0" y1="0" x2="5" y2="0" width="0.25" layer="1"/></signal></signals>
</board></drawing></eagle>"""


def _soup(rng: random.Random, max_len: int = 500) -> str:
    return "".join(rng.choice(_CHARS) for _ in range(rng.randrange(max_len)))


def _mutate(rng: random.Random, text: str) -> str:
    kind = rng.randrange(5)
    if not text:
        return text
    i = rng.randrange(len(text))
    j = min(len(text), i + rng.randrange(1, 40))
    if kind == 0:  # delete a slice
        return text[:i] + text[j:]
    if kind == 1:  # duplicate a slice
        return text[:j] + text[i:j] + text[j:]
    if kind == 2:  # swap two characters
        k = rng.randrange(len(text))
        chars = list(text)
        chars[i], chars[k] = chars[k], chars[i]
        return "".join(chars)
    if kind == 3:  # insert structural noise
        return text[:i] + rng.choice(['(', ')', '"', '((', '))', '"("']) + text[i:]
    return text[:i]  # truncate


def test_tokenizer_and_sexpr_never_crash() -> None:
    rng = random.Random(1)
    for index in range(200):
        text = _soup(rng)
        tokens = tokenize(text)
        assert isinstance(tokens, list)
        try:
            parse_sexpr(text)
        except ValueError:
            pass  # the documented failure mode
        except Exception as exc:  # pragma: no cover - only on regression
            pytest.fail(f"seed 1, soup {index}: parse_sexpr raised {type(exc).__name__}: {exc!r}")


def test_parse_board_on_mutations(tmp_path: Path) -> None:
    rng = random.Random(2)
    target = tmp_path / "mutant.kicad_pcb"
    failures: list[str] = []
    for index in range(300):
        mutant = _mutate(rng, BOARD.strip())
        target.write_text(mutant, encoding="utf-8")
        try:
            parse_board(target)
        except ValueError:
            pass
        except Exception as exc:
            failures.append(f"mutation {index}: {type(exc).__name__}: {str(exc)[:80]}")
    if failures:
        pytest.fail("parse_board crashes (seed 2):\n" + "\n".join(failures[:10]))


def test_eagle_parser_on_mutations(tmp_path: Path) -> None:
    rng = random.Random(3)
    target = tmp_path / "mutant.brd"
    failures: list[str] = []
    for index in range(200):
        mutant = _mutate(rng, EAGLE)
        target.write_text(mutant, encoding="utf-8")
        try:
            parse_eagle_board(target)
        except (ValueError, ET.ParseError):
            pass
        except Exception as exc:
            failures.append(f"mutation {index}: {type(exc).__name__}: {str(exc)[:80]}")
    if failures:
        pytest.fail("parse_eagle_board crashes (seed 3):\n" + "\n".join(failures[:10]))


def test_extractors_on_garbage(tmp_path: Path) -> None:
    rng = random.Random(4)
    for index in range(100):
        name = ["bom.csv", "board.net", "thing.xml"][index % 3]
        target = tmp_path / name
        target.write_text(_soup(rng, 300), encoding="utf-8")
        result = extract_from_file("fuzz/repo", target)
        assert result is None or result.repo == "fuzz/repo"


def test_numeric_torture(tmp_path: Path) -> None:
    target = tmp_path / "torture.kicad_pcb"
    target.write_text(
        '(kicad_pcb (version 20221018)\n'
        '  (net 0 "")\n'
        '  (net 1 "N")\n'
        '  (footprint "t:R" (at 1e300 -1e300 0.123456789012345678901234567890)\n'
        '    (property "Reference" "R1")\n'
        '    (pad "1" smd rect (at nan inf) (size 0.8 0.8) (net 1 "N")))\n'
        '  (segment (start -1e300 1e300) (end 0 0) (width 1e-300) (layer "F.Cu") (net 1))\n'
        ')\n',
        encoding="utf-8",
    )
    board = parse_board(target)  # parsing contract only: must not raise
    assert board.nets[1] == "N"
    # NaN/inf propagate into coordinates by design; downstream geometry is
    # not exercised here.
    assert len(board.segments) == 1
