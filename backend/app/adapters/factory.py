
import httpx

from app.adapters.gitea import GiteaAdapter
from app.adapters.github import GitHubAdapter
from app.adapters.protocol import GitHostAdapter

_adapter_registry: dict[str, GitHostAdapter] = {}


def get_git_adapter(
    provider: str,
    client: httpx.AsyncClient | None = None,
    force_new: bool = False,
) -> GitHostAdapter:
    """Retrieve or instantiate a GitHostAdapter for the given provider ('github' or 'gitea')."""
    normalized = provider.strip().lower()

    if not force_new and client is None and normalized in _adapter_registry:
        return _adapter_registry[normalized]

    if normalized == "github":
        adapter = GitHubAdapter(client=client)
    elif normalized == "gitea":
        adapter = GiteaAdapter(client=client)
    else:
        raise ValueError(f"Unsupported git provider '{provider}'. Must be 'github' or 'gitea'.")

    if client is None and not force_new:
        _adapter_registry[normalized] = adapter

    return adapter


def set_git_adapter(provider: str, adapter: GitHostAdapter) -> None:
    """Register or mock a GitHostAdapter instance for testing."""
    _adapter_registry[provider.strip().lower()] = adapter


def reset_git_adapters() -> None:
    """Reset registered adapters (useful for unit test isolation)."""
    _adapter_registry.clear()
