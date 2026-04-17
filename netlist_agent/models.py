from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Component:
    """Normalized PCB component from a repository design file."""

    reference: str
    value: str | None = None
    footprint: str | None = None
    library: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NetConnection:
    """A net and all participating component references."""

    net_name: str
    references: list[str]


@dataclass(slots=True)
class BoardNetlist:
    """Netlist output for a PCB project/repository."""

    repo: str
    path: str
    components: list[Component]
    nets: list[NetConnection]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "path": self.path,
            "components": [c.__dict__ for c in self.components],
            "nets": [n.__dict__ for n in self.nets],
        }
