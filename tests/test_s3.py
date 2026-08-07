from urllib.parse import urlparse

import pytest

from app.infrastructure.s3 import S3Storage


def test_presigned_put_url_uses_regional_virtual_hosted_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Presigned URLs must target the regional S3 endpoint (avoid legacy redirects)."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")

    region = "eu-north-1"
    bucket = "example-intake-bucket"
    storage = S3Storage(region_name=region, bucket=bucket)

    url = storage.create_presigned_put_url(
        key="incoming/job-id/example.csv",
        expires_in_seconds=900,
    )

    host = urlparse(url).netloc
    assert host == f"{bucket}.s3.{region}.amazonaws.com"
    # Explicitly reject the legacy global virtual-hosted host that triggers 307s.
    assert host != f"{bucket}.s3.amazonaws.com"


def test_s3_storage_client_uses_sigv4_and_virtual_addressing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")

    storage = S3Storage(region_name="eu-north-1", bucket="example-intake-bucket")
    assert storage._client.meta.config.signature_version == "s3v4"
    assert storage._client.meta.config.s3.get("addressing_style") == "virtual"
