"""Netlist agent package.

PCB toolkit: netlisting, ratsnest, placement, routing, DRC, and
fabrication outputs.

The public API is exposed lazily (PEP 562) so that ``import netlist_agent``
stays fast: submodules are only imported when one of their attributes is
first accessed.
"""

from importlib import import_module
from typing import Any

__version__ = "0.2.0"

# Public name -> (submodule, attribute) for lazy resolution.
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "Board": (".kicad_pcb", "Board"),
    "Pad": (".kicad_pcb", "Pad"),
    "TrackSegment": (".kicad_pcb", "TrackSegment"),
    "Via": (".kicad_pcb", "Via"),
    "Zone": (".kicad_pcb", "Zone"),
    "ArcTrack": (".kicad_pcb", "ArcTrack"),
    "Footprint": (".kicad_pcb", "Footprint"),
    "NetClass": (".kicad_pcb", "NetClass"),
    "parse_board": (".kicad_pcb", "parse_board"),
    "parse_eagle_board": (".eagle_brd", "parse_eagle_board"),
    "compute_ratsnest": (".ratsnest", "compute_ratsnest"),
    "RatsnestReport": (".ratsnest", "RatsnestReport"),
    "check_board": (".drc", "check_board"),
    "compare": (".erc", "compare"),
    "route_board": (".autoroute", "route_board"),
    "optimize_placement": (".placement", "optimize_placement"),
    "generate_pour": (".pour", "generate_pour"),
    "export_gerbers": (".gerber", "export_gerbers"),
    "export_dsn": (".dsn", "export_dsn"),
    "parse_ses": (".ses", "parse_ses"),
    "apply_ses": (".ses", "apply_ses"),
    "audit_board": (".audit", "audit_board"),
    "analyze_tree": (".pipeline", "analyze_tree"),
}

__all__ = ["__version__", *_LAZY_ATTRS]


def __getattr__(name: str) -> Any:
    try:
        module_name, attr = _LAZY_ATTRS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return getattr(import_module(module_name, __name__), attr)
