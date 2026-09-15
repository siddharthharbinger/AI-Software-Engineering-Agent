from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()
    settings = get_settings()
    logger.info("app_starting", environment=settings.ENVIRONMENT)
    yield
    # Shutdown
    logger.info("app_stopping")


def create_application() -> FastAPI:
    app = FastAPI(
        title="AI Software Engineering Agent Platform",
        description="Autonomous PR review and bug-fixing platform with dual-mode intake (webhooks & on-demand triggers)",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routers
    app.include_router(api_router, prefix="/api/v1")

    # Health check endpoints
    @app.get("/healthz", tags=["health"])
    async def healthz():
        return {"status": "ok", "service": "ai-swe-agent-backend"}

    @app.get("/readyz", tags=["health"])
    async def readyz():
        return {"status": "ready"}

    return app


app = create_application()
