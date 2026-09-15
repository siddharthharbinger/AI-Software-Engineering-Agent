from fastapi import APIRouter

from app.api.v1.endpoints import reviews, webhooks

api_router = APIRouter()

api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
api_router.include_router(reviews.router, prefix="/reviews", tags=["reviews"])
