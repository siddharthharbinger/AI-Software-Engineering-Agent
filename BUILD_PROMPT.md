# Build Prompt — AI Software Engineering Agent Platform

> Paste this entire document into your AI IDE (Claude Code, Cursor, etc.) as the opening brief. Work through it phase by phase — do not try to generate the whole system in one shot. After each phase, stop, show me the diff, and wait for confirmation before continuing.

## 1. Mission

Build an integrated AI software-engineering agent platform with two pipelines sharing one codebase-understanding engine and one persistent memory store:

1. **Automated PR review** — static analysis + LLM reasoning, inline findings, accept/dismiss/snooze, and a dismissed pattern must measurably stop recurring.
2. **Autonomous bug-fixing** — given a bug report or a linked ticket, reproduce → explore → edit → test → open a real PR, inside an isolated sandbox, bounded budget, never merging without explicit human approval.

Both pipelines read from and write to the same memory store, so the system provably gets better at a later, related task because of something it learned earlier — this is graded on a before/after test, not a claim, so a script that demonstrates it is part of the deliverable.

The platform must work end-to-end against **both GitHub and a self-hosted Gitea instance** through one adapter interface, and bidirectionally against **Jira** (pull ticket → post status → link PR). No autonomous action ever merges or force-applies without a human clicking approve in the UI.

## 2. Scope for this build (deadline-driven — do not silently expand beyond this)

**In scope, build for real:**
- Shared codebase understanding engine (tree-sitter parsing + embeddings + call graph)
- Persistent memory store with an inspectable usage log
- PR review pipeline against GitHub
- Bug-fix agent loop + sandbox + guardrails against GitHub
- Gitea adapter proving the same two flows work against it
- Jira integration via polling (not a registered webhook app)
- A real B2B UI: PR review view, bug-fix task view (live-streamed), memory/insights view, connections settings, trends dashboard
- Multi-tenant data model (org_id + row-level security) even though we're the only org during dev
- The before/after memory demonstration script
- Adversarial guardrail tests

