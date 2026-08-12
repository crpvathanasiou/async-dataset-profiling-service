from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.infrastructure.s3 import (
    S3AccessDeniedError,
    S3ObjectHead,
    S3ObjectNotFoundError,
    S3Storage,
    S3UnavailableError,
)
from app.infrastructure.sqs import SQSQueue, SQSUnavailableError
from app.intake.job_registry import InMemoryJobRegistry, get_job_registry
from app.intake.router import get_s3_storage, get_sqs_queue
from app.intake.schemas import JobStatus
from app.main import app
from app.messaging.schemas import JobCommandMessage, MessageType
from app.settings import Settings, get_settings

UPLOADS_URL = "/api/v1/intake/uploads"


@pytest.fixture
def registry() -> InMemoryJobRegistry:
    reg = get_job_registry()
    reg.clear()
    return reg


@pytest.fixture
def fake_s3() -> MagicMock:
    mock = MagicMock(spec=S3Storage)
    mock.bucket = "test-bucket"
    mock.create_presigned_put_url.return_value = "https://s3.example/presigned-put"
    return mock


@pytest.fixture
def fake_sqs() -> MagicMock:
    mock = MagicMock(spec=SQSQueue)
    mock.send_message.return_value = "sqs-message-id-1"
    return mock


@pytest.fixture
def client(
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
    registry: InMemoryJobRegistry,
) -> Any:
    app.dependency_overrides[get_s3_storage] = lambda: fake_s3
    app.dependency_overrides[get_sqs_queue] = lambda: fake_sqs
    app.dependency_overrides[get_job_registry] = lambda: registry
    get_settings.cache_clear()
    get_s3_storage.cache_clear()
    get_sqs_queue.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    get_s3_storage.cache_clear()
    get_sqs_queue.cache_clear()
    registry.clear()


