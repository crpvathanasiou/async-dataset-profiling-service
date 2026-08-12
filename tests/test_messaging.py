"""Unit tests for the versioned SQS message envelope (no AWS)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.messaging.schemas import (
    JobCommandMessage,
    MessageType,
    ProcessDatasetPayload,
    build_process_dataset_message,
)


def test_build_process_dataset_message_contract() -> None:
    msg = build_process_dataset_message(
        job_id="job-1",
        s3_bucket="bucket-a",
        s3_key="incoming/job-1/data.csv",
    )
    assert msg.message_type == MessageType.PROCESS_DATASET
    assert msg.schema_version == 1
    assert msg.job_id == "job-1"
    assert msg.payload.s3_bucket == "bucket-a"
    assert msg.payload.s3_key == "incoming/job-1/data.csv"
    assert msg.message_id
    assert msg.created_at.tzinfo is not None


def test_envelope_round_trip_json() -> None:
    original = build_process_dataset_message(
        job_id="job-2",
        s3_bucket="b",
        s3_key="incoming/job-2/x.csv",
    )
    parsed = JobCommandMessage.model_validate_json(original.model_dump_json())
    assert parsed == original


def test_envelope_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError):
        JobCommandMessage(
            message_id="m1",
            message_type=MessageType.PROCESS_DATASET,
            schema_version=1,
            created_at=datetime(2026, 8, 12, 12, 0, 0),  # naive
            job_id="job-1",
            payload=ProcessDatasetPayload(s3_bucket="b", s3_key="k"),
        )


def test_envelope_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        JobCommandMessage.model_validate(
            {
                "message_id": "m1",
                "message_type": "PROCESS_DATASET",
                "schema_version": 99,
                "created_at": datetime.now(UTC).isoformat(),
                "job_id": "job-1",
                "payload": {"s3_bucket": "b", "s3_key": "k"},
            }
        )


def test_envelope_rejects_unknown_message_type() -> None:
    with pytest.raises(ValidationError):
        JobCommandMessage.model_validate(
            {
                "message_id": "m1",
                "message_type": "UNKNOWN",
                "schema_version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "job_id": "job-1",
                "payload": {"s3_bucket": "b", "s3_key": "k"},
            }
        )


def test_message_ids_are_unique() -> None:
    a = build_process_dataset_message(job_id="j", s3_bucket="b", s3_key="k")
    b = build_process_dataset_message(job_id="j", s3_bucket="b", s3_key="k")
    assert a.message_id != b.message_id
