"""LLM Provider and Failover Router Package."""
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.protocol import (
    AllProvidersExhaustedError,
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    LLMUsage,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.llm.router import LLMRouter, create_default_router

__all__ = [
    "AllProvidersExhaustedError",
    "ChatMessage",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "LLMRouter",
    "LLMUsage",
    "OpenAICompatProvider",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RateLimitError",
    "create_default_router",
]
