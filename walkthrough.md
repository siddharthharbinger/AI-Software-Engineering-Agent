# Phase 1 Walkthrough — Project Skeleton & 4-Way Failover LLM Router

## What Was Completed in Phase 1

### 1. Minimal Docker Compose Skeleton
Created [docker-compose.yml](file:///c:/Users/Siddharth.Savant/Documents/AI%20Software%20Engineering%20Agent/docker-compose.yml) defining all 7 platform services:
- **`postgres`**: `pgvector/pgvector:pg16` with healthcheck
- **`redis`**: `redis:7-alpine` with healthcheck
- **`gitea`**: `gitea/gitea:1.22-rootless` (self-hosted git server) with healthcheck
- **`ollama`**: `ollama/ollama:latest` for local inference
- **`api`**: FastAPI application backend (`./backend`)
- **`worker`**: Background processing worker (`./backend`)
- **`frontend`**: React/Vite dashboard (`./frontend`)
- Named persistent volumes for Postgres, Redis, Gitea, and Ollama.

### 2. 12-Factor Configuration & Logging
- **[.env.example](file:///c:/Users/Siddharth.Savant/Documents/AI%20Software%20Engineering%20Agent/.env.example)**: Full environment template with LLM keys, base URLs, circuit breaker timeouts, database URLs, git/jira credentials, and sandbox settings.
- **[config.py](file:///c:/Users/Siddharth.Savant/Documents/AI%20Software%20Engineering%20Agent/backend/app/core/config.py)**: Pydantic Settings v2 configuration with `LLM_PROVIDER_PRIORITY` parsing.
- **[logging.py](file:///c:/Users/Siddharth.Savant/Documents/AI%20Software%20Engineering%20Agent/backend/app/core/logging.py)**: `structlog` setup outputting structured logs directly to `stdout`.

### 3. Unified 4-Way Failover LLM Provider Layer
- **[protocol.py](file:///c:/Users/Siddharth.Savant/Documents/AI%20Software%20Engineering%20Agent/backend/app/llm/protocol.py)**: Protocol definition, `ChatMessage`, `LLMResponse`, `LLMUsage`, and exception hierarchy (`RateLimitError`, `ProviderUnavailableError`, `ProviderTimeoutError`, `AllProvidersExhaustedError`).
- **[openai_compat.py](file:///c:/Users/Siddharth.Savant/Documents/AI%20Software%20Engineering%20Agent/backend/app/llm/openai_compat.py)**: Single `OpenAICompatProvider` client handling Ollama, Groq, OpenRouter, and Gemini with HTTP 429 `Retry-After` extraction, 5xx server error mapping, timeout handling, and response metadata.
- **[router.py](file:///c:/Users/Siddharth.Savant/Documents/AI%20Software%20Engineering%20Agent/backend/app/llm/router.py)**: Provider-agnostic router featuring:
  - Dynamic priority ordering (`LLM_PROVIDER_PRIORITY`).
  - Per-provider circuit breaker cooldown tracking.
  - Automatic fallthrough upon rate limits or errors.
  - Skipping unconfigured or cooling providers.
  - Structured failover event logs (`provider_failover`, `circuit_breaker_tripped`).
  - Serving provider telemetry and usage capture.

### 4. WSL2 & gVisor Environment Assessment
We investigated the local Windows / WSL2 environment:
- WSL2 distribution present: `docker-desktop` (Version 2).
- gVisor's `runsc` OCI runtime is **not** pre-installed in standard Docker Desktop WSL2 daemon.
- **Resolution & Trade-off for Phase 5**:
  - The sandbox runner will support `runsc` if detected in Docker runtimes.
  - If `runsc` is not present, it will automatically fall back to hardened standard Docker isolation: `--network none`, `--cap-drop=ALL`, `--read-only`, `tmpfs` mounts, CPU limits, memory limits, and PID limits.
  - This trade-off is documented in configuration and will be detailed in `ARCHITECTURE.md`.

---

## Verification & Test Results

We ran the comprehensive unit test suite in [backend/tests/test_llm_router.py](file:///c:/Users/Siddharth.Savant/Documents/AI%20Software%20Engineering%20Agent/backend/tests/test_llm_router.py):

```powershell
python -m pytest backend/tests/test_llm_router.py -v
```

### Test Output:
```
============================= test session starts =============================
platform win32 -- Python 3.11.1, pytest-9.1.1, pluggy-1.6.0
collecting ... collected 12 items

backend\tests\test_llm_router.py::test_primary_provider_serves_request PASSED           [  8%]
backend\tests\test_llm_router.py::test_429_rate_limit_failover PASSED                   [ 16%]
backend\tests\test_llm_router.py::test_circuit_breaker_skips_cooling_provider_on_subsequent_call PASSED [ 25%]
backend\tests\test_llm_router.py::test_circuit_breaker_recovers_after_cooldown PASSED   [ 33%]
backend\tests\test_llm_router.py::test_5xx_unavailable_and_timeout_failover PASSED     [ 41%]
backend\tests\test_llm_router.py::test_all_providers_exhausted_raises_error PASSED      [ 50%]
backend\tests\test_llm_router.py::test_unconfigured_providers_are_skipped PASSED       [ 58%]
backend\tests\test_llm_router.py::test_dynamic_priority_reordering PASSED              [ 66%]
backend\tests\test_llm_router.py::test_openai_compat_provider_success PASSED            [ 75%]
backend\tests\test_llm_router.py::test_openai_compat_provider_429_with_retry_after PASSED [ 83%]
backend\tests\test_llm_router.py::test_create_default_router_from_settings PASSED       [ 91%]
backend\tests\test_llm_router.py::test_status_telemetry_and_failover_accounting PASSED [100%]

============================= 12 passed in 0.29s ==============================
```

### Git Repository Status
Initialized git repository and created commit:
`[master (root-commit) e8c179c] feat(phase-1): setup project skeleton, docker-compose, and 4-way failover LLM router`
