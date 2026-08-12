from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Stage 1 job states. Terminal API responses use VALIDATED or REJECTED."""

    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class ValidationErrorCode(str, Enum):
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
    """Controlled API/infrastructure errors for the S3-backed intake path."""

    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
    S3_ACCESS_DENIED = "S3_ACCESS_DENIED"
    S3_UNAVAILABLE = "S3_UNAVAILABLE"
    SQS_UNAVAILABLE = "SQS_UNAVAILABLE"
    SQS_ACCESS_DENIED = "SQS_ACCESS_DENIED"


class ValidationErrorItem(BaseModel):
    code: ValidationErrorCode = Field(description="Stable machine-readable validation error code.")
    message: str = Field(description="Human-readable explanation of the validation failure.")


class FileInfo(BaseModel):
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
    job_id: str = Field(description="Unique identifier for this intake attempt.")
    status: JobStatus = Field(description="Terminal Stage 1 status: VALIDATED or REJECTED.")
    file: FileInfo
    validation: ValidationResult


class CreateUploadRequest(BaseModel):
    filename: str = Field(description="Original CSV filename provided by the client.")


class CreateUploadResponse(BaseModel):
    job_id: str = Field(description="Unique identifier for this intake attempt.")
    filename: str = Field(description="Original client-provided filename.")
    s3_key: str = Field(description="Server-generated S3 object key for the upload.")
    upload_url: str = Field(description="Presigned S3 PUT URL. Does not include AWS credentials.")
    expires_in_seconds: int = Field(description="Presigned URL lifetime in seconds.")


class ApiErrorResponse(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error message.")
