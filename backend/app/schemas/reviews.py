from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ReviewTriggerRequest(BaseModel):
    """Request payload for manual on-demand PR review trigger."""
    provider: Literal["github", "gitea"] = Field(
        ...,
        description="Git hosting provider ('github' or 'gitea')",
        examples=["github"],
    )
    repo: str = Field(
        ...,
        description="Full repository name in 'owner/repo' format",
        examples=["octocat/Hello-World"],
    )
    pr_number: int = Field(
        ...,
        gt=0,
        description="Pull request number (positive integer)",
        examples=[42],
    )
    org_id: str | None = Field(
        default=None,
        description="Optional organization UUID or identifier",
    )

    @field_validator("repo")
    @classmethod
    def validate_repo_format(cls, v: str) -> str:
        clean = v.strip()
        parts = clean.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("Repository must be in 'owner/repo' format (e.g. 'octocat/Hello-World')")
        return clean


class ReviewTriggerResponse(BaseModel):
    """Response payload returned when a review is successfully queued."""
    task_id: str = Field(..., description="Unique UUID for the queued review task")
    status: str = Field(default="queued", description="Status of the task ('queued', 'processing', 'completed', 'failed')")
    provider: str
    repo: str
    pr_number: int
    message: str
    created_at: datetime


class ReviewFindingItem(BaseModel):
    """Individual code review finding item."""
    file: str
    line: int
    rule_id: str | None = None
    severity: str = "warning"
    title: str
    message: str
    suggestion: str | None = None


class ReviewTaskStatus(BaseModel):
    """Status and result summary for a review task."""
    task_id: str
    status: str
    provider: str
    repo: str
    pr_number: int
    org_id: str | None = None
    head_sha: str | None = None
    title: str | None = None
    findings: list[ReviewFindingItem] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
