from __future__ import annotations

import logging
import uuid

from app.infrastructure.s3 import (
    S3AccessDeniedError,
    S3ObjectNotFoundError,
    S3Storage,
    S3UnavailableError,
)
from app.infrastructure.sqs import SQSAccessDeniedError, SQSQueue, SQSUnavailableError
from app.intake.job_registry import IntakeJobRecord, JobRegistry
from app.intake.schemas import (
    CreateUploadResponse,
    FileInfo,
    IntakeErrorCode,
    IntakeJobResponse,
    JobStatus,
    ValidationErrorCode,
    ValidationErrorItem,
    ValidationResult,
)
from app.intake.validators import (
    sanitize_filename,
    validate_csv_structure,
    validate_file,
    validate_filename,
)
from app.messaging.schemas import build_process_dataset_message
from app.settings import Settings

logger = logging.getLogger("async-dataset-profiling-service.intake")

# M3 technical debt:
# - No transactional outbox (no persistent DB). Publish failure leaves the job
#   VALIDATING so /validate can be retried.
# - Process-local message_published prevents duplicate publishes within this
#   process only. A crash between successful SQS SendMessage and updating the
#   in-memory registry can still produce a duplicate on retry. Durable
#   idempotency / transactional outbox remains future work. Do not treat M3 as
#   exactly-once publishing.


