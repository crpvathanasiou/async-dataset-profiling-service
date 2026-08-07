import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.intake.schemas import ValidationErrorCode, ValidationErrorItem


@dataclass
class ValidationOutcome:
    errors: list[ValidationErrorItem] = field(default_factory=list)
    row_count: int | None = None
    column_count: int | None = None

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


def _error(code: ValidationErrorCode, message: str) -> ValidationErrorItem:
    return ValidationErrorItem(code=code, message=message)


def validate_filename(filename: str | None) -> list[ValidationErrorItem]:
    """Filename checks shared by multipart and presigned upload flows."""
    if filename is None or filename.strip() == "":
        return [
            _error(
                ValidationErrorCode.MISSING_FILENAME,
                "Uploaded file is missing a filename.",
            )
        ]

    if not filename.lower().endswith(".csv"):
        return [
            _error(
                ValidationErrorCode.UNSUPPORTED_FILE_TYPE,
                "Only .csv files are accepted.",
            )
        ]

    return []


def sanitize_filename(filename: str) -> str:
    """
    Produce a safe object-name segment for S3 keys.

    Strips directories and replaces characters outside a conservative allow-list.
    """
    base = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not base:
        base = "upload.csv"
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not sanitized.lower().endswith(".csv"):
        sanitized = f"{sanitized}.csv"
    return sanitized


def validate_file(
    *,
    filename: str | None,
    content: bytes,
    max_upload_size_bytes: int,
) -> list[ValidationErrorItem]:
    """Deterministic file-level checks before CSV parsing."""
    errors = validate_filename(filename)
    if errors:
        return errors

    if len(content) == 0:
        return [
            _error(
                ValidationErrorCode.FILE_EMPTY,
                "Uploaded file is empty.",
            )
        ]

    if len(content) > max_upload_size_bytes:
        return [
            _error(
                ValidationErrorCode.FILE_TOO_LARGE,
                "Uploaded file exceeds the configured maximum size.",
            )
        ]

    return []


def validate_csv_structure(content: bytes) -> ValidationOutcome:
    """Deterministic CSV structural checks. Does not profile data quality."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return ValidationOutcome(
            errors=[
                _error(
                    ValidationErrorCode.CSV_PARSE_ERROR,
                    "File could not be read as a UTF-8 CSV.",
                )
            ]
        )

    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return ValidationOutcome(
            errors=[
                _error(
                    ValidationErrorCode.CSV_PARSE_ERROR,
                    "File could not be parsed as CSV.",
                )
            ]
        )

    # Drop trailing completely empty rows produced by a final newline.
    while rows and all(cell.strip() == "" for cell in rows[-1]):
        rows.pop()

    if not rows:
        return ValidationOutcome(
            errors=[
                _error(
                    ValidationErrorCode.CSV_HEADER_MISSING,
                    "CSV is missing a header row.",
                )
            ]
        )

    header = rows[0]
    if len(header) == 0:
        return ValidationOutcome(
            errors=[
                _error(
                    ValidationErrorCode.CSV_NO_COLUMNS,
                    "CSV header must contain at least one column.",
                )
            ]
        )

    errors: list[ValidationErrorItem] = []

    if any(cell.strip() == "" for cell in header):
        errors.append(
            _error(
                ValidationErrorCode.CSV_BLANK_COLUMN_NAME,
                "CSV contains a blank column name.",
            )
        )

    normalized = [cell.strip() for cell in header]
    if len(normalized) != len(set(normalized)):
        errors.append(
            _error(
                ValidationErrorCode.CSV_DUPLICATE_COLUMNS,
                "CSV contains duplicate column names.",
            )
        )

    data_rows = rows[1:]
    if len(data_rows) == 0:
        errors.append(
            _error(
                ValidationErrorCode.CSV_NO_DATA_ROWS,
                "CSV must contain at least one data row.",
            )
        )

    if errors:
        return ValidationOutcome(
            errors=errors,
            row_count=len(data_rows),
            column_count=len(header),
        )

    return ValidationOutcome(
        errors=[],
        row_count=len(data_rows),
        column_count=len(header),
    )
