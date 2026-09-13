from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Union
import structlog

from app.core.config import Settings, get_settings
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.protocol import (
    AllProvidersExhaustedError,
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)

logger = structlog.get_logger("llm.router")


@dataclass
class CircuitBreaker:
    name: str
    is_open: bool = False
    cooldown_until: float = 0.0
    failure_count: int = 0
    success_count: int = 0
    last_failure_reason: Optional[str] = None
    last_tripped_at: Optional[float] = None

    def trip(self, cooldown_seconds: float, reason: str):
        now = time.time()
        self.is_open = True
        self.cooldown_until = now + cooldown_seconds
        self.last_tripped_at = now
        self.failure_count += 1
        self.last_failure_reason = reason

    def check_open(self) -> bool:
        """Returns True if the circuit is currently open and cooling down.
        If cooldown has elapsed, resets to False."""
        if not self.is_open:
            return False
        if time.time() >= self.cooldown_until:
            self.is_open = False
            self.cooldown_until = 0.0
            return False
        return True

    def record_success(self):
        self.success_count += 1
        self.is_open = False
        self.cooldown_until = 0.0


class LLMRouter:
    """Provider-agnostic router with automatic 4-way failover,
    per-provider circuit breaker cooldowns, and structured telemetry."""

    def __init__(
        self,
        providers: List[LLMProvider],
        default_rate_limit_cooldown: float = 60.0,
        default_unavailable_cooldown: float = 30.0,
    ):
        self._providers: Dict[str, LLMProvider] = {p.name.lower(): p for p in providers}
        self._circuits: Dict[str, CircuitBreaker] = {
            p.name.lower(): CircuitBreaker(name=p.name.lower()) for p in providers
        }
        self.default_rate_limit_cooldown = default_rate_limit_cooldown
        self.default_unavailable_cooldown = default_unavailable_cooldown

    @property
    def providers(self) -> List[LLMProvider]:
        """Returns providers ordered by priority (lower number = attempted first)."""
        return sorted(self._providers.values(), key=lambda p: p.priority)

    def set_priority(self, ordered_names: List[str]):
        """Dynamically reorder provider priorities."""
        for idx, name in enumerate(ordered_names):
            key = name.strip().lower()
            if key in self._providers:
                self._providers[key].priority = idx

    def _circuit_open(self, provider: LLMProvider) -> bool:
        circuit = self._circuits.get(provider.name.lower())
        if not circuit:
            return False
        return circuit.check_open()

    def _trip_circuit(self, provider: LLMProvider, cooldown: float, reason: str):
        circuit = self._circuits.get(provider.name.lower())
        if circuit:
            circuit.trip(cooldown_seconds=cooldown, reason=reason)
            logger.warning(
                "circuit_breaker_tripped",
                provider=provider.name,
                cooldown_seconds=cooldown,
                reason=reason,
            )

    def reset_circuits(self):
        """Manually reset all circuit breakers."""
        for circuit in self._circuits.values():
            circuit.is_open = False
            circuit.cooldown_until = 0.0

    def get_status(self) -> Dict[str, Any]:
        """Inspect status of all registered providers and their circuits."""
        status = {}
        now = time.time()
        for p in self.providers:
            circuit = self._circuits.get(p.name.lower())
            is_open = circuit.check_open() if circuit else False
            status[p.name] = {
                "priority": p.priority,
                "configured": p.is_configured(),
                "model": getattr(p, "model", "unknown"),
                "circuit_open": is_open,
                "cooldown_remaining_seconds": max(0.0, circuit.cooldown_until - now) if (circuit and is_open) else 0.0,
                "failure_count": circuit.failure_count if circuit else 0,
                "success_count": circuit.success_count if circuit else 0,
                "last_failure_reason": circuit.last_failure_reason if circuit else None,
            }
        return status

    async def complete(
        self,
        messages: List[Union[ChatMessage, Dict[str, Any]]],
        **kwargs: Any,
    ) -> LLMResponse:
        attempted_providers: List[str] = []
        last_error: Optional[Exception] = None
        candidates = self.providers

        for idx, provider in enumerate(candidates):
            p_name = provider.name.lower()

            if not provider.is_configured():
                logger.debug("skipping_unconfigured_provider", provider=p_name)
                continue

            if self._circuit_open(provider):
                circuit = self._circuits.get(p_name)
                remaining = (circuit.cooldown_until - time.time()) if circuit else 0
                logger.info(
                    "skipping_cooling_provider",
                    provider=p_name,
                    cooldown_remaining=round(remaining, 1),
                )
                continue

            attempted_providers.append(p_name)

            try:
                response = await provider.complete(messages, **kwargs)
                # Success: record in circuit breaker
                circuit = self._circuits.get(p_name)
                if circuit:
                    circuit.record_success()

                logger.info(
                    "llm_request_served",
                    provider=p_name,
                    model=response.model,
                    latency_ms=round(response.latency_ms, 2),
                    tokens=response.usage.total_tokens if response.usage else 0,
                )
                return response

            except RateLimitError as exc:
                cooldown = exc.retry_after or self.default_rate_limit_cooldown
                self._trip_circuit(provider, cooldown=cooldown, reason=f"Rate Limit (429): {exc}")
                last_error = exc

                # Find next eligible candidate for structured failover logging
                next_candidate = next(
                    (
                        c.name.lower()
                        for c in candidates[idx + 1 :]
                        if c.is_configured() and not self._circuit_open(c)
                    ),
                    None,
                )
                logger.warning(
                    "provider_failover",
                    from_provider=p_name,
                    to_provider=next_candidate or "none",
                    reason="rate_limited",
                    retry_after=cooldown,
                    error=str(exc),
                )
                continue

            except (ProviderUnavailableError, ProviderTimeoutError) as exc:
                cooldown = self.default_unavailable_cooldown
                self._trip_circuit(provider, cooldown=cooldown, reason=f"Unavailable/Timeout: {exc}")
                last_error = exc

                next_candidate = next(
                    (
                        c.name.lower()
                        for c in candidates[idx + 1 :]
                        if c.is_configured() and not self._circuit_open(c)
                    ),
                    None,
                )
                logger.warning(
                    "provider_failover",
                    from_provider=p_name,
                    to_provider=next_candidate or "none",
                    reason="unavailable_or_timeout",
                    cooldown=cooldown,
                    error=str(exc),
                )
                continue

            except LLMError as exc:
                # Other non-transient LLM errors (e.g. invalid parameter/prompt rejection)
                self._trip_circuit(provider, cooldown=15.0, reason=f"LLM Error: {exc}")
                last_error = exc
                next_candidate = next(
                    (
                        c.name.lower()
                        for c in candidates[idx + 1 :]
                        if c.is_configured() and not self._circuit_open(c)
                    ),
                    None,
                )
                logger.error(
                    "provider_failover",
                    from_provider=p_name,
                    to_provider=next_candidate or "none",
                    reason="llm_error",
                    error=str(exc),
                )
                continue

        logger.critical("all_providers_exhausted", attempted=attempted_providers, error=str(last_error))
        raise AllProvidersExhaustedError(
            last_error=last_error,
            attempted_providers=attempted_providers,
        )


