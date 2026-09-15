import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import verify_github_signature
from app.schemas.webhooks import WebhookResponse
from app.services.review_service import get_review_service

logger = get_logger(__name__)

router = APIRouter()


@router.post(
    "/github",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
    summary="GitHub Webhook Ingestion Endpoint",
    description="Receives GitHub webhook deliveries, validates HMAC SHA-256 signature, and enqueues PR reviews.",
)
async def github_webhook(
    request: Request,
    response: Response,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
) -> WebhookResponse:
    settings = get_settings()
    body_bytes = await request.body()

    # 1. Verify HMAC SHA-256 Signature
    # If GITHUB_WEBHOOK_SECRET is set, reject any invalid or missing signature
    if settings.GITHUB_WEBHOOK_SECRET:
        if not verify_github_signature(body_bytes, x_hub_signature_256, settings.GITHUB_WEBHOOK_SECRET):
            logger.warning("unauthorized_github_webhook", signature=x_hub_signature_256)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Hub-Signature-256 header",
            )
    else:
        logger.warning("github_webhook_secret_not_configured_signature_skipped")

    # 2. Handle Ping event
    if x_github_event == "ping":
        logger.info("github_ping_received")
        return WebhookResponse(
            status="ok",
            message="Pong! GitHub webhook connection established successfully.",
        )

    # 3. Handle Pull Request events
    if x_github_event == "pull_request":
        try:
            payload: dict[str, Any] = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Malformed JSON payload: {err}",
            ) from err

        action = payload.get("action", "")
        # Trigger review on opened, synchronize (new push), or reopened
        if action in ("opened", "synchronize", "reopened"):
            repo_data = payload.get("repository", {})
            pr_data = payload.get("pull_request", {})

            repo_full_name = repo_data.get("full_name")
            pr_number = pr_data.get("number")
            head_sha = pr_data.get("head", {}).get("sha")
            title = pr_data.get("title")

            if not repo_full_name or not pr_number:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing repository.full_name or pull_request.number in webhook payload",
                )

            review_service = get_review_service()
            task = await review_service.queue_pr_review(
                provider="github",
                repo_full_name=repo_full_name,
                pr_number=int(pr_number),
                head_sha=head_sha,
                title=title,
            )

            response.status_code = status.HTTP_202_ACCEPTED
            return WebhookResponse(
                status="accepted",
                action=action,
                task_id=task.task_id,
                message=f"Review task queued for GitHub PR #{pr_number} in {repo_full_name}",
            )

        return WebhookResponse(
            status="ignored",
            action=action,
            message=f"Ignored pull_request action '{action}'. Reviews trigger only on opened/synchronize/reopened.",
        )

    return WebhookResponse(
        status="ignored",
        message=f"Ignored GitHub event '{x_github_event}'. Only 'pull_request' and 'ping' events trigger reviews.",
    )


@router.post(
    "/gitea",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
    summary="Gitea Webhook Ingestion Endpoint",
    description="Receives Gitea webhook deliveries and enqueues PR reviews.",
)
async def gitea_webhook(
    request: Request,
    response: Response,
    x_gitea_event: str | None = Header(None, alias="X-Gitea-Event"),
) -> WebhookResponse:
    try:
        body_bytes = await request.body()
        payload: dict[str, Any] = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON payload: {err}",
        ) from err

    # Gitea delivers event type in header or in body for some versions
    event = x_gitea_event or payload.get("event")

    if event == "pull_request":
        action = payload.get("action", "")
        # Gitea uses "opened", "reopened", "synchronized", or "synchronize"
        if action in ("opened", "synchronize", "synchronized", "reopened"):
            repo_data = payload.get("repository", {})
            pr_data = payload.get("pull_request", {})

            repo_full_name = repo_data.get("full_name")
            pr_number = pr_data.get("number") or payload.get("number")
            head_sha = pr_data.get("head", {}).get("sha")
            title = pr_data.get("title")

            if not repo_full_name or not pr_number:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing repository.full_name or pull_request.number in Gitea webhook payload",
                )

            review_service = get_review_service()
            task = await review_service.queue_pr_review(
                provider="gitea",
                repo_full_name=repo_full_name,
                pr_number=int(pr_number),
                head_sha=head_sha,
                title=title,
            )

            response.status_code = status.HTTP_202_ACCEPTED
            return WebhookResponse(
                status="accepted",
                action=action,
                task_id=task.task_id,
                message=f"Review task queued for Gitea PR #{pr_number} in {repo_full_name}",
            )

        return WebhookResponse(
            status="ignored",
            action=action,
            message=f"Ignored Gitea pull_request action '{action}'. Reviews trigger only on opened/synchronize/reopened.",
        )

    return WebhookResponse(
        status="ignored",
        message=f"Ignored Gitea event '{event}'. Only 'pull_request' events trigger reviews.",
    )
