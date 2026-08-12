"""
Stage 1 intake orchestration.

This module owns the use cases: it decides the order of operations, when AWS is
called, how job state moves, and when work is handed off to the asynchronous
side of the system. It performs no HTTP work and contains no boto3 calls.

Collaborators:
    router      -> calls the functions here (create upload / validate job)
    registry    -> job metadata and state between the two requests
    S3 adapter  -> presigned URL, HeadObject, GetObject
    validators  -> deterministic accept/reject rules
    messaging   -> builds the versioned PROCESS_DATASET envelope
    SQS adapter -> SendMessage (the asynchronous boundary)

Primary flow implemented here:

    RECEIVED
       |                 (create_presigned_upload: job + presigned PUT URL)
       v
    VALIDATING
       |
       v
    S3 HeadObject        cheap metadata: exists? how big?
       |
       v
    S3 GetObject         only for objects that passed the size checks
       |
       v
    CSV validation       deterministic rules in app/intake/validators.py
       |
       v
    VALIDATED
       |
       v
    Build PROCESS_DATASET message
       |
       v
    SendMessage to SQS   <-- synchronous request work ends here;
                             the worker process continues asynchronously

Two failure kinds are kept strictly apart:
- business rejection: the object is readable but unacceptable. The job becomes
  REJECTED, the client gets HTTP 200, and nothing is published. Publishing a
  rejected job would make the worker process data Stage 1 already refused.
- infrastructure failure: S3/SQS could not be used, so no verdict was reached.
  `IntakeServiceError` is raised, the job stays VALIDATING (never REJECTED), and
  the client can retry.

M3 technical debt:
- No transactional outbox (no persistent DB). Publish failure leaves the job
  VALIDATING so /validate can be retried.
- Process-local message_published prevents duplicate publishes within this
  process only. A crash between successful SQS SendMessage and updating the
  in-memory registry can still produce a duplicate on retry. Durable
  idempotency / transactional outbox remains future work. Do not treat M3 as
  exactly-once publishing.
"""

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

# Named logger so intake lines can be filtered separately from worker lines in
# aggregated container logs.
logger = logging.getLogger("async-dataset-profiling-service.intake")


