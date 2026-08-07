from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.infrastructure.s3 import S3Storage
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
    settings = get_settings()
    return S3Storage(region_name=settings.aws_region, bucket=settings.s3_input_bucket)


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
    try:
        return create_presigned_upload(
            filename=body.filename,
            settings=settings,
            s3=s3,
            registry=registry,
        )
    except FilenameValidationError as exc:
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
        503: {"model": ApiErrorResponse, "description": "S3 access or availability failure."},
    },
)
def validate_job(
    job_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    s3: Annotated[S3Storage, Depends(get_s3_storage)],
    registry: Annotated[InMemoryJobRegistry, Depends(get_job_registry)],
) -> IntakeJobResponse:
    try:
        return validate_s3_intake_job(
            job_id=job_id,
            settings=settings,
            s3=s3,
            registry=registry,
        )
    except IntakeServiceError as exc:
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
    content = await file.read()
    return process_intake(
        filename=file.filename,
        content=content,
        settings=settings,
    )
