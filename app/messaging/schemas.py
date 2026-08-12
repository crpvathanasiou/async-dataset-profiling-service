"""Versioned SQS message envelope for Stage 1 → worker handoff."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class MessageType(str, Enum):
    """Supported command types published to the job queue."""

    PROCESS_DATASET = "PROCESS_DATASET"


class ProcessDatasetPayload(BaseModel):
    """Payload for PROCESS_DATASET — points at the validated S3 object."""

    s3_bucket: str = Field(min_length=1, description="S3 bucket holding the validated object.")
    s3_key: str = Field(min_length=1, description="S3 object key for the validated CSV.")


class JobCommandMessage(BaseModel):
    """
    Explicit versioned message contract for the SQS job queue.

    Workers must validate this envelope before processing.
    """

    message_id: str = Field(description="Unique id for this queue message (publisher-generated).")
    message_type: MessageType
    schema_version: Literal[1] = Field(description="Envelope schema version.")
    created_at: datetime = Field(description="UTC timestamp when the message was created.")
    job_id: str = Field(min_length=1, description="Intake job id this command belongs to.")
    payload: ProcessDatasetPayload

    @field_validator("created_at")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (UTC).")
        return value.astimezone(UTC)


def build_process_dataset_message(
    *,
    job_id: str,
    s3_bucket: str,
    s3_key: str,
) -> JobCommandMessage:
    """Construct a PROCESS_DATASET command with a fresh message_id and UTC timestamp."""
    return JobCommandMessage(
        message_id=str(uuid4()),
        message_type=MessageType.PROCESS_DATASET,
        schema_version=1,
        created_at=datetime.now(UTC),
        job_id=job_id,
        payload=ProcessDatasetPayload(s3_bucket=s3_bucket, s3_key=s3_key),
    )
