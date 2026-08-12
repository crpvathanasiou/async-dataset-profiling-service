"""
Stage 1 HTTP contracts and status/error vocabularies.

These Pydantic models define what the API promises to clients: request bodies,
response bodies, and the stable string codes used in them. FastAPI also derives
the OpenAPI schema from this module, so changes here are changes to a public
contract.

Position in the architecture:
    router  -> imports these models for request/response typing
    service -> builds these models as the outcome of a use case
    tests   -> assert on the code values defined here

Two different failure vocabularies live side by side on purpose:
- `ValidationErrorCode` describes a rejected dataset. The request succeeded
  (HTTP 200) and the answer is "this file is not acceptable".
- `IntakeErrorCode` describes a failure of the operation itself (unknown job,
  or S3/SQS trouble) and is mapped to 4xx/5xx by the router.

This module holds no logic: validation rules live in `app/intake/validators.py`,
orchestration in `app/intake/service.py`, and the internal message contract in
`app/messaging/schemas.py`.
"""

from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """
    Stage 1 job states. Terminal API responses use VALIDATED or REJECTED.

    Progression of the S3-backed path:

        RECEIVED    upload job created, presigned URL handed out, object not
                    inspected yet
        VALIDATING  validation started; also the state a job keeps when
                    validation could not be completed because of an
                    infrastructure failure
        VALIDATED   the object is an acceptable CSV; this is the state that
                    triggers the asynchronous handoff to SQS
        REJECTED    a business rule rejected the object; nothing is published

    Inheriting from `str` keeps the JSON representation the plain code, so the
    enum can be compared with, and serialized as, its string value.
    """

    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class ValidationErrorCode(str, Enum):
    """
    Reasons a dataset can be rejected by Stage 1 business rules.

    Stable machine-readable codes let clients branch on the cause without
    parsing human-readable messages. A rejection is a normal, expected outcome
    and is returned with HTTP 200 alongside `REJECTED`.
    """

    MISSING_FILENAME = "MISSING_FILENAME"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    FILE_EMPTY = "FILE_EMPTY"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    CSV_PARSE_ERROR = "CSV_PARSE_ERROR"
    CSV_HEADER_MISSING = "CSV_HEADER_MISSING"
    CSV_NO_COLUMNS = "CSV_NO_COLUMNS"
    CSV_NO_DATA_ROWS = "CSV_NO_DATA_ROWS"
    CSV_BLANK_COLUMN_NAME = "CSV_BLANK_COLUMN_NAME"
    CSV_DUPLICATE_COLUMNS = "CSV_DUPLICATE_COLUMNS"


class IntakeErrorCode(str, Enum):
    """
    Controlled API/infrastructure errors for the S3-backed intake path.

    These are not statements about the dataset; they say the operation could not
    be carried out. The router maps them to HTTP status codes: missing job or
    object become 404, and the S3/SQS codes become 503 (retryable).

    Each code is deliberately coarse. Detailed AWS error text stays in the
    adapters and logs rather than in client-facing responses.
    """

    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
    S3_ACCESS_DENIED = "S3_ACCESS_DENIED"
    S3_UNAVAILABLE = "S3_UNAVAILABLE"
    SQS_UNAVAILABLE = "SQS_UNAVAILABLE"
    SQS_ACCESS_DENIED = "SQS_ACCESS_DENIED"


class ValidationErrorItem(BaseModel):
    """One rejection reason: a stable code plus an explanation for humans."""

    code: ValidationErrorCode = Field(description="Stable machine-readable validation error code.")
    message: str = Field(description="Human-readable explanation of the validation failure.")


class FileInfo(BaseModel):
    """
    What Stage 1 observed about the uploaded file.

    `row_count` and `column_count` are optional because they can only be
    reported once CSV structure was actually established. A file rejected for
    being too large or unparseable leaves them null rather than reporting a
    misleading zero.
    """

    filename: str | None = Field(description="Original uploaded filename, when available.")
    size_bytes: int = Field(description="Uploaded file size in bytes.")
    row_count: int | None = Field(
        default=None,
        description=(
            "Number of data rows (excluding header). "
            "Null when CSV structure was not established."
        ),
    )
    column_count: int | None = Field(
        default=None,
        description=(
            "Number of columns from the header. "
            "Null when CSV structure was not established."
        ),
    )


class ValidationResult(BaseModel):
    """
    Outcome of the deterministic Stage 1 checks.

    `warnings` is part of the contract from the start so non-blocking findings
    can be reported later without a breaking response change; the current rules
    only produce blocking `errors`.
    """

    passed: bool = Field(description="True when Stage 1 validation succeeded.")
    errors: list[ValidationErrorItem] = Field(
        default_factory=list,
        description="Validation errors that caused rejection.",
    )
    warnings: list[ValidationErrorItem] = Field(
        default_factory=list,
        description="Non-blocking validation warnings.",
    )


class IntakeJobResponse(BaseModel):
    """
    Response of both validation endpoints: the verdict for one intake attempt.

    Returned with HTTP 200 for `VALIDATED` and for `REJECTED`; the `status`
    field, not the HTTP code, carries the business outcome.
    """

    job_id: str = Field(description="Unique identifier for this intake attempt.")
    status: JobStatus = Field(description="Terminal Stage 1 status: VALIDATED or REJECTED.")
    file: FileInfo
    validation: ValidationResult


class CreateUploadRequest(BaseModel):
    """
    Request to start an upload.

    Only the filename is accepted: the object key is generated server-side so a
    client can never choose where in the bucket it writes.
    """

    filename: str = Field(description="Original CSV filename provided by the client.")


class CreateUploadResponse(BaseModel):
    """
    Everything the client needs to upload directly to S3 and then ask for
    validation.

    `s3_key` is returned for traceability, while `upload_url` is a short-lived
    capability: it authorizes a single PUT and must not be logged or shared.
    """

    job_id: str = Field(description="Unique identifier for this intake attempt.")
    filename: str = Field(description="Original client-provided filename.")
    s3_key: str = Field(description="Server-generated S3 object key for the upload.")
    upload_url: str = Field(description="Presigned S3 PUT URL. Does not include AWS credentials.")
    expires_in_seconds: int = Field(description="Presigned URL lifetime in seconds.")


class ApiErrorResponse(BaseModel):
    """
    Single error shape for every controlled failure of this API.

    Kept flat and free of stack traces or AWS text. `app/main.py` renders
    `AppApiError` into exactly this body, so clients can rely on one structure
    instead of FastAPI's default `detail` payload.
    """

    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error message.")
