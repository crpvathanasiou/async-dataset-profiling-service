"""
Deterministic Stage 1 validation rules.

Pure functions: bytes and strings in, error lists out. No I/O, no AWS, no
logging, no job state — which makes these rules cheap to unit test and safe to
call from either intake path.

Position in the architecture:
    intake service -> validators -> ValidationErrorItem list
    (the service decides what a result means for job state and for the SQS
     handoff; these functions only judge the input)

Scope: structural acceptance only ("is this a readable CSV with a usable
header?"). Statistical profiling of the data is Stage 2 work and is not done
here.

Both intake paths share these functions, so the multipart path and the S3 path
cannot drift apart in what they accept.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.intake.schemas import ValidationErrorCode, ValidationErrorItem


@dataclass
class ValidationOutcome:
    """
    Result of CSV structural validation.

    Carries `row_count`/`column_count` in addition to errors, because those
    figures are worth reporting even for a rejected file whose header could
    still be read. They stay None when structure was never established.
    """

    errors: list[ValidationErrorItem] = field(default_factory=list)
    row_count: int | None = None
    column_count: int | None = None

    @property
    def passed(self) -> bool:
        """Validation passed only when nothing blocking was found."""
        return len(self.errors) == 0


def _error(code: ValidationErrorCode, message: str) -> ValidationErrorItem:
    return ValidationErrorItem(code=code, message=message)


def validate_filename(filename: str | None) -> list[ValidationErrorItem]:
    """
    Filename checks shared by multipart and presigned upload flows.

    Input: the client-provided filename (optional, since a multipart part may
    omit it). Output: an empty list when acceptable, otherwise a single error.

    Called before any object key is created, so an unusable filename is rejected
    without creating a job or touching S3.
    """
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

    Security relevance: the client-supplied name becomes part of an S3 key.
    Dropping every path component defeats traversal attempts such as
    `../../other-prefix/file.csv`, and the allow-list keeps the key free of
    characters that complicate URLs and tooling. The result is only the final
    segment; the job-scoped prefix is added by the service.
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
    """
    Deterministic file-level checks before CSV parsing.

    Input: filename, the full byte content, and the configured size limit.
    Output: the first blocking problem found, or an empty list.

    Ordered cheapest-first (name, then emptiness, then size) so obviously
    unusable input never reaches the parser.
    """
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
    """
    Deterministic CSV structural checks. Does not profile data quality.

    Input: raw bytes. Output: a `ValidationOutcome` with any blocking errors plus
    the row/column counts that could be determined.

    Two decoding/parsing failures are reported with the same
    `CSV_PARSE_ERROR` code, because from the client's perspective both mean "this
    is not a readable UTF-8 CSV".

    Checks are grouped intentionally: fatal problems (undecodable, unparseable,
    no header) return immediately since nothing further can be judged, while
    header/row problems are collected so the client can see all of them at once.
    """
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

    # By convention the first remaining row is the header; every later row is data.
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

    # From here the structure is readable, so remaining findings are accumulated
    # instead of short-circuiting: the client gets the full list in one response.
    errors: list[ValidationErrorItem] = []

    if any(cell.strip() == "" for cell in header):
        errors.append(
            _error(
                ValidationErrorCode.CSV_BLANK_COLUMN_NAME,
                "CSV contains a blank column name.",
            )
        )

    # Compared after stripping, so names differing only by surrounding
    # whitespace still count as duplicates for downstream consumers.
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
        # Counts are still reported for a rejected file: the header was readable,
        # so they are meaningful diagnostics rather than guesses.
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
