from __future__ import annotations

import os
from dataclasses import dataclass

import requests


@dataclass(slots=True)
class RepoCandidate:
    full_name: str
    default_branch: str
    clone_url: str


class GitHubClient:
    """Thin GitHub API wrapper for searching PCB projects."""

    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/vnd.github+json"})
        token = token or os.getenv("GITHUB_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def search_pcb_repos(self, query: str, limit: int = 10) -> list[RepoCandidate]:
        q = f"{query} in:name,description,readme language:"
        params = {"q": q, "sort": "stars", "order": "desc", "per_page": limit}
        res = self.session.get(
            "https://api.github.com/search/repositories",
            params=params,
            timeout=self.timeout,
        )
        res.raise_for_status()
        payload = res.json()
        repos: list[RepoCandidate] = []
        for item in payload.get("items", []):
            repos.append(
                RepoCandidate(
                    full_name=item["full_name"],
                    default_branch=item["default_branch"],
                    clone_url=item["clone_url"],
                )
            )
        return repos
