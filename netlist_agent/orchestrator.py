from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .github_client import GitHubClient
from .models import BoardNetlist
from .pcb_extractors import extract_from_file


class NetlistAgent:
    def __init__(self, github: GitHubClient | None = None) -> None:
        self.github = github or GitHubClient()

    def run(self, query: str, limit: int = 5) -> list[BoardNetlist]:
        repos = self.github.search_pcb_repos(query=query, limit=limit)
        results: list[BoardNetlist] = []

        for repo in repos:
            with tempfile.TemporaryDirectory(prefix="netlist-agent-") as tmp:
                local_repo = Path(tmp) / repo.full_name.replace("/", "_")
                self._shallow_clone(repo.clone_url, local_repo)
                results.extend(self._collect_netlists(repo.full_name, local_repo))

        return results

    def save_json(self, netlists: list[BoardNetlist], output_file: Path) -> None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps([nl.to_dict() for nl in netlists], indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _shallow_clone(clone_url: str, local_repo: Path) -> None:
        if shutil.which("git") is None:
            raise RuntimeError("git is required for cloning repositories")
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(local_repo)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _collect_netlists(repo_name: str, root: Path) -> list[BoardNetlist]:
        netlists: list[BoardNetlist] = []
        for file in root.rglob("*"):
            if not file.is_file():
                continue
            parsed = extract_from_file(repo_name, file)
            if parsed:
                netlists.append(parsed)
        return netlists