class IntakeServiceError(Exception):
    """Controlled non-validation failure for Stage 1 intake."""

    def __init__(self, code: IntakeErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class FilenameValidationError(Exception):
    """Filename rejected before an upload job is created."""

    def __init__(self, errors: list[ValidationErrorItem]) -> None:
        self.errors = errors
        super().__init__(errors[0].message if errors else "Invalid filename.")


def process_intake(
    *,
    filename: str | None,
    content: bytes,
    settings: Settings,
) -> IntakeJobResponse:
    """
    Legacy multipart path: validate content within the request lifecycle.

    Logical states progress RECEIVED → VALIDATING → VALIDATED | REJECTED.
    """
    job_id = str(uuid.uuid4())
    return _validate_content(
        job_id=job_id,
        filename=filename,
        content=content,
        settings=settings,
    )


def create_presigned_upload(
    *,
    filename: str,
    settings: Settings,
    s3: S3Storage,
    registry: JobRegistry,
) -> CreateUploadResponse:
    """Primary M2 path: create job context and return a presigned PUT URL."""
    filename_errors = validate_filename(filename)
    if filename_errors:
        raise FilenameValidationError(filename_errors)

    job_id = str(uuid.uuid4())
    safe_name = sanitize_filename(filename)
    s3_key = f"incoming/{job_id}/{safe_name}"

    registry.put(
        IntakeJobRecord(
            job_id=job_id,
            filename=filename,
            s3_key=s3_key,
            status=JobStatus.RECEIVED,
        )
    )

    upload_url = s3.create_presigned_put_url(
        key=s3_key,
        expires_in_seconds=settings.presigned_url_expiry_seconds,
    )

    logger.info(
        "intake upload_created job_id=%s filename=%s s3_key=%s expires_in=%s",
        job_id,
        filename,
        s3_key,
        settings.presigned_url_expiry_seconds,
    )

    return CreateUploadResponse(
        job_id=job_id,
        filename=filename,
        s3_key=s3_key,
        upload_url=upload_url,
        expires_in_seconds=settings.presigned_url_expiry_seconds,
    )


def validate_s3_intake_job(
    *,
    job_id: str,
    settings: Settings,
    s3: S3Storage,
    sqs: SQSQueue,
    registry: JobRegistry,
) -> IntakeJobResponse:
    """Validate an object previously reserved via the presigned upload flow."""
    record = registry.get(job_id)
    if record is None:
        raise IntakeServiceError(
            IntakeErrorCode.JOB_NOT_FOUND,
            "No intake job was found for the provided job_id.",
        )

    registry.update_status(job_id, JobStatus.VALIDATING)
    logger.info(
        "intake job_id=%s state=%s s3_key=%s validation_start",
        job_id,
        JobStatus.VALIDATING.value,
        record.s3_key,
    )

    try:
        head = s3.head_object(key=record.s3_key)
    except S3ObjectNotFoundError as exc:
        raise IntakeServiceError(
            IntakeErrorCode.OBJECT_NOT_FOUND,
            "Uploaded object was not found.",
        ) from exc
    except S3AccessDeniedError as exc:
        raise IntakeServiceError(
            IntakeErrorCode.S3_ACCESS_DENIED,
            "Access to the S3 object was denied.",
        ) from exc
    except S3UnavailableError as exc:
        raise IntakeServiceError(
            IntakeErrorCode.S3_UNAVAILABLE,
            "S3 is temporarily unavailable.",
        ) from exc

    size_bytes = head.content_length
    if size_bytes == 0:
        response = _rejected(
            job_id=job_id,
            filename=record.filename,
            size_bytes=0,
            errors=[
                ValidationErrorItem(
                    code=ValidationErrorCode.FILE_EMPTY,
                    message="Uploaded file is empty.",
                )
            ],
        )
        registry.update_status(job_id, JobStatus.REJECTED)
        return response

    if size_bytes > settings.max_upload_size_bytes:
        response = _rejected(
            job_id=job_id,
            filename=record.filename,
            size_bytes=size_bytes,
            errors=[
                ValidationErrorItem(
                    code=ValidationErrorCode.FILE_TOO_LARGE,
                    message="Uploaded file exceeds the configured maximum size.",
                )
            ],
        )
        registry.update_status(job_id, JobStatus.REJECTED)
        return response

    try:
        content = s3.get_object_bytes(key=record.s3_key)
    except S3ObjectNotFoundError as exc:
        raise IntakeServiceError(
            IntakeErrorCode.OBJECT_NOT_FOUND,
            "Uploaded object was not found.",
        ) from exc
    except S3AccessDeniedError as exc:
        raise IntakeServiceError(
            IntakeErrorCode.S3_ACCESS_DENIED,
            "Access to the S3 object was denied.",
        ) from exc
    except S3UnavailableError as exc:
        raise IntakeServiceError(
            IntakeErrorCode.S3_UNAVAILABLE,
            "S3 is temporarily unavailable.",
        ) from exc

    response = _validate_content(
        job_id=job_id,
        filename=record.filename,
        content=content,
        settings=settings,
        known_size_bytes=size_bytes,
    )

    if response.status == JobStatus.VALIDATED:
        if record.message_published:
            logger.info(
                "intake job_id=%s message_id=%s already_published skip_publish",
                job_id,
                record.published_message_id,
            )
        else:
            published_message_id = _publish_process_dataset(
                job_id=job_id,
                s3_bucket=s3.bucket,
                s3_key=record.s3_key,
                sqs=sqs,
            )
            registry.mark_published(job_id, published_message_id)

    registry.update_status(job_id, response.status)
    return response


def _publish_process_dataset(
    *,
    job_id: str,
    s3_bucket: str,
    s3_key: str,
    sqs: SQSQueue,
) -> str:
    """
    Publish a PROCESS_DATASET command after successful Stage 1 validation.

    Returns the publisher-generated message_id. Caller must mark the job published
    only after this returns successfully.
    """
    command = build_process_dataset_message(
        job_id=job_id,
        s3_bucket=s3_bucket,
        s3_key=s3_key,
    )
    try:
        sqs_message_id = sqs.send_message(body=command.model_dump_json())
    except SQSAccessDeniedError as exc:
        logger.error(
            "intake job_id=%s message_id=%s sqs_publish_failed reason=access_denied",
            job_id,
            command.message_id,
        )
        raise IntakeServiceError(
            IntakeErrorCode.SQS_ACCESS_DENIED,
            "Access to the job queue was denied.",
        ) from exc
    except SQSUnavailableError as exc:
        logger.error(
            "intake job_id=%s message_id=%s sqs_publish_failed reason=unavailable",
            job_id,
            command.message_id,
        )
        raise IntakeServiceError(
            IntakeErrorCode.SQS_UNAVAILABLE,
            "The job queue is temporarily unavailable.",
        ) from exc

    logger.info(
        "intake job_id=%s message_id=%s message_type=%s sqs_message_id=%s published",
        job_id,
        command.message_id,
        command.message_type.value,
        sqs_message_id,
    )
    return command.message_id


# _ means Private (Internal)
# * (Standalone Asterisk) means "All arguments are required"
# -> IntakeJobResponse: means "Return an IntakeJobResponse"
def _validate_content(
    *,
    job_id: str,
    filename: str | None,
    content: bytes,
    settings: Settings,
    known_size_bytes: int | None = None,
) -> IntakeJobResponse:
    size_bytes = known_size_bytes if known_size_bytes is not None else len(content)

    logger.info(
        "intake job_id=%s state=%s filename=%s size_bytes=%s",
        job_id,
        JobStatus.RECEIVED.value,
        filename,
        size_bytes,
    )
    logger.info(
        "intake job_id=%s state=%s validation_start",
        job_id,
        JobStatus.VALIDATING.value,
    )

    file_errors = validate_file(
        filename=filename,
        content=content,
        max_upload_size_bytes=settings.max_upload_size_bytes,
    )
    if file_errors:
        return _rejected(
            job_id=job_id,
            filename=filename,
            size_bytes=size_bytes,
            errors=file_errors,
        )

    csv_outcome = validate_csv_structure(content)
    if not csv_outcome.passed:
        return _rejected(
            job_id=job_id,
            filename=filename,
            size_bytes=size_bytes,
            errors=csv_outcome.errors,
            row_count=csv_outcome.row_count,
            column_count=csv_outcome.column_count,
        )

    logger.info(
        "intake job_id=%s state=%s outcome=validated row_count=%s column_count=%s",
        job_id,
        JobStatus.VALIDATED.value,
        csv_outcome.row_count,
        csv_outcome.column_count,
    )
    return IntakeJobResponse(
        job_id=job_id,
        status=JobStatus.VALIDATED,
        file=FileInfo(
            filename=filename,
            size_bytes=size_bytes,
            row_count=csv_outcome.row_count,
            column_count=csv_outcome.column_count,
        ),
        validation=ValidationResult(passed=True, errors=[], warnings=[]),
    )


def _rejected(
    *,
    job_id: str,
    filename: str | None,
    size_bytes: int,
    errors: list[ValidationErrorItem],
    row_count: int | None = None,
    column_count: int | None = None,
) -> IntakeJobResponse:
    codes = [error.code.value for error in errors]
    logger.info(
        "intake job_id=%s state=%s outcome=rejected codes=%s",
        job_id,
        JobStatus.REJECTED.value,
        codes,
    )
    return IntakeJobResponse(
        job_id=job_id,
        status=JobStatus.REJECTED,
        file=FileInfo(
            filename=filename,
            size_bytes=size_bytes,
            row_count=row_count,
            column_count=column_count,
        ),
        validation=ValidationResult(passed=False, errors=errors, warnings=[]),
    )
