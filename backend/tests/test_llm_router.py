import asyncio
import json
import time
from typing import Any, Dict, List, Optional
import httpx
import pytest

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
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.router import LLMRouter, create_default_router
from app.core.config import Settings


class MockProvider:
    """Mock implementation of LLMProvider for deterministic router testing."""

    def __init__(
        self,
        name: str,
        priority: int = 10,
        model: str = "mock-model",
        configured: bool = True,
        side_effect: Optional[Any] = None,
        return_content: str = "mock completion",
    ):
        self.name = name
        self.priority = priority
        self.model = model
        self._configured = configured
        self.side_effect = side_effect
        self.return_content = return_content
        self.call_count = 0
        self.last_call_messages: Optional[List[Any]] = None

    def is_configured(self) -> bool:
        return self._configured

    async def complete(self, messages: List[Any], **kwargs: Any) -> LLMResponse:
        self.call_count += 1
        self.last_call_messages = messages

        if isinstance(self.side_effect, Exception):
            raise self.side_effect
        if callable(self.side_effect):
            res = self.side_effect()
            if isinstance(res, Exception):
                raise res
            return res

        return LLMResponse(
            content=self.return_content,
            provider=self.name,
            model=self.model,
            latency_ms=12.5,
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_primary_provider_serves_request():
    """Verify provider with highest priority (lowest index) serves request when healthy."""
    p1 = MockProvider(name="groq", priority=0, return_content="groq response")
    p2 = MockProvider(name="openrouter", priority=1, return_content="openrouter response")
    router = LLMRouter(providers=[p1, p2])

    resp = await router.complete([ChatMessage(role="user", content="hello")])

    assert resp.provider == "groq"
    assert resp.content == "groq response"
    assert p1.call_count == 1
    assert p2.call_count == 0


@pytest.mark.asyncio
async def test_429_rate_limit_failover():
    """Verify router trips circuit on 429 and falls through to next provider."""
    p1 = MockProvider(
        name="groq",
        priority=0,
        side_effect=RateLimitError("Rate limit reached", retry_after=60, provider="groq"),
    )
    p2 = MockProvider(name="openrouter", priority=1, return_content="openrouter response")
    router = LLMRouter(providers=[p1, p2])

    resp = await router.complete([ChatMessage(role="user", content="hello")])

    assert resp.provider == "openrouter"
    assert resp.content == "openrouter response"
    assert p1.call_count == 1
    assert p2.call_count == 1

    status = router.get_status()
    assert status["groq"]["circuit_open"] is True
    assert status["groq"]["cooldown_remaining_seconds"] > 0
    assert status["openrouter"]["circuit_open"] is False


@pytest.mark.asyncio
async def test_circuit_breaker_skips_cooling_provider_on_subsequent_call():
    """Subsequent calls skip the cooling provider entirely without invoking complete()."""
    p1 = MockProvider(
        name="groq",
        priority=0,
        side_effect=RateLimitError("Rate limit reached", retry_after=90, provider="groq"),
    )
    p2 = MockProvider(name="gemini", priority=1, return_content="gemini response")
    router = LLMRouter(providers=[p1, p2])

    # First call: trips p1, falls over to p2
    resp1 = await router.complete([ChatMessage(role="user", content="call 1")])
    assert resp1.provider == "gemini"
    assert p1.call_count == 1
    assert p2.call_count == 1

    # Second call: p1 is in cooldown, should be skipped directly
    resp2 = await router.complete([ChatMessage(role="user", content="call 2")])
    assert resp2.provider == "gemini"
    assert p1.call_count == 1  # Not called again
    assert p2.call_count == 2


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_cooldown():
    """Provider circuit resets and accepts requests once cooldown elapses."""
    p1 = MockProvider(
        name="groq",
        priority=0,
        side_effect=RateLimitError("Temporary rate limit", retry_after=0.05, provider="groq"),
    )
    p2 = MockProvider(name="openrouter", priority=1, return_content="openrouter fallback")
    router = LLMRouter(providers=[p1, p2])

    # First call trips p1
    await router.complete([ChatMessage(role="user", content="call 1")])
    assert router.get_status()["groq"]["circuit_open"] is True

    # Allow cooldown (0.05s) to expire
    await asyncio.sleep(0.08)

    # Now make p1 healthy
    p1.side_effect = None
    p1.return_content = "groq recovered"

    resp = await router.complete([ChatMessage(role="user", content="call 2")])
    assert resp.provider == "groq"
    assert resp.content == "groq recovered"
    assert p1.call_count == 2
    assert router.get_status()["groq"]["circuit_open"] is False


@pytest.mark.asyncio
async def test_5xx_unavailable_and_timeout_failover():
    """Verify 5xx server errors and timeouts trip circuit and fail over."""
    p1 = MockProvider(
        name="groq",
        priority=0,
        side_effect=ProviderUnavailableError("Connection refused", status_code=503),
    )
    p2 = MockProvider(
        name="openrouter",
        priority=1,
        side_effect=ProviderTimeoutError("OpenRouter request timed out"),
    )
    p3 = MockProvider(name="gemini", priority=2, return_content="gemini success")
    router = LLMRouter(providers=[p1, p2, p3])

    resp = await router.complete([ChatMessage(role="user", content="fix bug")])

    assert resp.provider == "gemini"
    assert resp.content == "gemini success"
    assert p1.call_count == 1
    assert p2.call_count == 1
    assert p3.call_count == 1


@pytest.mark.asyncio
async def test_all_providers_exhausted_raises_error():
    """Verify AllProvidersExhaustedError is raised when all providers fail."""
    p1 = MockProvider(name="groq", priority=0, side_effect=ProviderUnavailableError("Down"))
    p2 = MockProvider(name="openrouter", priority=1, side_effect=RateLimitError("429"))
    router = LLMRouter(providers=[p1, p2])

    with pytest.raises(AllProvidersExhaustedError) as exc_info:
        await router.complete([ChatMessage(role="user", content="test")])

    err = exc_info.value
    assert "groq" in err.attempted_providers
    assert "openrouter" in err.attempted_providers
    assert isinstance(err.last_error, RateLimitError)


@pytest.mark.asyncio
async def test_unconfigured_providers_are_skipped():
    """Providers marked unconfigured (e.g. missing API keys) are skipped without error."""
    p1 = MockProvider(name="groq", priority=0, configured=False)
    p2 = MockProvider(name="gemini", priority=1, configured=True, return_content="gemini active")
    router = LLMRouter(providers=[p1, p2])

    resp = await router.complete([ChatMessage(role="user", content="test")])
    assert resp.provider == "gemini"
    assert p1.call_count == 0
    assert p2.call_count == 1


@pytest.mark.asyncio
async def test_dynamic_priority_reordering():
    """Verify router can dynamically update provider priority order."""
    p1 = MockProvider(name="groq", priority=0, return_content="groq")
    p2 = MockProvider(name="gemini", priority=1, return_content="gemini")
    router = LLMRouter(providers=[p1, p2])

    # Default order: groq first
    r1 = await router.complete([ChatMessage(role="user", content="1")])
    assert r1.provider == "groq"

    # Reorder dynamically: gemini first
    router.set_priority(["gemini", "groq"])
    r2 = await router.complete([ChatMessage(role="user", content="2")])
    assert r2.provider == "gemini"


@pytest.mark.asyncio
async def test_openai_compat_provider_success():
    """Test OpenAICompatProvider with mock HTTP transport returning valid JSON completion."""
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["model"] == "test-model"
        assert len(data["messages"]) == 1
        assert request.headers["Authorization"] == "Bearer mock-key"

        mock_body = {
            "id": "chatcmpl-123",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello from mock LLM!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            },
        }
        return httpx.Response(200, json=mock_body)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key="mock-key",
        model="test-model",
        client=mock_client,
    )

    assert provider.is_configured() is True
    response = await provider.complete([ChatMessage(role="user", content="Hi")])

    assert response.provider == "groq"
    assert response.content == "Hello from mock LLM!"
    assert response.usage.total_tokens == 20
    assert response.latency_ms > 0
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_openai_compat_provider_429_with_retry_after():
    """Test OpenAICompatProvider translates 429 response and extracts Retry-After header."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text='{"error": {"message": "Rate limit reached, try again in 45.0s"}}',
            headers={"Retry-After": "45"},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatProvider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key="mock-key",
        model="test-model",
        client=mock_client,
    )

    with pytest.raises(RateLimitError) as exc_info:
        await provider.complete([ChatMessage(role="user", content="Hi")])

    assert exc_info.value.retry_after == 45.0
    assert exc_info.value.provider == "groq"
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_openai_compat_requires_api_key():
    """Verify provider is NOT configured if api_key is missing (no dead fallback branches)."""
    p_no_key = OpenAICompatProvider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key="",
        model="llama-3.3-70b-versatile",
    )
    assert p_no_key.is_configured() is False


@pytest.mark.asyncio
async def test_create_default_router_from_settings():
    """Verify default router factory instantiates 3 providers in configured order."""
    settings = Settings(
        LLM_PROVIDER_PRIORITY="gemini,groq,openrouter",
        GEMINI_API_KEY="test-gemini-key",
        GROQ_API_KEY="test-groq-key",
    )
    router = create_default_router(settings)

    providers = router.providers
    assert len(providers) == 3
    # Highest priority first
    assert providers[0].name == "gemini"
    assert providers[1].name == "groq"
    assert providers[2].name == "openrouter"

    # Verify configured check
    assert router._providers["gemini"].is_configured() is True
    assert router._providers["groq"].is_configured() is True
    assert router._providers["openrouter"].is_configured() is False  # No key


@pytest.mark.asyncio
async def test_status_telemetry_and_failover_accounting():
    """Verify router status dictionary exposes circuit state, failure counts, and cooldowns."""
    p1 = MockProvider(
        name="groq",
        priority=0,
        side_effect=RateLimitError("Rate limited on groq", retry_after=30, provider="groq"),
    )
    p2 = MockProvider(name="gemini", priority=1, return_content="gemini success")
    router = LLMRouter(providers=[p1, p2])

    resp = await router.complete([ChatMessage(role="user", content="ping")])
    assert resp.provider == "gemini"

    status = router.get_status()
    groq_stat = status["groq"]
    gemini_stat = status["gemini"]

    assert groq_stat["circuit_open"] is True
    assert groq_stat["failure_count"] == 1
    assert "Rate Limit" in groq_stat["last_failure_reason"]
    assert groq_stat["cooldown_remaining_seconds"] > 0

    assert gemini_stat["circuit_open"] is False
    assert gemini_stat["success_count"] == 1
    assert gemini_stat["failure_count"] == 0
