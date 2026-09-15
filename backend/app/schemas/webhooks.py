
from pydantic import BaseModel, Field


class WebhookResponse(BaseModel):
    """Standardized response for incoming webhook calls."""
    status: str = Field(..., description="'ok', 'ignored', or 'accepted'")
    message: str
    action: str | None = None
    task_id: str | None = None
