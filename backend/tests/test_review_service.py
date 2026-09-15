import asyncio

import pytest

from app.adapters.factory import reset_git_adapters, set_git_adapter
from app.adapters.protocol import Diff, PullRequest
from app.services.review_service import ReviewService, ReviewTask


class MockAdapter:
    def __init__(self, name: str):
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    async def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        return PullRequest(
            number=pr_number,
            title="Update dependencies",
            head_branch="feature/deps",
            base_branch="main",
            head_sha="head123456",
            author="dependabot",
            html_url=f"https://github.com/{repo}/pull/{pr_number}",
        )

    async def get_diff(self, repo: str, pr_number: int) -> Diff:
        return Diff(
            raw_diff="diff --git a/requirements.txt b/requirements.txt\n-old==1.0\n+new==2.0",
            files_changed=["requirements.txt"],
            additions=1,
            deletions=1,
        )

    async def post_pr_review_comment(self, repo: str, pr_number: int, file: str, line: int, body: str) -> None:
        pass

    async def create_pr_review(self, repo: str, pr_number: int, event: str, body: str, comments=None) -> None:
        pass

    def clone_url(self, repo: str) -> str:
        return f"https://github.com/{repo}.git"


@pytest.fixture(autouse=True)
def setup_service_test():
    reset_git_adapters()
    set_git_adapter("github", MockAdapter("github"))
    set_git_adapter("gitea", MockAdapter("gitea"))
    yield
    reset_git_adapters()


@pytest.mark.asyncio
async def test_review_service_contract():
    """Verify ReviewService.queue_pr_review dispatches identical ReviewTask structure."""
    service = ReviewService()

    task = await service.queue_pr_review(
        provider="github",
        repo_full_name="org/repo-app",
        pr_number=10,
        org_id="org-uuid-001",
    )

    assert isinstance(task, ReviewTask)
    assert task.provider == "github"
    assert task.repo == "org/repo-app"
    assert task.pr_number == 10
    assert task.org_id == "org-uuid-001"
    assert task.head_sha == "head123456"
    assert task.title == "Update dependencies"
    assert task.diff is not None
    assert "requirements.txt" in task.diff.files_changed

    # Allow the background pipeline task to run
    await asyncio.sleep(0.05)

    stored_task = service.get_task(task.task_id)
    assert stored_task is not None
    assert stored_task.status == "completed"
    assert len(stored_task.findings) > 0


@pytest.mark.asyncio
async def test_review_service_with_preprovided_diff():
    """Verify webhook intake with pre-provided diff or metadata skips redundant remote fetch."""
    service = ReviewService()

    custom_diff = "diff --git a/src/index.ts b/src/index.ts\n+console.log('test')"
    task = await service.queue_pr_review(
        provider="gitea",
        repo_full_name="org/gitea-repo",
        pr_number=5,
        head_sha="custom-sha",
        title="Custom PR Title",
        diff_content=custom_diff,
    )

    assert task.diff is not None
    assert task.diff.raw_diff == custom_diff
    assert task.head_sha == "custom-sha"
    assert task.title == "Custom PR Title"


@pytest.mark.asyncio
async def test_review_service_validation_failures():
    """Verify invalid inputs raise ValueError."""
    service = ReviewService()

    with pytest.raises(ValueError, match="Unsupported provider"):
        await service.queue_pr_review("bitbucket", "owner/repo", 1)

    with pytest.raises(ValueError, match="Invalid repository format"):
        await service.queue_pr_review("github", "invalid-repo-format", 1)

    with pytest.raises(ValueError, match="Invalid PR number"):
        await service.queue_pr_review("github", "owner/repo", 0)
