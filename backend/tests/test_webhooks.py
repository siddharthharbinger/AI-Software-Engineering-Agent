import json

import httpx
import pytest

from app.adapters.factory import reset_git_adapters, set_git_adapter
from app.adapters.protocol import Diff, PullRequest
from app.core.config import get_settings
from app.core.security import compute_github_signature
from app.main import app
from app.services.review_service import ReviewService, set_review_service


class DummyAdapter:
    def __init__(self, name: str):
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    async def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        return PullRequest(
            number=pr_number,
            title="Fix login vulnerability",
            head_branch="fix/login",
            base_branch="main",
            head_sha="abc1234def",
        )

    async def get_diff(self, repo: str, pr_number: int) -> Diff:
        return Diff(
            raw_diff="diff --git a/auth.py b/auth.py\n+def secure_login(): pass",
            files_changed=["auth.py"],
            additions=1,
            deletions=0,
        )

    async def post_pr_review_comment(self, repo: str, pr_number: int, file: str, line: int, body: str) -> None:
        pass

    async def create_pr_review(self, repo: str, pr_number: int, event: str, body: str, comments=None) -> None:
        pass

    def clone_url(self, repo: str) -> str:
        return f"https://example.com/{repo}.git"


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    """Fixture to ensure consistent test settings and dummy adapters."""
    reset_git_adapters()
    set_git_adapter("github", DummyAdapter("github"))
    set_git_adapter("gitea", DummyAdapter("gitea"))
    set_review_service(ReviewService())
    monkeypatch.setattr(get_settings(), "GITHUB_WEBHOOK_SECRET", "")
    yield
    reset_git_adapters()


@pytest.mark.asyncio
async def test_github_webhook_ping_event(monkeypatch):
    """Verify GitHub ping event returns 200 pong with and without secret configured."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: Unsigned ping when secret is not configured
        resp = await client.post(
            "/api/v1/webhooks/github",
            json={"zen": "Non-blocking is better than blocking."},
            headers={"X-GitHub-Event": "ping"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "Pong" in data["message"]

        # Case 2: Signed ping when secret is configured
        secret = "test-ping-secret"
        monkeypatch.setattr(get_settings(), "GITHUB_WEBHOOK_SECRET", secret)
        payload_bytes = json.dumps({"zen": "Approachable is better than simple."}).encode("utf-8")
        sig = compute_github_signature(payload_bytes, secret)
        signed_resp = await client.post(
            "/api/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": sig,
            },
        )
        assert signed_resp.status_code == 200
        assert signed_resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_github_webhook_signature_verification(monkeypatch):
    """Verify HMAC SHA-256 validation when GITHUB_WEBHOOK_SECRET is set."""
    settings = get_settings()
    test_secret = "super-secret-webhook-key"
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", test_secret)

    payload_dict = {
        "action": "opened",
        "repository": {"full_name": "acme/backend-service"},
        "pull_request": {
            "number": 101,
            "title": "Add auth middleware",
            "head": {"sha": "sha-head-123"},
        },
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: Missing signature header -> 401
        res1 = await client.post(
            "/api/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
            },
        )
        assert res1.status_code == 401

        # Case 2: Tampered/invalid signature -> 401
        res2 = await client.post(
            "/api/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=invalidhash00000000000000000000000000000000000000000000000000000000",
            },
        )
        assert res2.status_code == 401

        # Case 3: Valid HMAC signature -> 202 Accepted
        valid_signature = compute_github_signature(payload_bytes, test_secret)
        res3 = await client.post(
            "/api/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": valid_signature,
            },
        )
        assert res3.status_code == 202
        body3 = res3.json()
        assert body3["status"] == "accepted"
        assert body3["action"] == "opened"
        assert body3["task_id"] is not None


@pytest.mark.asyncio
async def test_github_webhook_pr_actions(monkeypatch):
    """Verify opened, synchronize, and reopened trigger reviews, while closed is ignored."""
    settings = get_settings()
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # synchronize action (new commit pushed)
        resp_sync = await client.post(
            "/api/v1/webhooks/github",
            json={
                "action": "synchronize",
                "repository": {"full_name": "acme/repo"},
                "pull_request": {"number": 12, "head": {"sha": "sha-sync-999"}, "title": "Update PR"},
            },
            headers={"X-GitHub-Event": "pull_request"},
        )
        assert resp_sync.status_code == 202
        assert resp_sync.json()["status"] == "accepted"

        # reopened action
        resp_reopen = await client.post(
            "/api/v1/webhooks/github",
            json={
                "action": "reopened",
                "repository": {"full_name": "acme/repo"},
                "pull_request": {"number": 12, "head": {"sha": "sha-sync-999"}, "title": "Reopen PR"},
            },
            headers={"X-GitHub-Event": "pull_request"},
        )
        assert resp_reopen.status_code == 202
        assert resp_reopen.json()["status"] == "accepted"

        # closed action -> ignored
        resp_closed = await client.post(
            "/api/v1/webhooks/github",
            json={
                "action": "closed",
                "repository": {"full_name": "acme/repo"},
                "pull_request": {"number": 12},
            },
            headers={"X-GitHub-Event": "pull_request"},
        )
        assert resp_closed.status_code == 200
        assert resp_closed.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_gitea_webhook_pr_ingestion():
    """Verify Gitea pull request events enqueue a review task."""
    payload = {
        "action": "opened",
        "repository": {"full_name": "dev/platform-service"},
        "pull_request": {
            "number": 55,
            "title": "Fix SQL injection",
            "head": {"sha": "sha-gitea-111"},
        },
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/gitea",
            json=payload,
            headers={"X-Gitea-Event": "pull_request"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["action"] == "opened"
        assert data["task_id"] is not None
        assert "Gitea PR #55" in data["message"]


@pytest.mark.asyncio
async def test_gitea_webhook_ignored_action():
    """Verify non-review actions on Gitea PRs are ignored gracefully."""
    payload = {
        "action": "labeled",
        "repository": {"full_name": "dev/platform-service"},
        "pull_request": {"number": 55},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/gitea",
            json=payload,
            headers={"X-Gitea-Event": "pull_request"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
