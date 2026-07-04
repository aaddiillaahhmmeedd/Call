"""The curated lazy public API surface stays resolvable, versioned, and light."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import netlist_agent


def test_all_names_resolve() -> None:
    for name in netlist_agent.__all__:
        obj = getattr(netlist_agent, name)
        assert callable(obj) or isinstance(obj, str), f"{name} did not resolve"


def test_version_matches_pyproject() -> None:
    assert netlist_agent.__version__ == "0.2.0"
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == netlist_agent.__version__


def test_unknown_attribute_raises() -> None:
    with pytest.raises(AttributeError):
        netlist_agent.does_not_exist


def test_import_is_lazy() -> None:
    """A bare import must not eagerly pull in heavy submodules."""
    check = "import sys, netlist_agent; assert 'netlist_agent.autoroute' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", check], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
