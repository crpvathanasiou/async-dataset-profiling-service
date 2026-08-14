"""
FastAPI application bootstrap (the API process).

This module is the composition root of the synchronous half of the system: it
configures logging, creates the ASGI application, mounts routers, registers the
error handler that shapes every controlled failure, and exposes the operational
endpoints (health/version).

    uvicorn -> app.main:app -> intake router -> intake service -> S3/SQS adapters

It is intentionally thin. No validation rules, no AWS calls, no job state — those
live in `app/intake` and `app/infrastructure`. Adding a feature normally means
adding a router/service, not editing this file.

The worker has its own entrypoint (`app/worker/main.py`) and does not import
anything from here; the two processes only meet at the SQS queue.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from fastapi.exceptions import RequestValidationError
from starlette.status import HTTP_422_UNPROCESSABLE_ENTITY, HTTP_500_INTERNAL_SERVER_ERROR


from app.intake.errors import AppApiError
from app.intake.router import router as intake_router
from app.intake.schemas import ApiErrorResponse
from app.settings import get_settings

# Loaded at import time so a misconfigured environment (e.g. missing
# S3_INPUT_BUCKET or SQS_JOB_QUEUE_URL) fails during startup rather than on the
# first request.
settings = get_settings()


def setup_logging() -> None:
    """
    Logs to stdout/stderr (container-friendly).

    The runtime (Docker, later CloudWatch) collects the standard streams, so the
    application writes no log files and needs no rotation. The format matches the
    worker's, which keeps both processes readable in one aggregated stream. An
    unknown `LOG_LEVEL` falls back to INFO instead of failing startup.
    """
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


# Called before the app object exists, so records emitted during import and
# startup are formatted the same way as request-time records.
setup_logging()
logger = logging.getLogger("async-dataset-profiling-service")


class HealthResponse(BaseModel):
    """
    Payload of the health endpoints.

    Environment and version are included so an operator (or a smoke test) can
    confirm which build is answering, not merely that something answers.
    """

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
# Feature routes are mounted, not defined here; the router owns its own prefix
# and tags so this file does not accumulate HTTP details.
# include_router = Assembler: Takes the ready-made building blocks that we made (Prefixes, Sub-paths, Tags, Dependencies)
app.include_router(intake_router)


# Exception handlers: registered at startup; invoked when the matching exception is raised.
@app.exception_handler(AppApiError)
async def app_api_error_handler(_request: Request, exc: AppApiError) -> JSONResponse:
    """
    Render every controlled failure as the documented `ApiErrorResponse` body.

    This single handler is what makes the error contract uniform: routers raise
    `AppApiError` with a status, a stable code, and a safe message, and only those
    fields reach the client. FastAPI's default `detail` shape is bypassed, and
    exception internals (including AWS/SDK text) stay in logs.
    """
    body = ApiErrorResponse(code=exc.code, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.get("/health/live", response_model=HealthResponse)
def health_live() -> HealthResponse:
    """
    Process is alive.

    Liveness must stay dependency-free: it answers "is this process still
    functioning?". If it called S3 or SQS, a transient AWS problem would make the
    orchestrator kill and restart otherwise healthy containers.
    """
    return HealthResponse(
        status="ok",
        app_env=settings.app_env,
        app_version=settings.app_version,
    )


@app.get("/health/ready", response_model=HealthResponse)
def health_ready() -> HealthResponse:
    """
    Application is ready to receive requests (no external dependency checks yet).

    Kept as a separate endpoint from liveness because the two answer different
    questions (restart me vs. send me traffic). It currently performs no
    dependency checks, so it reports the same result as liveness; real readiness
    probes would be added when there is dependency state worth gating traffic on.
    """
    return HealthResponse(
        status="ok",
        app_env=settings.app_env,
        app_version=settings.app_version,
    )


@app.get("/version")
def version() -> dict[str, str]:
    """Report the deployed build, injected via `APP_VERSION`."""
    return {"version": settings.app_version}


# ------------------------------------------------------------------
# 1. Handle Validation Errors (Invalid inputs from the client - 422)
# ------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    body = ApiErrorResponse(
        code="INVALID_INPUT",
        message="The request payload or parameters are invalid."
    )
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY,
        content=body.model_dump()
    )


# ------------------------------------------------------------------
# 2. Handle Catch-All / Unexpected Errors (Unexpected errors - 500)
# ------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Log the actual exception and stack trace for debugging
    logger.exception("Unhandled exception occurred: %s", str(exc))
    
    # Return a safe, generic message to the client without exposing internal details
    body = ApiErrorResponse(
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred. Please try again later."
    )
    return JSONResponse(
        status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        content=body.model_dump()
    )
