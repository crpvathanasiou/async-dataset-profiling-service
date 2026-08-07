import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.intake.errors import AppApiError
from app.intake.router import router as intake_router
from app.intake.schemas import ApiErrorResponse
from app.settings import get_settings

settings = get_settings()


def setup_logging() -> None:
    """
    Logs to stdout/stderr (container-friendly).
    """
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


setup_logging()
logger = logging.getLogger("async-dataset-profiling-service")


class HealthResponse(BaseModel):
    status: str
    app_env: str
    app_version: str


# IMPORTANT:
# Uvicorn loads "app.main:app" by importing app.main and then looking for a variable named "app".
# This must exist at module import time.
app = FastAPI(
    title="async-dataset-profiling-service",
    version=settings.app_version,
)
app.include_router(intake_router)


@app.exception_handler(AppApiError)
async def app_api_error_handler(_request: Request, exc: AppApiError) -> JSONResponse:
    body = ApiErrorResponse(code=exc.code, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.get("/health/live", response_model=HealthResponse)
def health_live() -> HealthResponse:
    """Process is alive."""
    return HealthResponse(
        status="ok",
        app_env=settings.app_env,
        app_version=settings.app_version,
    )


@app.get("/health/ready", response_model=HealthResponse)
def health_ready() -> HealthResponse:
    """Application is ready to receive requests (no external dependency checks yet)."""
    return HealthResponse(
        status="ok",
        app_env=settings.app_env,
        app_version=settings.app_version,
    )


@app.get("/version")
def version() -> dict[str, str]:
    return {"version": settings.app_version}
