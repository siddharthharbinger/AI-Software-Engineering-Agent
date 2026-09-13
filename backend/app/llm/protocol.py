from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union


@dataclass
class ChatMessage:
    role: str  # "system", "user", "assistant", "tool"
    content: str
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        data = {"role": self.role, "content": self.content}
        if self.name:
            data["name"] = self.name
        return data


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    latency_ms: float = 0.0
    usage: Optional[LLMUsage] = None
    finish_reason: Optional[str] = None
    raw: Optional[Dict[str, Any]] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Exception Hierarchy
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base exception for LLM operations."""
    def __init__(self, message: str, provider: Optional[str] = None):
        super().__init__(message)
        self.provider = provider


class RateLimitError(LLMError):
    """Raised when a provider responds with HTTP 429 / Rate Limit Exceeded."""
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: Optional[float] = None,
        provider: Optional[str] = None,
    ):
        super().__init__(message, provider=provider)
        self.retry_after = retry_after


class ProviderUnavailableError(LLMError):
    """Raised on 5xx errors or network connectivity failures."""
    def __init__(
        self,
        message: str = "Provider service unavailable",
        status_code: Optional[int] = None,
        provider: Optional[str] = None,
    ):
        super().__init__(message, provider=provider)
        self.status_code = status_code


class ProviderTimeoutError(LLMError):
    """Raised when a provider request times out."""
    def __init__(
        self,
        message: str = "Provider request timed out",
        provider: Optional[str] = None,
    ):
        super().__init__(message, provider=provider)


class AllProvidersExhaustedError(LLMError):
    """Raised when all configured providers fail or are in circuit-open cooldown."""
    def __init__(
        self,
        last_error: Optional[Exception] = None,
        attempted_providers: Optional[List[str]] = None,
    ):
        providers_str = ", ".join(attempted_providers or [])
        msg = f"All LLM providers exhausted. Attempted: [{providers_str}]. Last error: {last_error}"
        super().__init__(msg)
        self.last_error = last_error
        self.attempted_providers = attempted_providers or []


# ---------------------------------------------------------------------------
# Provider Protocol
# ---------------------------------------------------------------------------

class LLMProvider(Protocol):
    name: str
    priority: int  # lower = tried first
    model: str

    def is_configured(self) -> bool:
        """Returns True if the provider has all required configuration (e.g. valid API key or endpoint)."""
        ...

    async def complete(
        self,
        messages: List[Union[ChatMessage, Dict[str, Any]]],
        **kwargs: Any,
    ) -> LLMResponse:
        """Sends chat messages to the provider's /chat/completions endpoint."""
        ...
