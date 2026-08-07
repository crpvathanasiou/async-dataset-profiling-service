from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class S3ObjectNotFoundError(Exception):
    """Object does not exist in the configured bucket."""


class S3AccessDeniedError(Exception):
    """Caller is not allowed to access the object or bucket."""


class S3UnavailableError(Exception):
    """S3 could not be reached or returned an unexpected error."""


@dataclass(frozen=True)
class S3ObjectHead:
    content_length: int


class S3Storage:
    """Thin adapter around boto3 S3 operations used by Stage 1 intake."""

    def __init__(self, *, region_name: str, bucket: str, client: Any | None = None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client("s3", region_name=region_name)

    @property
    def bucket(self) -> str:
        return self._bucket

    def create_presigned_put_url(self, *, key: str, expires_in_seconds: int) -> str:
        try:
            return self._client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
                HttpMethod="PUT",
            )
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise S3UnavailableError("S3 is temporarily unavailable.") from exc

    def head_object(self, *, key: str) -> S3ObjectHead:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise S3UnavailableError("S3 is temporarily unavailable.") from exc

        content_length = int(response.get("ContentLength", 0))
        return S3ObjectHead(content_length=content_length)

    def get_object_bytes(self, *, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            return body.read()
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise S3UnavailableError("S3 is temporarily unavailable.") from exc

    def _map_client_error(self, exc: ClientError) -> Exception:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))

        if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
            return S3ObjectNotFoundError("S3 object was not found.")
        if code in {"403", "AccessDenied"} or status == 403:
            return S3AccessDeniedError("Access to the S3 object was denied.")
        return S3UnavailableError("S3 request failed.")
