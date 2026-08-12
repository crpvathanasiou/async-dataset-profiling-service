"""
Stage 1 HTTP surface.

This module is the only place in the intake package that knows about HTTP. It
declares routes, documents them for OpenAPI, resolves dependencies, and turns
service-level exceptions into HTTP status codes.

Request path:
    client -> FastAPI router -> intake service -> S3/SQS adapters + registry

Routers stay thin on purpose: no validation rules, no AWS calls, no job state
transitions. Each handler does three things — collect input, call one service
function, and translate a domain error into an `AppApiError`. That keeps the use
cases testable without an HTTP layer and keeps HTTP decisions out of services.

The mapping this module owns:
    FilenameValidationError                     -> 400
    JOB_NOT_FOUND / OBJECT_NOT_FOUND            -> 404
    every other IntakeErrorCode (S3/SQS)        -> 503 (retryable)
"""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.infrastructure.s3 import S3Storage
from app.infrastructure.sqs import SQSQueue
from app.intake.errors import AppApiError
from app.intake.job_registry import InMemoryJobRegistry, get_job_registry
from app.intake.schemas import (
    ApiErrorResponse,
    CreateUploadRequest,
    CreateUploadResponse,
    IntakeErrorCode,
    IntakeJobResponse,
)
from app.intake.service import (
    FilenameValidationError,
    IntakeServiceError,
    create_presigned_upload,
    process_intake,
    validate_s3_intake_job,
)
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/intake", tags=["intake"])


@lru_cache(maxsize=1)
def get_s3_storage() -> S3Storage:
    """
    Dependency provider for the S3 adapter.

    Cached because a boto3 client is comparatively expensive to build (it loads
    service models and resolves credentials) and is safe to reuse across
    requests. Bucket and region come from settings, never from the request.

    Being a dependency also makes it overridable: tests replace it through
    `app.dependency_overrides` so no AWS access is needed.
    """
    settings = get_settings()
    return S3Storage(region_name=settings.aws_region, bucket=settings.s3_input_bucket)


@lru_cache(maxsize=1)
def get_sqs_queue() -> SQSQueue:
    """
    Dependency provider for the SQS adapter used by the publish side.

    Same reasoning as `get_s3_storage`: one reusable client per process, queue
    URL and region injected from configuration. The worker process builds its own
    `SQSQueue` instance in `app/worker/main.py`; the two processes share the queue,
    not the client object.
    """
    settings = get_settings()
    return SQSQueue(region_name=settings.aws_region, queue_url=settings.sqs_job_queue_url)


@router.post(
    "/uploads",
    response_model=CreateUploadResponse,
    summary="Create a Stage 1 presigned S3 upload",
    description=(
        "Primary production intake path. Creates a job_id and S3 object key, "
        "then returns a short-lived presigned PUT URL. The client uploads directly to S3."
    ),
    responses={
        400: {"model": ApiErrorResponse, "description": "Invalid filename."},
    },
)
def create_upload(
    body: CreateUploadRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    s3: Annotated[S3Storage, Depends(get_s3_storage)],
    registry: Annotated[InMemoryJobRegistry, Depends(get_job_registry)],
) -> CreateUploadResponse:
    """
    Step 1 of the primary flow: reserve a job and hand out a presigned PUT URL.

    The dataset itself never passes through this endpoint; the client uploads it
    straight to S3 with the returned URL. No SQS message is published here,
    because nothing has been validated yet.

    A rejected filename is a client mistake (HTTP 400), unlike a rejected
    dataset, which is reported as `REJECTED` with HTTP 200 by the validate
    endpoints.
    """
    try:
        return create_presigned_upload(
            filename=body.filename,
            settings=settings,
            s3=s3,
            registry=registry,
        )
    except FilenameValidationError as exc:
        # The service reports every filename problem it found; the API surfaces
        # the first one, since the request cannot proceed either way.
        error = exc.errors[0]
        raise AppApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=error.code.value,
            message=error.message,
        ) from exc


@router.post(
    "/jobs/{job_id}/validate",
    response_model=IntakeJobResponse,
    summary="Validate a previously uploaded S3 object",
    description=(
        "Primary production validation path. Looks up the server-generated S3 key for the "
        "job, reads the object, and returns VALIDATED or REJECTED."
    ),
    responses={
        404: {"model": ApiErrorResponse, "description": "Job or S3 object not found."},
        503: {
            "model": ApiErrorResponse,
            "description": "S3 or SQS access/availability failure.",
        },
    },
)
def validate_job(
    job_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    s3: Annotated[S3Storage, Depends(get_s3_storage)],
    sqs: Annotated[SQSQueue, Depends(get_sqs_queue)],
    registry: Annotated[InMemoryJobRegistry, Depends(get_job_registry)],
) -> IntakeJobResponse:
    """
    Step 2 of the primary flow: validate the uploaded object and hand it off.

    Only the `job_id` is accepted from the client; the S3 key is resolved from the
    registry, so a caller cannot aim validation at an arbitrary object.

    This endpoint contains the asynchronous boundary: on `VALIDATED` the service
    publishes a `PROCESS_DATASET` command to SQS, and the actual processing
    happens later in the worker process. The response therefore reports the
    validation verdict, not the processing result.

    Both outcomes below are HTTP 200 with a body: `VALIDATED` and `REJECTED`. Only
    failures of the operation itself become error responses.
    """
    try:
        return validate_s3_intake_job(
            job_id=job_id,
            settings=settings,
            s3=s3,
            sqs=sqs,
            registry=registry,
        )
    except IntakeServiceError as exc:
        # Missing job/object means the client asked about something that is not
        # there; the remaining codes are S3/SQS trouble on our side and are
        # reported as retryable 503 rather than as a dataset rejection.
        if exc.code in {IntakeErrorCode.JOB_NOT_FOUND, IntakeErrorCode.OBJECT_NOT_FOUND}:
            raise AppApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code=exc.code.value,
                message=exc.message,
            ) from exc
        raise AppApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=exc.code.value,
            message=exc.message,
        ) from exc


@router.post(
    "/jobs",
    response_model=IntakeJobResponse,
    summary="[Legacy/local] Submit a CSV via multipart upload",
    description=(
        "Legacy/local development path. Accepts a multipart CSV upload and validates it "
        "synchronously in the request. Prefer POST /api/v1/intake/uploads for production."
    ),
    tags=["intake-legacy"],
)
async def create_intake_job_legacy(
    file: Annotated[UploadFile, File(description="CSV file to validate.")],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IntakeJobResponse:
    """
    Legacy/local path: upload and validate inside one request.

    Kept for quick local experiments and as a reference for the pure validation
    rules. It differs from the production path in three ways: the file passes
    through the application process, no job is stored in the registry, and no SQS
    message is published — so it never reaches the worker.

    Declared `async` because reading an `UploadFile` is awaited; the validation
    that follows is synchronous CPU work.
    """
    content = await file.read()
    return process_intake(
        filename=file.filename,
        content=content,
        settings=settings,
    )
