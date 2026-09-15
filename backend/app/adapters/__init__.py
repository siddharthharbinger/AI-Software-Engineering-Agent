"""Git host adapters for GitHub and Gitea."""
from app.adapters.factory import get_git_adapter, reset_git_adapters, set_git_adapter
from app.adapters.gitea import GiteaAdapter
from app.adapters.github import GitHubAdapter
from app.adapters.protocol import Diff, GitHostAdapter, PullRequest

__all__ = [
    "Diff",
    "GitHostAdapter",
    "GitHubAdapter",
    "GiteaAdapter",
    "PullRequest",
    "get_git_adapter",
    "reset_git_adapters",
    "set_git_adapter",
]
