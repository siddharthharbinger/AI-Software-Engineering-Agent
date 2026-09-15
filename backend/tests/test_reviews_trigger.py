import httpx
import pytest

from app.adapters.factory import reset_git_adapters, set_git_adapter
from app.adapters.protocol import Diff, PullRequest
from app.main import app
from app.services.review_service import ReviewService, set_review_service


class MockGitAdapter:
    def __init__(self, name: str, should_fail_404: bool = False):
        self._name = name
        self._should_fail_404 = should_fail_404

    @property
    def provider_name(self) -> str:
        return self._name

    async def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        if self._should_fail_404:
            req = httpx.Request("GET", f"https://api.github.com/repos/{repo}/pulls/{pr_number}")
            raise httpx.HTTPStatusError(
                "Not Found",
                request=req,
                response=httpx.Response(404, request=req),
            )
        return PullRequest(
            number=pr_number,
            title="Refactor auth module",
            head_branch="refactor/auth",
            base_branch="main",
            head_sha="sha999",
            author="octocat",
            html_url=f"https://github.com/{repo}/pull/{pr_number}",
        )

    async def get_diff(self, repo: str, pr_number: int) -> Diff:
        if self._should_fail_404:
            req = httpx.Request("GET", f"https://api.github.com/repos/{repo}/pulls/{pr_number}")
            raise httpx.HTTPStatusError(
                "Not Found",
                request=req,
                response=httpx.Response(404, request=req),
            )
        return Diff(
            raw_diff="diff --git a/main.py b/main.py\n+print('hello')",
            files_changed=["main.py"],
            additions=1,
            deletions=0,
        )

    async def post_pr_review_comment(self, repo: str, pr_number: int, file: str, line: int, body: str) -> None:
        pass

    async def create_pr_review(self, repo: str, pr_number: int, event: str, body: str, comments=None) -> None:
        pass

    def clone_url(self, repo: str) -> str:
        return f"https://git.example.com/{repo}.git"


@pytest.fixture(autouse=True)
def setup_review_environment():
    reset_git_adapters()
    set_git_adapter("github", MockGitAdapter("github"))
    set_git_adapter("gitea", MockGitAdapter("gitea"))
    set_review_service(ReviewService())
    yield
    reset_git_adapters()


@pytest.mark.asyncio
async def test_manual_trigger_github_success():
    """Verify on-demand trigger with provider 'github' succeeds and enqueues ReviewTask."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/reviews/trigger",
            json={
                "provider": "github",
                "repo": "octocat/Hello-World",
                "pr_number": 42,
                "org_id": "org-1234",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["provider"] == "github"
        assert data["repo"] == "octocat/Hello-World"
        assert data["pr_number"] == 42
        assert data["status"] == "queued"
        assert data["task_id"] is not None

        # Verify task can be retrieved via GET /reviews/{task_id}
        task_id = data["task_id"]
        status_resp = await client.get(f"/api/v1/reviews/{task_id}")
        assert status_resp.status_code == 200
        task_data = status_resp.json()
        assert task_data["task_id"] == task_id
        assert task_data["repo"] == "octocat/Hello-World"
        assert task_data["pr_number"] == 42


@pytest.mark.asyncio
async def test_manual_trigger_gitea_success():
    """Verify on-demand trigger with provider 'gitea' succeeds and enqueues ReviewTask."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/reviews/trigger",
            json={
                "provider": "gitea",
                "repo": "gitea-admin/internal-tool",
                "pr_number": 7,
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["provider"] == "gitea"
        assert data["repo"] == "gitea-admin/internal-tool"
        assert data["pr_number"] == 7
        assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_manual_trigger_validation_errors():
    """Verify 422 errors on invalid request payloads."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid provider
        res1 = await client.post(
            "/api/v1/reviews/trigger",
            json={"provider": "gitlab", "repo": "owner/repo", "pr_number": 1},
        )
        assert res1.status_code == 422

        # Invalid repo format (missing slash)
        res2 = await client.post(
            "/api/v1/reviews/trigger",
            json={"provider": "github", "repo": "repo-with-no-owner", "pr_number": 1},
        )
        assert res2.status_code == 422

        # Negative pr_number
        res3 = await client.post(
            "/api/v1/reviews/trigger",
            json={"provider": "github", "repo": "owner/repo", "pr_number": -5},
        )
        assert res3.status_code == 422


@pytest.mark.asyncio
async def test_manual_trigger_pr_not_found():
    """Verify 404 is returned when the remote git provider reports PR does not exist."""
    set_git_adapter("github", MockGitAdapter("github", should_fail_404=True))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/reviews/trigger",
            json={"provider": "github", "repo": "owner/repo", "pr_number": 99999},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_nonexistent_task_returns_404():
    """Verify GET /api/v1/reviews/{task_id} returns 404 for unknown task ID."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/reviews/non-existent-task-id")
        assert resp.status_code == 404