def create_default_router(settings: Optional[Settings] = None) -> LLMRouter:
    """Factory creating the 4-way failover router initialized from application settings."""
    cfg = settings or get_settings()
    priority_order = cfg.provider_priority_list

    def get_priority(name: str) -> int:
        name_lower = name.lower()
        if name_lower in priority_order:
            return priority_order.index(name_lower)
        return 99

    providers: List[LLMProvider] = [
        OpenAICompatProvider(
            name="ollama",
            base_url=cfg.OLLAMA_BASE_URL,
            api_key=cfg.OLLAMA_API_KEY,
            model=cfg.OLLAMA_MODEL,
            priority=get_priority("ollama"),
            timeout=cfg.LLM_TIMEOUT_SECONDS,
        ),
        OpenAICompatProvider(
            name="groq",
            base_url=cfg.GROQ_BASE_URL,
            api_key=cfg.GROQ_API_KEY,
            model=cfg.GROQ_MODEL,
            priority=get_priority("groq"),
            timeout=cfg.LLM_TIMEOUT_SECONDS,
        ),
        OpenAICompatProvider(
            name="openrouter",
            base_url=cfg.OPENROUTER_BASE_URL,
            api_key=cfg.OPENROUTER_API_KEY,
            model=cfg.OPENROUTER_MODEL,
            priority=get_priority("openrouter"),
            timeout=cfg.LLM_TIMEOUT_SECONDS,
        ),
        OpenAICompatProvider(
            name="gemini",
            base_url=cfg.GEMINI_BASE_URL,
            api_key=cfg.GEMINI_API_KEY,
            model=cfg.GEMINI_MODEL,
            priority=get_priority("gemini"),
            timeout=cfg.LLM_TIMEOUT_SECONDS,
        ),
    ]

    return LLMRouter(
        providers=providers,
        default_rate_limit_cooldown=cfg.CIRCUIT_RATE_LIMIT_COOLDOWN_SECONDS,
        default_unavailable_cooldown=cfg.CIRCUIT_DEFAULT_COOLDOWN_SECONDS,
    )
