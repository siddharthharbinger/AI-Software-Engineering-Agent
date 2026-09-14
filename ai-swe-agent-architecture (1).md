# AI Software Engineering Agent Platform — Architecture & Stack (All-Free Build)

This is a build plan for the merged PR-review + autonomous-bug-fix platform, using only free/open-source components or genuine free tiers. Every choice below is picked so the whole thing can run via `docker compose up` on a single dev machine with no paid subscriptions, while still satisfying the rubric (persistent, inspectable memory; real dual git-hosting; real PM integration; guardrails; multi-tenant B2B UI).

---

## 1. Guiding Architectural Idea

Everything hangs off **one shared understanding layer** and **one shared memory store**. The review pipeline and the bug-fix agent are two different *consumers* of the same indexed codebase and the same memory — they are not two apps that happen to share a database.

```
                        ┌───────────────────────────────┐
                        │        B2B Web UI (React)     │
                        └───────────────┬───────────────┘
                                        │ REST + WebSocket
                        ┌───────────────▼───────────────┐
                        │        API Gateway (FastAPI)   │
                        │  authz / multi-tenant routing  │
                        └───┬───────────────────────┬────┘
             ┌──────────────┘                       └─────────────┐
   ┌─────────▼─────────┐                                ┌─────────▼─────────┐
   │  PR Review Service │                                │ Bug-Fix Agent Svc │
   │ (static + LLM)     │                                │ (LangGraph loop)  │
   └─────────┬──────────┘                                └─────────┬─────────┘
             │                 ┌───────────────────────┐           │
             └────────────────▶│ Codebase Understanding │◀─────────┘
                               │  Engine (shared)        │
                               └───────────┬─────────────┘
                                           │
                               ┌───────────▼─────────────┐
                               │   Persistent Memory      │
                               │   Store (Postgres+pgvec) │
                               └───────────┬─────────────┘
                                           │
        ┌──────────────────────────────────┼──────────────────────────────────┐
        │                                  │                                  │
┌───────▼────────┐                ┌────────▼────────┐              ┌──────────▼─────────┐
│ Git Adapter     │                │ PM Connector     │              │ Guardrail/Policy    │
│ (GitHub + Gitea)│                │ (Jira)           │              │ Engine              │
└─────────────────┘                └──────────────────┘              └─────────────────────┘
```

A background **worker fleet** (Celery/RQ on Redis) does the heavy lifting for both services: cloning, indexing, static analysis, sandboxed agent runs.

---

## 2. Stack — Everything Free