class IntakeServiceError(Exception):
    """
    A controlled failure of the operation itself, not a verdict on the dataset.

    Carries an `IntakeErrorCode` so the router can choose an HTTP status without
    inspecting AWS details. Raising this leaves the job in its current state, so
    an S3 or SQS problem never masquerades as a rejected file.
    """

    def __init__(self, code: IntakeErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class FilenameValidationError(Exception):
    """
    Filename rejected before an upload job is created.

    Separate from `IntakeServiceError` because it happens before any job or S3
    key exists: there is nothing to record and nothing to retry. The router maps
    it to HTTP 400.
    """

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

    Inputs: the filename and bytes that arrived in the request, plus settings for
    the size limit. Output: the verdict for this attempt.

    Deliberately narrower than the production path: it stores no job in the
    registry, touches no S3 object, and publishes no SQS message. It exercises
    the same validation rules synchronously, which is why it stays useful for
    local checks while remaining outside the asynchronous flow.
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
    """
    Primary M2 path: create job context and return a presigned PUT URL.

    Inputs: the client's filename, settings, and the S3/registry collaborators.
    Output: `job_id`, the server-generated `s3_key`, and a short-lived upload URL.

    Order matters here:
    1. reject an unusable filename first, so no job and no key are created for it
    2. generate the key server-side from `job_id` + sanitized name, so the client
       cannot choose its location in the bucket and two jobs cannot collide
    3. store the record (state RECEIVED) before returning, so the later validate
       request can resolve `job_id` to this exact key

    Side effects: one registry write; the presigned URL is produced by local
    signing, so no S3 API call happens and the object does not exist yet.
    """
    filename_errors = validate_filename(filename)
    if filename_errors:
        raise FilenameValidationError(filename_errors)

    job_id = str(uuid.uuid4())
    safe_name = sanitize_filename(filename)
    # Job-scoped prefix: one job's upload can never overwrite another's object.
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

    # The key and expiry are logged; the presigned URL itself is not, since it
    # authorizes a write to the bucket.
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
    """
    Validate an object previously reserved via the presigned upload flow.

    Inputs: the `job_id` from the URL plus the settings, S3, SQS, and registry
    collaborators. Output: an `IntakeJobResponse` whose status is VALIDATED or
    REJECTED.

    Side effects, in order: job state transitions in the registry, two S3 reads,
    and — only for a validated job that has not been handed off yet — one SQS
    SendMessage plus a publish marker in the registry.

    Raises `IntakeServiceError` when the job is unknown or when S3/SQS fails; in
    those cases no verdict is recorded and the client may retry.
    """
    record = registry.get(job_id)
    if record is None:
        # Unknown job: either never created, or created by another process /
        # before a restart — see the in-memory registry limitations.
        raise IntakeServiceError(
            IntakeErrorCode.JOB_NOT_FOUND,
            "No intake job was found for the provided job_id.",
        )

    # State moves to VALIDATING before the first AWS call, so an interrupted
    # validation is distinguishable from one that never started.
    registry.update_status(job_id, JobStatus.VALIDATING)
    logger.info(
        "intake job_id=%s state=%s s3_key=%s validation_start",
        job_id,
        JobStatus.VALIDATING.value,
        record.s3_key,
    )

    # HeadObject before GetObject: it returns metadata without transferring the
    # body, so existence and size are settled before any bytes are downloaded.
    # This is also what lets an oversized upload be rejected without paying for
    # the transfer or holding it in memory.
    try:
        head = s3.head_object(key=record.s3_key)
    except S3ObjectNotFoundError as exc:
        # Typically the client never completed the presigned PUT.
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

    # Metadata-only rejections below: business outcomes (HTTP 200, REJECTED) that
    # are decided without downloading the object, and are never published to SQS.
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

    # Size is acceptable, so the body is now downloaded for structural checks.
    try:
        content = s3.get_object_bytes(key=record.s3_key)
    except S3ObjectNotFoundError as exc:
        # Possible even after a successful HeadObject: the object may have been
        # deleted in between, so the same mapping is applied again.
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

    # `known_size_bytes` reports the size S3 confirmed, rather than re-deriving
    # it from the downloaded buffer.
    response = _validate_content(
        job_id=job_id,
        filename=record.filename,
        content=content,
        settings=settings,
        known_size_bytes=size_bytes,
    )

    # The asynchronous handoff happens only for a validated job.
    if response.status == JobStatus.VALIDATED:
        if record.message_published:
            # Repeated /validate for a job already handed off: publishing again
            # would enqueue the same work twice. This guard is process-local
            # state, not a durable exactly-once guarantee.
            logger.info(
                "intake job_id=%s message_id=%s already_published skip_publish",
                job_id,
                record.published_message_id,
            )
        else:
            # Publish first, mark second: the marker must only be set once AWS
            # has accepted the message. The reverse order could mark a job as
            # handed off although the queue never received the command.
            published_message_id = _publish_process_dataset(
                job_id=job_id,
                s3_bucket=s3.bucket,
                s3_key=record.s3_key,
                sqs=sqs,
            )
            registry.mark_published(job_id, published_message_id)

    # Terminal state is recorded after the handoff attempt, so a failed publish
    # (which raises) leaves the job VALIDATING and therefore retryable.
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

    Inputs: the business `job_id`, the S3 location the worker should read, and the
    SQS adapter. The message carries only that pointer — never the dataset bytes,
    which stay in S3.

    This is the moment control leaves the request: once SQS accepts the command,
    the work continues in the separate worker process. Adapter exceptions are
    translated into `IntakeServiceError`, so a publish failure surfaces as a
    controlled infrastructure error instead of the request pretending the job
    entered processing.
    """
    command = build_process_dataset_message(
        job_id=job_id,
        s3_bucket=s3_bucket,
        s3_key=s3_key,
    )
    try:
        sqs_message_id = sqs.send_message(body=command.model_dump_json())
    except SQSAccessDeniedError as exc:
        # `job_id` and our `message_id` are logged even on failure, which is why
        # the envelope is built before the AWS call.
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

    # Both identifiers are logged: ours (`message_id`) and the one SQS assigned
    # (`sqs_message_id`). The pair is what links this log line to the worker's.
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
    """
    Shared validation core for both intake paths.

    Inputs: the job id, filename, full byte content, settings, and optionally the
    size already confirmed by S3. Output: the VALIDATED or REJECTED response.

    Both entry points funnel through here so the multipart path and the S3 path
    cannot diverge in what they accept or in how results are shaped. The function
    only judges content and logs; job state is updated by its callers, and it
    never publishes to SQS.

    Checks run file-level first, then CSV structure, so cheap disqualifications
    happen before parsing.
    """
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
        # Counts are forwarded when the header was readable, so a rejection can
        # still report what was observed.
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
    """
    Build the REJECTED response and log the reason codes.

    One helper for every rejection site keeps the response shape and the log
    format identical no matter which rule fired. Rejection is a normal business
    outcome: it is returned with HTTP 200 and is never published to SQS.

    Only the error codes are logged, not dataset content.
    """
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
