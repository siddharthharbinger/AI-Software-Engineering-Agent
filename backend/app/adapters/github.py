import re
from typing import Any

import httpx

from app.adapters.protocol import Diff, GitHostAdapter, PullRequest
from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class GitHubAdapter(GitHostAdapter):
    """GitHub API adapter implementing GitHostAdapter."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        client: httpx.AsyncClient | None = None,
    ):
        settings: Settings = get_settings()
        self._token = token if token is not None else settings.GITHUB_TOKEN
        self._base_url = base_url.rstrip("/")
        self._client = client

    @property
    def provider_name(self) -> str:
        return "github"

    def _get_headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-SWE-Agent-Platform",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        req_headers = headers or self._get_headers()

        if self._client is not None:
            resp = await self._client.request(method, url, headers=req_headers, json=json_data, timeout=30.0)
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(method, url, headers=req_headers, json=json_data)

        resp.raise_for_status()
        return resp

    async def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        path = f"/repos/{repo}/pulls/{pr_number}"
        resp = await self._request("GET", path)
        data = resp.json()

        return PullRequest(
            number=data["number"],
            title=data.get("title", ""),
            body=data.get("body"),
            head_branch=data["head"]["ref"],
            base_branch=data["base"]["ref"],
            head_sha=data["head"]["sha"],
            author=data.get("user", {}).get("login", ""),
            html_url=data.get("html_url", ""),
            raw_data=data,
        )

    async def get_diff(self, repo: str, pr_number: int) -> Diff:
        path = f"/repos/{repo}/pulls/{pr_number}"
        headers = self._get_headers(accept="application/vnd.github.v3.diff")
        resp = await self._request("GET", path, headers=headers)
        diff_text = resp.text

        files_changed = []
        additions = 0
        deletions = 0

        for line in diff_text.splitlines():
            if line.startswith("diff --git a/"):
                match = re.match(r"^diff --git a/(.*?) b/(.*)$", line)
                if match:
                    files_changed.append(match.group(2))
            elif line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return Diff(
            raw_diff=diff_text,
            files_changed=list(dict.fromkeys(files_changed)),
            additions=additions,
            deletions=deletions,
        )

    async def post_pr_review_comment(
        self,
        repo: str,
        pr_number: int,
        file: str,
        line: int,
        body: str,
    ) -> None:
        pr = await self.get_pull_request(repo, pr_number)
        path = f"/repos/{repo}/pulls/{pr_number}/comments"
        payload = {
            "body": body,
            "commit_id": pr.head_sha,
            "path": file,
            "line": line,
            "side": "RIGHT",
        }
        await self._request("POST", path, json_data=payload)

    async def create_pr_review(
        self,
        repo: str,
        pr_number: int,
        event: str,
        body: str,
        comments: list[dict[str, Any]] | None = None,
    ) -> None:
        path = f"/repos/{repo}/pulls/{pr_number}/reviews"
        payload: dict[str, Any] = {
            "body": body,
            "event": event,
        }
        if comments:
            payload["comments"] = comments
        await self._request("POST", path, json_data=payload)

    def clone_url(self, repo: str) -> str:
        if self._token:
            return f"https://x-access-token:{self._token}@github.com/{repo}.git"
        return f"https://github.com/{repo}.git"