| Layer | Choice | Why free / how |
|---|---|---|
| **Backend language/framework** | Python 3.12 + FastAPI | Free, async, auto-generates OpenAPI spec (a required deliverable), native WebSocket support for streaming agent activity to the UI. Also the natural home for the LLM/agent ecosystem below. |
| **Agent orchestration** | LangGraph (open source) | State-machine/graph runtime for the reproduce→explore→edit→test loop; gives you checkpointing (pause/resume, resumable after crash) and a step-by-step trace for free, which doubles as the "explainable decision trace" bonus. |
| **LLM (reasoning)** | Pluggable `LLMProvider` interface with 3 free backends: **(a)** self-hosted **Ollama** running `qwen2.5-coder:14b`/`32b` or `deepseek-coder-v2` — zero cost, unlimited use, your own hardware; **(b)** **Google Gemini API free tier** (Flash / Flash-Lite models, no credit card) as a hosted fallback; **(c)** **Groq free tier** (Llama 3.3 70B etc., no credit card, very fast) for latency-sensitive steps. | All three are genuinely free today. Gemini's free tier is Flash-family only with modest per-minute/per-day caps, and enabling billing on that Google Cloud project *deletes* the free tier — so keep the demo project billing-free. Groq's free tier is ~30 RPM / ~14,400 RPD per org (no card). Because the interface is pluggable, you can fail over between them or run fully offline via Ollama if you don't want to depend on any external rate limit at all. |
| **Embeddings** | `sentence-transformers` (`all-MiniLM-L6-v2`) or Ollama's `nomic-embed-text` | Fully local, free, no API calls needed for the memory/code-search layer. |
| **Primary datastore** | PostgreSQL 16 + **pgvector** extension | One database for relational data (orgs, PRs, tickets, findings) *and* vector similarity search (code embeddings, memory embeddings) — avoids running a separate vector DB for the MVP. Free, OSS. |
| **Code graph (optional stretch)** | Neo4j **Community Edition** | Free, for call-graph/dependency queries if Postgres recursive CTEs get unwieldy. Not required for MVP — start with adjacency tables in Postgres. |
| **Code parsing** | `tree-sitter` + language grammars | Free, fast, incremental AST parsing across languages — this is what feeds the symbol/dependency index. |
| **Static analysis** | **Semgrep** (OSS rules), **ESLint**, **ruff**, **Bandit** | All free/OSS. Normalize their outputs into one internal `Finding` schema so the LLM reasoning layer and the UI don't care which tool produced a finding. |
| **Queue / background jobs** | Redis + **Celery** (or RQ for something lighter) | Free, OSS. Runs indexing jobs, review jobs, and bug-fix agent runs off the request path. |
| **Sandbox for autonomous fixes** | Docker containers, `--network none`, CPU/memory/pids limits, **gVisor (`runsc`)** runtime | Free, OSS (gVisor is Google's open-source sandboxed container runtime). Gives you real syscall-level isolation without needing a paid microVM service. Enforce a bounded budget: wall-clock timeout, max LLM turns, max file edits. |
| **Frontend** | React + TypeScript + Vite + Tailwind + shadcn/ui | Free. **Monaco Editor** (VS Code's editor, free/OSS) for the PR diff view and the agent's live diff. **TanStack Query** for data fetching, **Recharts** for the trends dashboard. |
| **Realtime streaming** | WebSocket endpoint in FastAPI, or Server-Sent Events | Free, no extra infra — streams the agent's live activity (`reproduce`, `explore`, `edit`, `test`) to the bug-fix task view. |
| **Auth / multi-tenancy** | JWT (via `python-jose` or `authlib`), `org_id` on every row + Postgres **Row-Level Security** | Free. No SSO needed (explicitly out of scope). Simple, auditable tenant isolation. |
| **Secrets (per-org PM/git credentials)** | Encrypted columns (libsodium/`nacl` sealed boxes) in Postgres, or self-hosted **HashiCorp Vault OSS** if you want a dedicated secrets service | Free either way; encrypted columns are enough for the MVP and simpler to run. |
| **Git hosting #1** | **GitHub** — real API + webhooks, GitHub App (free for this use case) | Free tier is sufficient: repos, PRs, checks, webhooks. |
| **Git hosting #2 (self-deployed)** | **Gitea** (not GitLab CE) | Gitea is a single ~100MB binary/container, far lighter than GitLab CE, but still exposes a full REST API + webhooks that are close enough to GitHub's model to sit behind the same adapter interface. Runs great inside `docker compose`. |
| **PM tool** | **Jira Cloud Free plan** (up to 10 users, unlimited projects, REST API v3, webhooks via a Connect/Forge app or polling) | Genuinely free forever at small scale (2GB storage, 100 automation runs/month — irrelevant here since you're using the REST API/webhooks directly, not Jira's internal automation). |
| **PM tool #2 (bonus)** | Linear (generous free developer tier) | Proves the connector abstraction isn't Jira-specific. |
| **Observability** | `structlog` → stdout, **Prometheus** client + **Grafana** dashboards, `/healthz` + `/readyz` endpoints, optional **OpenTelemetry** + **Jaeger** for tracing | All free/OSS. |
| **CI/CD** | GitHub Actions (free minutes tier) | Runs lint, unit tests, integration tests (via `testcontainers`), builds/pushes images. |
| **Testing** | `pytest` + `pytest-asyncio` + `coverage.py` (backend), `Vitest`/`Jest` + React Testing Library (frontend), `testcontainers-python` for real Postgres/Redis in CI | Free/OSS. |
| **Deployment** | `docker-compose.yml` spinning up: `api`, `worker`, `postgres` (pgvector image), `redis`, `gitea`, `frontend`, optional `ollama` | Everything runs locally with one command, satisfying the "deployable via `docker compose up`" deliverable. |

---

## 3. The Persistent Memory Store — the Core of the Grade

This is 20% of the rubric and the thing that separates this from a demo, so give it a real, distinct component rather than folding it into "just the database."

### 3.1 Schema (Postgres + pgvector)

```sql
-- one row per discrete "thing the agent learned"
CREATE TABLE memory_entries (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  repo_id       UUID NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN (
                  'codebase_fact',       -- architecture notes, trouble spots
                  'reviewer_preference', -- dismissed pattern, accepted fix style
                  'task_outcome',        -- what was tried on a bug, did it work
                  'root_cause'           -- a resolved bug's root cause, reusable
                )),
  summary       TEXT NOT NULL,            -- human-readable, shown in the Memory/Insights UI
  detail        JSONB,                    -- structured payload (files touched, diff hash, etc.)
  embedding     VECTOR(384),              -- from the summary, for similarity search
  source_task_id UUID,                    -- which review/bug-fix task produced this
  confidence    REAL DEFAULT 1.0,         -- decays over time / on contradiction
  status        TEXT DEFAULT 'active' CHECK (status IN ('active','superseded','corrected')),
  superseded_by UUID REFERENCES memory_entries(id),
  created_at    TIMESTAMPTZ DEFAULT now(),
  last_used_at  TIMESTAMPTZ
);

-- explicit audit: which memory entries influenced which decision
CREATE TABLE memory_usage_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_entry_id UUID REFERENCES memory_entries(id),
  used_in_task_id UUID NOT NULL,
  used_in_step    TEXT,          -- e.g. "review_finding_generation", "fix_plan"
  effect          TEXT,          -- e.g. "suppressed_finding", "reused_root_cause"
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

`memory_usage_log` is what makes memory *inspectable and explainable* — for any finding or fix, the UI can answer "why did the agent do this?" by joining back through this table, which is exactly the bonus "decision trace" feature and a hard requirement of the main rubric line.

### 3.2 Write path

- After every PR review: write a `reviewer_preference` entry when a human accepts/dismisses/snoozes a finding. A **dismissed pattern** (e.g. "don't flag `console.log` in test files") gets embedded and checked against future findings before they're ever shown — a repeat match auto-suppresses (with a visible "suppressed by past feedback" tag, still overridable).
- After every bug-fix task (success or failure): write a `task_outcome` entry (bug description, files touched, diff, whether tests passed, human approval/rejection) and, on success, a `root_cause` entry summarizing the actual root cause in reusable language.
- Periodically (or after N entries): a **consolidation job** re-embeds and clusters similar `task_outcome`/`codebase_fact` entries, marks stale ones `superseded`, and merges duplicates — this is the "memory decay" bonus and keeps the store from growing forever.

### 3.3 Read path

- Before generating review findings: embed the diff/file context, pull top-k similar `reviewer_preference` and `codebase_fact` entries, and feed them into the LLM prompt *and* use them as a hard filter (if a pattern was explicitly dismissed before, don't resurface it — that's a deterministic check, not just a prompt hint, so it can't be "argued around" by the model).
- Before starting a bug-fix task: embed the bug report, pull top-k similar `root_cause`/`task_outcome` entries. If similarity is high, the agent's plan step starts from "this looks related to a bug fixed on \<date\>, root cause was X, proposed starting point: reuse that fix" instead of a cold re-exploration — this is exactly the cross-task-transfer bonus and the required "must not re-suggest a rejected pattern" behavior.

### 3.4 The before/after test you need to *build* (rubric requires this)

1. Seed two structurally similar bugs (e.g. same off-by-one pattern in two different modules) and two structurally similar review-triggering diffs.
2. Run the agent cold on bug #1 / diff #1, capture time-to-fix and tokens used, let a human review/reject some findings.
3. Run the agent on bug #2 / diff #2 *with memory enabled*, and again with memory *disabled* (a debug flag that skips the read path) as a control.
4. Record the delta (faster convergence, fewer re-flagged dismissed patterns, reused root cause) — this before/after artifact is what a reviewer will actually score you on for the 20% line, so build it as an actual script/notebook, not just an anecdote.

---

## 4. Codebase Understanding Engine (shared by both pipelines)

**Indexing workflow**, triggered on repo connect and on every push (via the git adapter's webhook):

1. Clone/pull the repo (shallow clone + incremental fetch after the first index).
2. Walk files → `tree-sitter` parse per language → extract symbols (functions, classes, imports) and a lightweight call graph (who calls whom, who imports what).
3. Chunk files (by function/class boundaries from the AST, not blind line-splitting) → embed each chunk with the local embedding model → upsert into `code_chunks(embedding VECTOR, file_path, symbol_name, repo_id, commit_sha)`.
4. Store the call/dependency graph as adjacency rows (`edges(from_symbol, to_symbol, edge_type)`) — queryable via recursive CTEs ("what calls this function, transitively, up to depth 3").
5. Emit an `IndexingComplete` event; both the review service and the bug-fix agent query this same table set via a shared `CodebaseSearchClient` — never re-implement search twice.

This is what both "reproduce → explore" (bug-fix) and "understand the diff's blast radius" (review) draw on.

---

## 5. PR Review Pipeline

1. Webhook fires on PR open/update (from either git adapter) → enqueue `ReviewTask`.
2. Worker pulls the diff, resolves it against the indexed codebase (which functions/files does this diff touch, what calls them).
3. Run static analyzers (Semgrep/ESLint/ruff/Bandit as applicable) → normalize to `Finding{file, line, severity, rule_id, message}`.
4. For each finding *and* for diff hunks with no static finding, run an LLM pass with: the diff, the surrounding code context from the understanding engine, and the top-k relevant memory entries (past dismissals, org coding-style notes) → LLM adds explanation, severity calibration, and — where reasonable — a suggested patch.
5. Filter: drop findings that match a previously-dismissed pattern (deterministic check against `reviewer_preference` memory, logged in `memory_usage_log`).
6. Post findings as inline PR comments via the git adapter (GitHub review comments API / Gitea PR comments API) *and* store them for the UI's diff view.
7. Human accepts/dismisses/snoozes in the UI → writes back to `reviewer_preference` memory and (if configured) updates the underlying PR comment/resolution via the adapter.

---

## 6. Bug-Fix Agent Loop (LangGraph state machine, inside the sandbox)

```
 ┌────────────┐   ┌───────────┐   ┌─────────┐   ┌────────┐   ┌────────┐
 │ Intake bug │──▶│ Reproduce │──▶│ Explore │──▶│  Edit  │──▶│  Test  │
 └────────────┘   └─────┬─────┘   └────┬────┘   └───┬────┘   └───┬────┘
                        │ fail          │ done       │            │
                        └───────────────┴────────────┘            │
                    (loop, bounded by budget) ◀────────────────────┘
                                                                    │ pass
                                                            ┌───────▼────────┐
                                                            │ Open real PR    │
                                                            │ + link ticket   │
                                                            └───────┬────────┘
                                                                    │
                                                            ┌───────▼────────┐
                                                            │ Human approval  │
                                                            │ gate (required) │
                                                            └────────────────┘
```

1. **Intake**: bug report arrives manually via UI, or pulled from a Jira ticket (webhook or poll). Memory read: check for similar `root_cause`/`task_outcome` entries; if a strong match exists, seed the plan with "likely same root cause as \<past fix\>, try that patch shape first."
2. **Reproduce**: inside the sandbox, the agent writes/runs a failing test (or executes the reported repro steps) — this step must produce a concrete, verifiable failure before any edit is attempted. No reproduction, no fix attempt — log this explicitly, because "reproduction rigor" is 15% of the grade.
3. **Explore**: uses the shared codebase understanding engine (symbol search, call graph) to locate the relevant code, not a blind grep.
4. **Edit**: proposes a patch; applied inside the sandbox only.
5. **Test**: re-runs the reproduction test + the existing test suite (or an affected subset) inside the sandbox.
6. **Iterate**: loop back to Explore/Edit if tests fail, bounded by (a) max turns, (b) wall-clock timeout, (c) max files touched — enforced by the policy/guardrail layer, not just prompted for.
7. On success: open a real PR via the git adapter with a description containing the bug, root cause, and fix; post a status update + link back to the originating Jira ticket via the PM connector; write `task_outcome` + `root_cause` to memory.
8. **Nothing merges without an explicit human approval action in the UI** — the agent's PR sits open until a human clicks Approve (which can optionally trigger merge via the adapter) or Reject (which is itself a memory write: "this proposed fix style was rejected").

Sandbox specifics: one ephemeral Docker container per task, `gVisor` runtime, `--network none` (except for a controlled package-install phase if needed, then network is cut), CPU/memory/pids-limits, filesystem confined to a scratch clone of the repo, destroyed after the task regardless of outcome.

---

## 7. PM-Tool Integration (Jira)

Bidirectional, via Jira Cloud REST API v3 (free plan is sufficient):

- **Pull**: poll (or webhook, via a lightweight Jira Cloud app/Forge trigger) for issues in a configured JQL filter (e.g. `label = "agent-fixable"`) → creates a `BugFixTask`.
- **Push**: as the LangGraph loop progresses, post comments back onto the ticket (`Reproduced ✅`, `Patch proposed, PR #123 opened`, `Awaiting human approval`) via `POST /rest/api/3/issue/{id}/comment`.
- **Link**: once the PR is opened, attach it to the ticket via Jira's development-panel API (or, simplest, a comment with the PR URL plus a custom field storing the PR link) so the ticket shows the linked PR.
- Same connector *interface* (`PMConnector.pull_tasks()`, `.post_update()`, `.link_pr()`) should be implementable for Linear as the bonus, proving it's a real abstraction and not Jira-specific code with a different name on it.

---

## 8. Git Hosting Adapter Layer

One interface, two implementations:

```python
class GitHostAdapter(Protocol):
    def clone_url(self, repo) -> str: ...
    def register_webhook(self, repo, callback_url) -> None: ...
    def post_pr_review_comment(self, pr, file, line, body) -> None: ...
    def open_pull_request(self, repo, branch, title, body) -> PullRequest: ...
    def get_diff(self, pr) -> Diff: ...
```

- `GitHubAdapter`: GitHub REST/GraphQL API + a GitHub App for webhooks (free).
- `GiteaAdapter`: Gitea's REST API (very close shape to GitHub's — PRs, comments, webhooks), running as its own container in `docker-compose` so "self-deployed git server" is a literal, real deployment, not a mock.
- Both the review pipeline and the bug-fix agent call only the interface — demonstrate this by running the *same* review task and the *same* bug-fix task against a GitHub repo and a Gitea repo in the demo video (this is explicitly 15% of the grade).

---

## 9. Guardrail / Policy Layer

A small, separate service/module that both pipelines must pass through before any state-changing action:

- Review policy: findings above a configured severity can be posted, but a "block merge" style action (e.g. auto-requesting changes) is never allowed to fire outside declared bounds (e.g. only on `main`-targeted PRs, only up to N findings per PR).
- Fix policy: enforces the sandbox budget, refuses to touch files outside an allow-listed set of paths (e.g. never edit CI config or secrets files), and — the hard rule — **the merge action itself is not exposed to the agent's toolset at all**; it only exists as a UI button a human clicks.
- **Actively test this**: write adversarial test cases that try to get the agent to merge without approval, or get the review pipeline to auto-approve/auto-merge, or exceed the sandbox budget — a passing test suite here is what the rubric calls "critical if bypassed," so this needs its own dedicated test file, not an afterthought.

---

## 10. B2B UI (views)

- **PR review/diff view** — Monaco diff editor + inline finding markers, accept/dismiss/snooze actions.
- **Bug-fix task view** — WebSocket-streamed live agent activity (current step: reproduce/explore/edit/test), live diff, "why" panel pulling from `memory_usage_log`, final Approve/Reject gate.
- **Memory/Insights view** — browsable, searchable list of `memory_entries` grouped by kind, with an editor for a human to correct or retire an entry (writes `status = 'corrected'`).
- **Connections settings** — per-org config for the PM tool and both git adapters (OAuth/token entry, webhook status).
- **History/trends dashboard** — Recharts trends: findings over time, false-positive rate, bug-fix success rate, time-to-fix — this is also your raw material for the before/after memory test in §3.4.

---

## 11. Multi-Tenancy

- Every table carries `org_id`; Postgres Row-Level Security policies scope every query automatically so a bug in application code can't leak cross-tenant data.
- Per-org connection config (Jira site, GitHub App installation, Gitea instance URL) stored per `org_id`, encrypted at rest.
- Worker queue tasks carry `org_id` and are safe to run concurrently across orgs (no shared mutable state between sandboxed agent runs).

---

## 12. Suggested `docker-compose.yml` shape

```yaml
services:
  postgres:      # pgvector/pgvector:pg16 image
  redis:
  gitea:         # self-hosted git server, real deployment
  ollama:        # optional local LLM, pull qwen2.5-coder on first boot
  api:           # FastAPI app
  worker:        # Celery worker(s) — indexing, review, bug-fix sandbox launcher
  frontend:      # React app served via nginx or vite preview
```

Everything free, everything reproducible with `docker compose up`.

---

## 13. Build Order (practical milestones)

1. Codebase understanding engine (indexing + search) against a single test repo — no UI yet.
2. Memory store schema + read/write API, with the `memory_usage_log` wired in from day one (retrofitting explainability later is painful).
3. Static analysis + LLM review pipeline against GitHub only.
4. Bug-fix LangGraph loop + sandbox, against GitHub only, with the human-approval gate.
5. Add the Gitea adapter behind the same interface; run both flows against it.
6. Add the Jira connector (pull ticket → task, push status, link PR).
7. Guardrail test suite (adversarial cases) — do this before, not after, the demo recording.
8. UI: diff view → task view → memory view → connections → dashboard.
9. Multi-tenancy pass (RLS policies, per-org config) once single-tenant flows all work.
10. Record the before/after memory demonstration, the demo video, and write ARCHITECTURE.md.
