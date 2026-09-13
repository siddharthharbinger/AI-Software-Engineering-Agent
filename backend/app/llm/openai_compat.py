import time
from typing import Any, Dict, List, Optional, Union
import httpx

from app.llm.protocol import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    LLMUsage,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)


class OpenAICompatProvider(LLMProvider):
    """Unified provider client for any OpenAI-compatible /chat/completions endpoint:
    Ollama (local), Groq, OpenRouter, Google Gemini."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str = "",
        model: str = "",
        priority: int = 10,
        timeout: float = 45.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.name = name.lower().strip()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.priority = priority
        self.timeout = timeout
        self._client = client

    def is_configured(self) -> bool:
        """Check if required endpoint and credentials are provided."""
        if not self.base_url:
            return False
        # Ollama does not require an API key
        if self.name == "ollama":
            return bool(self.model)
        # All cloud providers require both API key and model
        return bool(self.api_key and self.model)

    def _get_endpoint_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self.name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/agent-platform"
            headers["X-Title"] = "AI Engineering Agent Platform"

        return headers

    def _format_messages(
        self, messages: List[Union[ChatMessage, Dict[str, Any]]]
    ) -> List[Dict[str, str]]:
        formatted: List[Dict[str, str]] = []
        for msg in messages:
            if isinstance(msg, ChatMessage):
                formatted.append(msg.to_dict())
            elif isinstance(msg, dict):
                formatted.append({"role": str(msg.get("role", "user")), "content": str(msg.get("content", ""))})
            else:
                formatted.append({"role": "user", "content": str(msg)})
        return formatted

    def _extract_retry_after(self, response: httpx.Response) -> Optional[float]:
        retry_header = response.headers.get("retry-after")
        if retry_header:
            try:
                return float(retry_header)
            except ValueError:
                pass
        # Fallback: check json body if provider included retry detail
        try:
            body = response.json()
            error_detail = body.get("error", {})
            if isinstance(error_detail, dict):
                message = error_detail.get("message", "")
                # Some providers include "please try again in X.XXs"
                if "try again in " in message:
                    part = message.split("try again in ")[1].split("s")[0].strip()
                    return float(part)
        except Exception:
            pass
        return None

    async def complete(
        self,
        messages: List[Union[ChatMessage, Dict[str, Any]]],
        **kwargs: Any,
    ) -> LLMResponse:
        endpoint = self._get_endpoint_url()
        headers = self._get_headers()
        payload = {
            "model": self.model,
            "messages": self._format_messages(messages),
        }
        # Allow caller overrides (temperature, max_tokens, etc.)
        for k, v in kwargs.items():
            if v is not None:
                payload[k] = v

        start_time = time.perf_counter()

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        should_close = self._client is None

        try:
            response = await client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Provider '{self.name}' timed out after {self.timeout}s: {exc}",
                provider=self.name,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"Connection error to provider '{self.name}': {exc}",
                provider=self.name,
            ) from exc
        finally:
            if should_close:
                await client.aclose()

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if response.status_code == 429:
            retry_after = self._extract_retry_after(response)
            raise RateLimitError(
                f"Rate limit exceeded (HTTP 429) for provider '{self.name}': {response.text}",
                retry_after=retry_after,
                provider=self.name,
            )

        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"Provider '{self.name}' returned server error HTTP {response.status_code}: {response.text}",
                status_code=response.status_code,
                provider=self.name,
            )

        if response.status_code != 200:
            raise LLMError(
                f"Provider '{self.name}' returned HTTP {response.status_code}: {response.text}",
                provider=self.name,
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
            finish_reason = choice.get("finish_reason")

            usage_data = data.get("usage", {})
            usage = LLMUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

            return LLMResponse(
                content=content,
                provider=self.name,
                model=self.model,
                latency_ms=elapsed_ms,
                usage=usage,
                finish_reason=finish_reason,
                raw=data,
            )
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(
                f"Failed to parse response from provider '{self.name}': {exc}",
                provider=self.name,
            ) from exc
