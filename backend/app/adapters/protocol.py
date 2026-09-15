from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class PullRequest:
    """Normalized Pull Request data structure across Git hosting providers."""
    number: int
    title: str
    head_branch: str
    base_branch: str
    head_sha: str
    body: str | None = None
    author: str = ""
    html_url: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Diff:
    """Normalized PR Diff representation."""
    raw_diff: str
    files_changed: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0


@runtime_checkable
class GitHostAdapter(Protocol):
    """Unified interface for Git hosting providers (GitHub and Gitea)."""

    @property
    def provider_name(self) -> str:
        """Provider identifier (e.g. 'github', 'gitea')."""
        ...

    async def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        """Fetch pull request metadata."""
        ...

    async def get_diff(self, repo: str, pr_number: int) -> Diff:
        """Fetch raw diff and metadata for a pull request."""
        ...

    async def post_pr_review_comment(
        self,
        repo: str,
        pr_number: int,
        file: str,
        line: int,
        body: str,
    ) -> None:
        """Post an inline review comment on a specific line of a pull request."""
        ...

    async def create_pr_review(
        self,
        repo: str,
        pr_number: int,
        event: str,
        body: str,
        comments: list[dict[str, Any]] | None = None,
    ) -> None:
        """Submit a complete PR review verdict (e.g. COMMENT, REQUEST_CHANGES, APPROVE)."""
        ...

    def clone_url(self, repo: str) -> str:
        """Get git clone URL for sandbox checkout."""
        ...
