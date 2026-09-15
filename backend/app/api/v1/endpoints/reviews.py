
import httpx
from fastapi import APIRouter, HTTPException, Query, status

from app.core.logging import get_logger
from app.schemas.reviews import (
    ReviewFindingItem,
    ReviewTaskStatus,
    ReviewTriggerRequest,
    ReviewTriggerResponse,
)
from app.services.review_service import ReviewTask, get_review_service

logger = get_logger(__name__)

router = APIRouter()


@router.post(
    "/trigger",
    response_model=ReviewTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="On-Demand PR Review Trigger",
    description="Manually triggers a PR review by directly fetching the diff and queuing a ReviewTask via the shared review service.",
)
async def trigger_pr_review(payload: ReviewTriggerRequest) -> ReviewTriggerResponse:
    review_service = get_review_service()

    try:
        task = await review_service.queue_pr_review(
            provider=payload.provider,
            repo_full_name=payload.repo,
            pr_number=payload.pr_number,
            org_id=payload.org_id,
        )
    except ValueError as e:
        logger.warning("invalid_review_trigger_request", error=str(e), repo=payload.repo)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except httpx.HTTPStatusError as e:
        logger.error(
            "upstream_git_provider_error",
            status_code=e.response.status_code,
            repo=payload.repo,
            pr_number=payload.pr_number,
        )
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PR #{payload.pr_number} in repository '{payload.repo}' was not found on {payload.provider}",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to communicate with {payload.provider}: HTTP {e.response.status_code}",
        )
    except Exception as e:
        logger.exception("review_trigger_unexpected_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue review task: {e}",
        ) from e

    return ReviewTriggerResponse(
        task_id=task.task_id,
        status=task.status,
        provider=task.provider,
        repo=task.repo,
        pr_number=task.pr_number,
        message=f"Review queued successfully for {payload.provider} PR #{payload.pr_number}",
        created_at=task.created_at,
    )


@router.get(
    "/{task_id}",
    response_model=ReviewTaskStatus,
    summary="Get Review Task Status",
    description="Retrieves the current execution status, findings, or error of a queued review task.",
)
async def get_review_task_status(task_id: str) -> ReviewTaskStatus:
    review_service = get_review_service()
    task = review_service.get_task(task_id)

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review task '{task_id}' not found",
        )

    return _serialize_task(task)


@router.get(
    "",
    response_model=list[ReviewTaskStatus],
    summary="List Review Tasks",
    description="Lists recent review tasks, optionally filtered by repository.",
)
async def list_review_tasks(
    repo: str | None = Query(None, description="Filter by repository name (owner/repo)"),
) -> list[ReviewTaskStatus]:
    review_service = get_review_service()
    tasks = review_service.list_tasks(repo=repo)
    return [_serialize_task(t) for t in tasks]


def _serialize_task(task: ReviewTask) -> ReviewTaskStatus:
    findings = [
        ReviewFindingItem(
            file=f.get("file", "unknown"),
            line=f.get("line", 1),
            rule_id=f.get("rule_id"),
            severity=f.get("severity", "info"),
            title=f.get("title", ""),
            message=f.get("message", ""),
            suggestion=f.get("suggestion"),
        )
        for f in task.findings
    ]
    return ReviewTaskStatus(
        task_id=task.task_id,
        status=task.status,
        provider=task.provider,
        repo=task.repo,
        pr_number=task.pr_number,
        org_id=task.org_id,
        head_sha=task.head_sha,
        title=task.title,
        findings=findings,
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
