from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings

INTAKE_URL = "/api/v1/intake/jobs"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _upload(client: TestClient, filename: str | None, content: bytes):
    files = {
        "file": (filename, BytesIO(content), "text/csv"),
    }
    return client.post(INTAKE_URL, files=files)


def test_valid_csv_returns_validated(client: TestClient) -> None:
    content = b"id,name\n1,alice\n2,bob\n"
    resp = _upload(client, "transactions.csv", content)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "VALIDATED"
    assert data["validation"]["passed"] is True
    assert data["validation"]["errors"] == []
    assert data["file"]["filename"] == "transactions.csv"
    assert data["file"]["size_bytes"] == len(content)
    assert data["file"]["row_count"] == 2
    assert data["file"]["column_count"] == 2
    assert data["job_id"]


def test_empty_file_returns_rejected(client: TestClient) -> None:
    resp = _upload(client, "empty.csv", b"")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert data["validation"]["passed"] is False
    assert data["validation"]["errors"][0]["code"] == "FILE_EMPTY"
    assert data["file"]["row_count"] is None
    assert data["file"]["column_count"] is None


def test_non_csv_extension_returns_rejected(client: TestClient) -> None:
    resp = _upload(client, "data.txt", b"id,name\n1,alice\n")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert data["validation"]["errors"][0]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_header_only_returns_rejected(client: TestClient) -> None:
    resp = _upload(client, "header_only.csv", b"id,name\n")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    codes = [error["code"] for error in data["validation"]["errors"]]
    assert "CSV_NO_DATA_ROWS" in codes


def test_blank_column_name_returns_rejected(client: TestClient) -> None:
    resp = _upload(client, "blank_col.csv", b"id,,name\n1,x,alice\n")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    codes = [error["code"] for error in data["validation"]["errors"]]
    assert "CSV_BLANK_COLUMN_NAME" in codes


def test_duplicate_columns_returns_rejected(client: TestClient) -> None:
    resp = _upload(client, "dupes.csv", b"id,name,id\n1,alice,1\n")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    codes = [error["code"] for error in data["validation"]["errors"]]
    assert "CSV_DUPLICATE_COLUMNS" in codes


def test_malformed_csv_returns_rejected(client: TestClient) -> None:
    # Invalid UTF-8 payload cannot be decoded as CSV text.
    resp = _upload(client, "bad.csv", b"\xff\xfe\x00id,name\n")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert data["validation"]["errors"][0]["code"] == "CSV_PARSE_ERROR"


def test_file_exceeding_configured_size_returns_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
    get_settings.cache_clear()
    try:
        # Just over 1 MiB.
        oversized = b"a" * (1 * 1024 * 1024 + 1)
        resp = _upload(client, "too_large.csv", oversized)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "REJECTED"
        assert data["validation"]["errors"][0]["code"] == "FILE_TOO_LARGE"
    finally:
        get_settings.cache_clear()
