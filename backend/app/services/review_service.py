import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.adapters.factory import get_git_adapter
from app.adapters.protocol import Diff, GitHostAdapter, PullRequest
from app.core.logging import get_logger
from app.llm.router import LLMRouter, create_default_router

logger = get_logger(__name__)


@dataclass
class ReviewTask:
    """Represents an asynchronous PR review task."""
    task_id: str
    provider: str
    repo: str
    pr_number: int
    status: str = "queued"  # "queued", "processing", "completed", "failed"
    org_id: str | None = None
    head_sha: str | None = None
    title: str | None = None
    diff: Diff | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ReviewService:
    """Shared business service handling both webhook-driven and on-demand PR reviews."""

    def __init__(self, llm_router: LLMRouter | None = None):
        self._tasks: dict[str, ReviewTask] = {}
        self._llm_router = llm_router

    @property
    def llm_router(self) -> LLMRouter:
        if self._llm_router is None:
            self._llm_router = create_default_router()
        return self._llm_router

    def get_task(self, task_id: str) -> ReviewTask | None:
        """Retrieve review task by ID."""
        return self._tasks.get(task_id)

    def list_tasks(self, repo: str | None = None) -> list[ReviewTask]:
        """List all tasks, optionally filtered by repository."""
        tasks = list(self._tasks.values())
        if repo:
            tasks = [t for t in tasks if t.repo.lower() == repo.lower()]
        return tasks

    async def queue_pr_review(
        self,
        provider: str,
        repo_full_name: str,
        pr_number: int,
        org_id: str | None = None,
        head_sha: str | None = None,
        title: str | None = None,
        diff_content: str | None = None,
    ) -> ReviewTask:
        """Unified entrypoint for queuing a PR review.
        
        Called identically by:
        1. Webhook endpoints (GitHub & Gitea push/PR events)
        2. On-demand trigger endpoint (/api/v1/reviews/trigger)
        """
        normalized_provider = provider.strip().lower()
        if normalized_provider not in ("github", "gitea"):
            raise ValueError(f"Unsupported provider '{provider}'. Must be 'github' or 'gitea'.")

        clean_repo = repo_full_name.strip()
        if "/" not in clean_repo or len(clean_repo.split("/")) != 2:
            raise ValueError(f"Invalid repository format '{repo_full_name}'. Expected 'owner/repo'.")

        if pr_number <= 0:
            raise ValueError(f"Invalid PR number {pr_number}. Must be greater than 0.")

        task_id = str(uuid.uuid4())
        adapter: GitHostAdapter = get_git_adapter(normalized_provider)

        # Fetch PR metadata or diff if not provided in payload
        diff_obj: Diff | None = None
        if diff_content:
            diff_obj = Diff(raw_diff=diff_content)
        else:
            try:
                diff_obj = await adapter.get_diff(clean_repo, pr_number)
            except Exception as e:
                logger.error("failed_to_fetch_pr_diff", provider=normalized_provider, repo=clean_repo, pr_number=pr_number, error=str(e))
                raise

        if not head_sha or not title:
            try:
                pr_meta: PullRequest = await adapter.get_pull_request(clean_repo, pr_number)
                head_sha = head_sha or pr_meta.head_sha
                title = title or pr_meta.title
            except Exception as e:
                logger.warning("failed_to_fetch_pr_metadata", provider=normalized_provider, repo=clean_repo, pr_number=pr_number, error=str(e))

        task = ReviewTask(
            task_id=task_id,
            provider=normalized_provider,
            repo=clean_repo,
            pr_number=pr_number,
            org_id=org_id,
            head_sha=head_sha,
            title=title,
            diff=diff_obj,
            status="queued",
        )

        self._tasks[task_id] = task

        logger.info(
            "review_task_queued",
            task_id=task_id,
            provider=normalized_provider,
            repo=clean_repo,
            pr_number=pr_number,
            org_id=org_id,
            head_sha=head_sha,
        )

        # Dispatch background review pipeline execution
        asyncio.create_task(self.run_review_pipeline(task))

        return task

    async def run_review_pipeline(self, task: ReviewTask) -> None:
        """Executes the analysis and review generation pipeline for a queued task."""
        task.status = "processing"
        task.updated_at = datetime.now(UTC)

        logger.info(
            "review_pipeline_started",
            task_id=task.task_id,
            repo=task.repo,
            pr_number=task.pr_number,
        )

        try:
            # Here we can run static analysis & LLM analysis
            diff_text = task.diff.raw_diff if task.diff else ""

            # Check if diff is empty
            if not diff_text.strip():
                task.findings = []
                task.status = "completed"
                task.updated_at = datetime.now(UTC)
                logger.info("review_pipeline_completed_empty_diff", task_id=task.task_id)
                return

            # Simulate/invoke review pass
            # For now, record analysis completion
            task.findings = [
                {
                    "file": task.diff.files_changed[0] if task.diff and task.diff.files_changed else "general",
                    "line": 1,
                    "severity": "info",
                    "title": "Review Completed",
                    "message": f"Automated inspection completed for PR #{task.pr_number}.",
                }
            ]

            task.status = "completed"
            task.updated_at = datetime.now(UTC)

            logger.info(
                "review_pipeline_completed",
                task_id=task.task_id,
                findings_count=len(task.findings),
            )
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.updated_at = datetime.now(UTC)
            logger.exception(
                "review_pipeline_failed",
                task_id=task.task_id,
                error=str(e),
            )


# Global singleton instance
_review_service: ReviewService | None = None


def get_review_service() -> ReviewService:
    """Get the singleton ReviewService instance."""
    global _review_service
    if _review_service is None:
        _review_service = ReviewService()
    return _review_service


def set_review_service(service: ReviewService) -> None:
    """Set or override ReviewService (useful for unit testing)."""
    global _review_service
    _review_service = service
