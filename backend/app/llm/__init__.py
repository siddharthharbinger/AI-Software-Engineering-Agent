"""LLM Provider and Failover Router Package."""
from app.llm.protocol import (
    ChatMessage,
    LLMResponse,
    LLMUsage,
    LLMProvider,
    LLMError,
    RateLimitError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    AllProvidersExhaustedError,
)
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.router import LLMRouter, create_default_router

__all__ = [
    "ChatMessage",
    "LLMResponse",
    "LLMUsage",
    "LLMProvider",
    "LLMError",
    "RateLimitError",
    "ProviderUnavailableError",
    "ProviderTimeoutError",
    "AllProvidersExhaustedError",
    "OpenAICompatProvider",
    "LLMRouter",
    "create_default_router",
]