def test_missing_s3_input_bucket_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv("S3_INPUT_BUCKET", raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    with pytest.raises(ValidationError) as exc_info:
        Settings()  # pyright: ignore[reportCallIssue]
    error_text = str(exc_info.value)
    assert "S3_INPUT_BUCKET" in error_text or "s3_input_bucket" in error_text
    get_settings.cache_clear()


def test_missing_sqs_job_queue_url_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv("SQS_JOB_QUEUE_URL", raising=False)
    monkeypatch.setenv("S3_INPUT_BUCKET", "test-intake-bucket")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    with pytest.raises(ValidationError) as exc_info:
        Settings()  # pyright: ignore[reportCallIssue]
    error_text = str(exc_info.value)
    assert "SQS_JOB_QUEUE_URL" in error_text or "sqs_job_queue_url" in error_text
    get_settings.cache_clear()


def test_create_upload_returns_presigned_url(client: TestClient, fake_s3: MagicMock) -> None:
    resp = client.post(UPLOADS_URL, json={"filename": "transactions.csv"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "transactions.csv"
    assert data["upload_url"] == "https://s3.example/presigned-put"
    assert data["expires_in_seconds"] == 900
    assert data["s3_key"].startswith(f"incoming/{data['job_id']}/")
    assert data["s3_key"].endswith("transactions.csv")
    fake_s3.create_presigned_put_url.assert_called_once()


def test_create_upload_rejects_non_csv_filename(client: TestClient) -> None:
    resp = client.post(UPLOADS_URL, json={"filename": "transactions.txt"})
    assert resp.status_code == 400
    body = resp.json()
    assert body == {
        "code": "UNSUPPORTED_FILE_TYPE",
        "message": "Only .csv files are accepted.",
    }
    assert "detail" not in body


def test_validate_uploaded_valid_csv_returns_validated(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
) -> None:
    content = b"id,name\n1,alice\n"
    create = client.post(UPLOADS_URL, json={"filename": "ok.csv"}).json()
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content

    resp = client.post(f"/api/v1/intake/jobs/{create['job_id']}/validate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "VALIDATED"
    assert data["validation"]["passed"] is True
    assert data["file"]["row_count"] == 1
    assert data["file"]["column_count"] == 2
    fake_s3.head_object.assert_called_once_with(key=create["s3_key"])
    fake_s3.get_object_bytes.assert_called_once_with(key=create["s3_key"])
    fake_sqs.send_message.assert_called_once()


def test_validated_job_publishes_one_process_dataset_message(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
) -> None:
    content = b"id,name\n1,alice\n"
    create = client.post(UPLOADS_URL, json={"filename": "ok.csv"}).json()
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content

    resp = client.post(f"/api/v1/intake/jobs/{create['job_id']}/validate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "VALIDATED"
    assert fake_sqs.send_message.call_count == 1

    body = fake_sqs.send_message.call_args.kwargs["body"]
    envelope = JobCommandMessage.model_validate_json(body)
    assert envelope.message_type == MessageType.PROCESS_DATASET
    assert envelope.schema_version == 1
    assert envelope.job_id == create["job_id"]
    assert envelope.payload.s3_bucket == "test-bucket"
    assert envelope.payload.s3_key == create["s3_key"]
    assert envelope.message_id


def test_rejected_job_publishes_no_message(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
) -> None:
    content = b"\xff\xfe\x00bad"
    create = client.post(UPLOADS_URL, json={"filename": "bad.csv"}).json()
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content

    resp = client.post(f"/api/v1/intake/jobs/{create['job_id']}/validate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
    fake_sqs.send_message.assert_not_called()


def test_sqs_send_failure_returns_controlled_error_and_keeps_validating(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    content = b"id,name\n1,alice\n"
    create = client.post(UPLOADS_URL, json={"filename": "ok.csv"}).json()
    job_id = create["job_id"]
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content
    fake_sqs.send_message.side_effect = SQSUnavailableError("down")

    resp = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert resp.status_code == 503
    body = resp.json()
    assert body == {
        "code": "SQS_UNAVAILABLE",
        "message": "The job queue is temporarily unavailable.",
    }
    assert "detail" not in body
    updated = registry.get(job_id)
    assert updated is not None
    assert updated.status == JobStatus.VALIDATING
    assert updated.message_published is False
    assert updated.published_message_id is None


def test_first_successful_validate_publishes_exactly_once(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    content = b"id,name\n1,alice\n"
    create = client.post(UPLOADS_URL, json={"filename": "ok.csv"}).json()
    job_id = create["job_id"]
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content

    resp = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "VALIDATED"
    assert fake_sqs.send_message.call_count == 1

    record = registry.get(job_id)
    assert record is not None
    assert record.message_published is True
    assert record.published_message_id is not None


def test_second_validate_does_not_publish_again(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    content = b"id,name\n1,alice\n"
    create = client.post(UPLOADS_URL, json={"filename": "ok.csv"}).json()
    job_id = create["job_id"]
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content

    first = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert first.status_code == 200
    assert first.json()["status"] == "VALIDATED"
    assert fake_sqs.send_message.call_count == 1
    first_message_id = registry.get(job_id)
    assert first_message_id is not None
    published_id = first_message_id.published_message_id

    second = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert second.status_code == 200
    assert second.json()["status"] == "VALIDATED"
    assert fake_sqs.send_message.call_count == 1

    record = registry.get(job_id)
    assert record is not None
    assert record.message_published is True
    assert record.published_message_id == published_id


def test_publish_failure_does_not_mark_published(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    content = b"id,name\n1,alice\n"
    create = client.post(UPLOADS_URL, json={"filename": "ok.csv"}).json()
    job_id = create["job_id"]
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content
    fake_sqs.send_message.side_effect = SQSUnavailableError("down")

    resp = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert resp.status_code == 503
    record = registry.get(job_id)
    assert record is not None
    assert record.message_published is False
    assert record.published_message_id is None


def test_retry_after_publish_failure_is_allowed_to_publish(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    content = b"id,name\n1,alice\n"
    create = client.post(UPLOADS_URL, json={"filename": "ok.csv"}).json()
    job_id = create["job_id"]
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content
    fake_sqs.send_message.side_effect = [
        SQSUnavailableError("down"),
        "sqs-message-id-retry",
    ]

    first = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert first.status_code == 503
    assert fake_sqs.send_message.call_count == 1
    failed = registry.get(job_id)
    assert failed is not None
    assert failed.message_published is False

    second = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert second.status_code == 200
    assert second.json()["status"] == "VALIDATED"
    assert fake_sqs.send_message.call_count == 2

    record = registry.get(job_id)
    assert record is not None
    assert record.message_published is True
    assert record.published_message_id is not None


def test_registry_transitions_received_to_validating_to_validated(
    client: TestClient,
    fake_s3: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    content = b"id,name\n1,alice\n"
    create = client.post(UPLOADS_URL, json={"filename": "ok.csv"}).json()
    job_id = create["job_id"]
    record = registry.get(job_id)
    assert record is not None
    assert record.status == JobStatus.RECEIVED

    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content

    resp = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "VALIDATED"
    updated = registry.get(job_id)
    assert updated is not None
    assert updated.status == JobStatus.VALIDATED


def test_registry_transitions_received_to_validating_to_rejected(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    content = b"\xff\xfe\x00bad"
    create = client.post(UPLOADS_URL, json={"filename": "bad.csv"}).json()
    job_id = create["job_id"]
    record = registry.get(job_id)
    assert record is not None
    assert record.status == JobStatus.RECEIVED

    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content

    resp = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
    updated = registry.get(job_id)
    assert updated is not None
    assert updated.status == JobStatus.REJECTED
    fake_sqs.send_message.assert_not_called()


def test_validate_object_not_found_returns_controlled_error(
    client: TestClient,
    fake_s3: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    create = client.post(UPLOADS_URL, json={"filename": "missing.csv"}).json()
    job_id = create["job_id"]
    fake_s3.head_object.side_effect = S3ObjectNotFoundError("missing")

    resp = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert resp.status_code == 404
    body = resp.json()
    assert body == {
        "code": "OBJECT_NOT_FOUND",
        "message": "Uploaded object was not found.",
    }
    assert "detail" not in body
    # Infrastructure failure must not mark the job REJECTED.
    updated = registry.get(job_id)
    assert updated is not None
    assert updated.status == JobStatus.VALIDATING


def test_s3_infrastructure_failure_does_not_become_rejected(
    client: TestClient,
    fake_s3: MagicMock,
    registry: InMemoryJobRegistry,
) -> None:
    create = client.post(UPLOADS_URL, json={"filename": "denied.csv"}).json()
    job_id = create["job_id"]
    record = registry.get(job_id)
    assert record is not None
    assert record.status == JobStatus.RECEIVED
    fake_s3.head_object.side_effect = S3AccessDeniedError("denied")

    resp = client.post(f"/api/v1/intake/jobs/{job_id}/validate")
    assert resp.status_code == 503
    assert resp.json()["code"] == "S3_ACCESS_DENIED"
    updated = registry.get(job_id)
    assert updated is not None
    assert updated.status == JobStatus.VALIDATING
    assert updated.status != JobStatus.REJECTED


def test_validate_object_too_large_returns_rejected(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
    get_settings.cache_clear()
    try:
        create = client.post(UPLOADS_URL, json={"filename": "big.csv"}).json()
        fake_s3.head_object.return_value = S3ObjectHead(content_length=1 * 1024 * 1024 + 1)

        resp = client.post(f"/api/v1/intake/jobs/{create['job_id']}/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "REJECTED"
        assert data["validation"]["errors"][0]["code"] == "FILE_TOO_LARGE"
        fake_s3.get_object_bytes.assert_not_called()
        fake_sqs.send_message.assert_not_called()
    finally:
        get_settings.cache_clear()


def test_validate_malformed_csv_returns_rejected(
    client: TestClient,
    fake_s3: MagicMock,
    fake_sqs: MagicMock,
) -> None:
    content = b"\xff\xfe\x00id,name\n"
    create = client.post(UPLOADS_URL, json={"filename": "bad.csv"}).json()
    fake_s3.head_object.return_value = S3ObjectHead(content_length=len(content))
    fake_s3.get_object_bytes.return_value = content

    resp = client.post(f"/api/v1/intake/jobs/{create['job_id']}/validate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert data["validation"]["errors"][0]["code"] == "CSV_PARSE_ERROR"
    fake_sqs.send_message.assert_not_called()


def test_validate_s3_access_error_returns_controlled_api_error(
    client: TestClient,
    fake_s3: MagicMock,
) -> None:
    create = client.post(UPLOADS_URL, json={"filename": "denied.csv"}).json()
    fake_s3.head_object.side_effect = S3AccessDeniedError("denied")

    resp = client.post(f"/api/v1/intake/jobs/{create['job_id']}/validate")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "S3_ACCESS_DENIED"
    assert "detail" not in body


def test_validate_unknown_job_returns_not_found(client: TestClient) -> None:
    resp = client.post("/api/v1/intake/jobs/does-not-exist/validate")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "JOB_NOT_FOUND"
    assert "detail" not in body


def test_validate_s3_unavailable_returns_controlled_api_error(
    client: TestClient,
    fake_s3: MagicMock,
) -> None:
    create = client.post(UPLOADS_URL, json={"filename": "down.csv"}).json()
    fake_s3.head_object.side_effect = S3UnavailableError("down")

    resp = client.post(f"/api/v1/intake/jobs/{create['job_id']}/validate")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "S3_UNAVAILABLE"
    assert "detail" not in body


def test_runtime_api_error_body_matches_documented_contract(client: TestClient) -> None:
    resp = client.post("/api/v1/intake/jobs/missing-job/validate")
    assert resp.status_code == 404
    assert set(resp.json().keys()) == {"code", "message"}
    assert resp.json()["code"] == "JOB_NOT_FOUND"