**Explicitly deferred — stub or skip, note in ARCHITECTURE.md as a known limitation:**
- A second PM tool (Linear) integration
- Neo4j / dedicated graph database (use Postgres recursive CTEs)
- Memory consolidation/decay job (note the design, implement only if time remains)
- Kubernetes manifests (design for it, don't build it — see §7)

## 3. Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI (async), Pydantic v2 |
| Agent orchestration | LangGraph |
| Embeddings | `sentence-transformers` (`all-MiniLM-L6-v2`), local, no API |
| Code parsing | `tree-sitter` + language grammars |
| Static analysis | Semgrep, ESLint, ruff, Bandit — normalized to one `Finding` schema |
| Database | PostgreSQL 16 + `pgvector` |
| Queue | Redis + Celery |
| Sandbox | Docker, `gVisor` (`runsc`) runtime, `--network none`, cpu/mem/pids limits |
| Frontend | React + TypeScript + Vite + Tailwind + shadcn/ui, Monaco Editor, TanStack Query, Recharts |
| Realtime | WebSocket (FastAPI native) |
| Auth | JWT, `org_id` row-level security, no SSO |
| Git hosting | GitHub (API + webhooks/PAT), Gitea (self-hosted container) |
| PM tool | Jira Cloud Free (REST API v3, polling) |
| Observability | `structlog`, Prometheus client + Grafana, `/healthz` + `/readyz` |
| CI | GitHub Actions |
| Tests | `pytest` + `pytest-asyncio` + `testcontainers-python`; `Vitest` + React Testing Library |
| Deploy | Single `docker-compose.yml`: `postgres`, `redis`, `gitea`, `ollama`, `api`, `worker`, `frontend` |

## 4. LLM Provider Layer — 4-way failover (build this first, everything else depends on it)

I will not rely on a single API key. Build a **provider-agnostic router** with automatic failover across four independent providers, so if one is rate-limited or down, the next one picks up the request transparently.

### 4.1 The key architectural shortcut

Ollama (in OpenAI-compat mode), Groq, OpenRouter, and Gemini **all expose an OpenAI-compatible `/chat/completions` endpoint** — so implement **one** HTTP client class and configure four instances of it with different `base_url` / `api_key` / `model`, instead of writing four bespoke SDK integrations:

| Provider | Base URL | Notes |
|---|---|---|
| Ollama (local) | `http://ollama:11434/v1` | No API key needed; unlimited, runs on our own container; pull `qwen2.5-coder:14b` (or a smaller tag if hardware is constrained) on first boot |
| Groq | `https://api.groq.com/openai/v1` | Free tier, no card, fast; use a current Llama/Mixtral model — check `console.groq.com` for the active free model list before hardcoding a name |
| OpenRouter | `https://openrouter.ai/api/v1` | Free tier, no card; use `openrouter/free` (auto-router across ~25 free models) or a specific `:free`-suffixed model id; rate limit is 20 req/min, 50 req/day (rising to 1000/day after a one-time $10 top-up, optional) |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | Free tier, no card *as long as billing is never enabled on the project* — enabling billing removes the free tier entirely; use a current Flash-family model name (check `ai.google.dev` — model names iterate, e.g. a `-flash-latest` alias if available) |

### 4.2 Interface

```python
class LLMProvider(Protocol):
    name: str
    priority: int  # lower = tried first
    async def complete(self, messages: list[dict], **kwargs) -> LLMResponse: ...
    def is_configured(self) -> bool: ...  # env vars present?

class OpenAICompatProvider:
    """One implementation, reused for Ollama / Groq / OpenRouter / Gemini
    by passing different base_url, api_key, model, and priority."""
    def __init__(self, name, base_url, api_key, model, priority): ...
    async def complete(self, messages, **kwargs) -> LLMResponse: ...
```

### 4.3 Router + failover behavior

```python
class LLMRouter:
    def __init__(self, providers: list[LLMProvider]):
        self.providers = sorted(providers, key=lambda p: p.priority)

    async def complete(self, messages, **kwargs) -> LLMResponse:
        last_error = None
        for provider in self.providers:
            if not provider.is_configured() or self._circuit_open(provider):
                continue
            try:
                return await provider.complete(messages, **kwargs)
            except RateLimitError as e:
                self._trip_circuit(provider, cooldown=e.retry_after or 60)
                last_error = e
                continue
            except (ProviderUnavailableError, TimeoutError) as e:
                self._trip_circuit(provider, cooldown=30)
                last_error = e
                continue
        raise AllProvidersExhaustedError(last_error)
```

- **Circuit breaker per provider**: on a 429 or 5xx, mark that provider "cooling down" (respect a `Retry-After` header if present, else a fixed backoff) and skip it on subsequent calls until the cooldown elapses — don't hammer a provider that just rejected you.
- **Priority order is configurable, not hardcoded** — read from an env var so it can be re-ordered without a redeploy: `LLM_PROVIDER_PRIORITY=ollama,groq,openrouter,gemini`.
- **Log every failover** (`structlog`, structured event: `provider_failover`, `from`, `to`, `reason`) — this is directly demoable as "handles a failure/edge case gracefully," which is an explicit judged requirement.
- **Every request is logged with which provider actually served it** (store on the task/finding record) — useful both for debugging and for the "why did it do this" inspectability story.
- Config entirely via environment variables (`.env` / `.env.example`), never hardcoded keys — same 12-factor principle as the rest of the system.

### 4.4 Testing this layer

Write a test that force-fails the first N providers (mock 429s) and asserts the router falls through to the next one, and a test that asserts a fully-exhausted router raises a clear, catchable error rather than hanging — this is exactly the kind of "behavior under partial failure" the production-readiness criterion is checking for.

## 5. Persistent Memory Store — schema

```sql
CREATE TABLE memory_entries (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  repo_id        UUID NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN
                   ('codebase_fact','reviewer_preference','task_outcome','root_cause')),
  summary        TEXT NOT NULL,
  detail         JSONB,
  embedding      VECTOR(384),
  source_task_id UUID,
  confidence     REAL DEFAULT 1.0,
  status         TEXT DEFAULT 'active' CHECK (status IN ('active','superseded','corrected')),
  superseded_by  UUID REFERENCES memory_entries(id),
  created_at     TIMESTAMPTZ DEFAULT now(),
  last_used_at   TIMESTAMPTZ
);

CREATE TABLE memory_usage_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_entry_id UUID REFERENCES memory_entries(id),
  used_in_task_id UUID NOT NULL,
  used_in_step    TEXT,
  effect          TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

Every finding-suppression and every "reused root cause" decision must write a row here — build this at the same time as the memory tables, not after.

## 6. Services to build, in this order

1. **Codebase understanding engine**: clone/pull → tree-sitter parse → symbol + call-graph extraction → chunk-and-embed → `code_chunks` table. Expose one `CodebaseSearchClient` that both later pipelines call — never duplicate this logic.
2. **Memory store API**: CRUD + similarity search over `memory_entries`, plus the write path described in the earlier design (dismissed-pattern suppression is a deterministic filter, not just a prompt hint).
3. **PR review pipeline**: webhook/poll → static analyzers → LLM pass (via the router) with memory-filtered context → post findings via the git adapter → accept/dismiss/snooze writes back to memory.
4. **Bug-fix agent (LangGraph)**: intake → reproduce → explore → edit → test → iterate (bounded) → open PR → post Jira status → await human approval. Memory read at intake (check for a similar past root cause) and memory write on completion.
5. **Sandbox**: one ephemeral Docker container per task, `gVisor` runtime, `--network none`, hard limits on cpu/memory/pids/wall-clock/turns/files-touched, destroyed after every run regardless of outcome.
6. **Git adapter layer**: one `GitHostAdapter` interface, `GitHubAdapter` + `GiteaAdapter` implementations, same flows demoed against both.
7. **Jira connector**: `PMConnector` interface (`pull_tasks`, `post_update`, `link_pr`), Jira implementation via polling REST API v3.
8. **Guardrail/policy layer**: enforced sandbox budgets, file allow-list, and — critically — the merge action is never in the agent's own toolset, only a UI button a human clicks. Write adversarial tests trying to bypass this.
9. **Frontend**: PR review/diff view, bug-fix task view (WebSocket-streamed), memory/insights view (with the "why" trace via `memory_usage_log`), connections settings, trends dashboard.
10. **Before/after memory test script**: seed two similar bugs/diffs, run cold vs. with-memory, record the delta as a runnable artifact (not just a written claim).

## 7. Deployment & future-proofing

The whole system must come up with a single command for local/demo use, **and** be structured so a company could later lift it into a real cloud deployment without a redesign:

- `docker compose up` brings up every service (`postgres`, `redis`, `gitea`, `ollama`, `api`, `worker`, `frontend`) from a clean checkout — this is a required, judged deliverable, not optional polish.
- **12-factor config**: every secret and connection string comes from environment variables (`.env.example` checked in with placeholder values, real `.env` gitignored). No credentials hardcoded anywhere, including the four LLM provider keys.
- **Stateless services**: `api` and `worker` hold no local state — all state lives in Postgres/Redis — so either can be horizontally scaled or lifted into Kubernetes `Deployments` later with zero code changes, only manifest changes.
- **Stateful services isolated**: `postgres` and `redis` are the only things that need persistent volumes / `StatefulSets` in a future cloud deploy — call this out explicitly in ARCHITECTURE.md so it's obvious what would need a `PersistentVolumeClaim` later.
- **Health checks**: `/healthz` (liveness) and `/readyz` (readiness, checks DB/Redis/LLM-router connectivity) on the API — these map directly to Kubernetes liveness/readiness probes later, so implement them for real now even though only Docker Compose uses them today.
- **Structured logging** to stdout (never to a local file) — this is what makes the service log-aggregator-friendly in any future deployment (ELK, Loki, CloudWatch, whatever the company already runs), without changing the app.
- Do **not** build actual Kubernetes manifests or a Helm chart for this deadline — just make sure nothing in the design (no local file state, no hardcoded hostnames, no in-memory session affinity) would block writing them later. Note this trade-off explicitly in ARCHITECTURE.md.

## 8. Definition of done for this build

- [ ] `docker compose up` from a clean clone brings up all services and the UI loads
- [ ] A PR opened against the GitHub test repo gets reviewed end-to-end with inline findings
- [ ] The same review flow works against a Gitea test repo
- [ ] A seeded bug is reproduced, fixed, and a real PR opened, sitting unmerged until manual approval
- [ ] A Jira ticket polled in triggers a bug-fix task, and status/PR-link updates appear back on the ticket
- [ ] A dismissed review-finding pattern is measurably suppressed on the next similar PR
- [ ] The before/after memory script produces a concrete, saved delta (not just console output — write it to a file)
- [ ] Killing/removing one LLM provider's API key still results in successful completions (proves the failover)
- [ ] The adversarial guardrail tests exist and fail the merge-bypass attempt as expected
- [ ] README, API reference (OpenAPI), ARCHITECTURE.md, and demo video are all present

---

Work phase by phase from §6. Confirm each phase's tests pass before starting the next. Ask me before making any scope decision not covered above.
