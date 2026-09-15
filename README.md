# AI-Software-Engineering-Agent — Autonomous PR Review & Bug-Fixing Platform

An autonomous AI software engineering platform providing automated PR reviews, git webhook ingestion (GitHub & Gitea), and resilient multi-provider LLM execution.

## LLM Architecture & Resilience

The platform features a provider-agnostic **4-way failover router** that dynamically routes completions across 4 providers:

1. **Groq** (`llama-3.3-70b-versatile`): Ultra-low latency primary provider.
2. **OpenRouter** (`openrouter/free`): Broad model router fallback.
3. **Google Gemini** (`gemini-3.8-flash`): State-of-the-art Flash agentic coding model.
4. **Mistral AI** (`codestral-latest`): Dedicated high-capability coding model served as 4th-tier failover.

### Circuit Breaker & Proactive RPM Throttling
- **Per-Provider Circuit Breakers**: Automatically open on HTTP 429 (`Retry-After`), HTTP 5xx server errors, and network timeouts.
- **Proactive RPM Throttling**: Providers with strict free-tier rate limits (such as Mistral's `max_rpm=2`) use a sliding-window tracker. The router proactively skips the provider before exceeding its quota, preventing unnecessary HTTP 429 failures.
- **Structured Telemetry**: Full `structlog` event logging tracks failover reasons, latencies, and circuit transitions.