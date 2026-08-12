"""
S3 infrastructure adapter.

This module isolates boto3/S3-specific operations from the intake service.

Typical flow:
    intake service -> S3Storage -> boto3 -> AWS S3

S3 is the durable input storage of the system. The dataset bytes never travel
through the API as a stored artifact: the client uploads directly to S3 using a
presigned PUT URL, and the API later reads the object back from S3 to validate
it. That keeps large uploads off the application process and makes S3, not
local disk or memory, the source of truth for input data.

Division of responsibility:
- the intake service decides WHICH object key a job uses, WHEN a presigned URL
  is issued, and WHAT counts as a valid CSV
- this module only knows HOW to call S3 and how to express S3 failures as
  application-level exceptions

Validation rules, job state, and key naming policy do not belong here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class S3ObjectNotFoundError(Exception):
    """
    The object does not exist in the configured bucket.

    Expected in normal operation: the client may call the validate endpoint
    before (or without ever) completing the presigned upload.
    """


class S3AccessDeniedError(Exception):
    """
    The caller's AWS identity may not access the object or bucket.

    Kept distinct from `S3UnavailableError` because the cause is usually an IAM
    policy or bucket configuration issue rather than a transient outage.
    """


class S3UnavailableError(Exception):
    """S3 could not be reached, or returned an error we do not handle specially."""


@dataclass(frozen=True)
class S3ObjectHead:
    """
    The subset of HeadObject metadata the intake service actually uses.

    Returning a small dataclass instead of the raw boto3 response keeps the
    service independent of the AWS response shape.
    """

    content_length: int


# signature_version="s3v4" plus virtual addressing produces presigned URLs
# against the regional endpoint (bucket.s3.<region>.amazonaws.com). Using the
# configured regional virtual-hosted endpoint avoids redirect and signing issues.
# Requests sent to an unsuitable S3 endpoint can receive a temporary redirect;
# this occurred during the eu-north-1 presigned PUT test for this project, which
# is why the addressing style is pinned here.
_S3_CLIENT_CONFIG = Config(
    signature_version="s3v4",
    s3={"addressing_style": "virtual"},
)


class S3Storage:
    """
    Thin adapter around the boto3 S3 operations used by Stage 1 intake.

    One instance is bound to one bucket, so callers pass object keys only and
    the bucket identity comes from configuration (`S3_INPUT_BUCKET`).

    The `client` parameter lets tests inject a stub. When omitted, a real boto3
    client is created with an explicit region (required for correct SigV4
    signing) and the standard AWS credential provider chain, so no credentials
    are ever embedded in the application.
    """

    def __init__(self, *, region_name: str, bucket: str, client: Any | None = None) -> None:
        self._bucket = bucket
        # boto3 stays private to this adapter; upper layers use the methods below.
        self._client = client or boto3.client(
            "s3",
            region_name=region_name,
            config=_S3_CLIENT_CONFIG,
        )

    @property
    def bucket(self) -> str:
        """Bucket this adapter is bound to; also published in the SQS message payload."""
        return self._bucket

    def create_presigned_put_url(self, *, key: str, expires_in_seconds: int) -> str:
        """
        Create a short-lived URL that lets the client PUT one object directly to S3.

        Input: the server-generated object `key` and the URL lifetime.
        Output: an HTTPS URL that already carries a signature.

        Why this pattern: the upload bypasses the API process entirely, so
        request size and duration do not consume application resources. The URL
        is scoped to a single bucket/key and expires, and it carries a signature
        rather than AWS credentials — but it is still a capability, so it must
        not be logged.

        Note that generating the URL is a local signing operation; no S3 call is
        made here, and the returned URL does not prove the object exists.
        """
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
        """
        Fetch object metadata (S3 HeadObject) without transferring the body.

        Used first in the validation sequence: it answers "does the upload
        exist, and how big is it?" cheaply, so an oversized or empty object can
        be rejected before any bytes are downloaded.

        Raises `S3ObjectNotFoundError` when the client never completed the
        upload.
        """
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise S3UnavailableError("S3 is temporarily unavailable.") from exc

        content_length = int(response.get("ContentLength", 0))
        return S3ObjectHead(content_length=content_length)

    def get_object_bytes(self, *, key: str) -> bytes:
        """
        Download the whole object body (S3 GetObject) into memory.

        Called only after `head_object` confirmed the object exists and its size
        is within the configured limit.

        Known technical debt: `body.read()` loads the entire object into process
        memory. That is acceptable for the small CSVs this milestone accepts and
        for the size limit enforced before this call, but a larger workload would
        need streaming or chunked processing instead.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            return body.read()
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise S3UnavailableError("S3 is temporarily unavailable.") from exc

    def _map_client_error(self, exc: ClientError) -> Exception:
        """
        Translate a boto3 `ClientError` into this module's exception types.

        Both the error code and the HTTP status are inspected because S3 reports
        a missing object differently depending on the operation: GetObject
        returns `NoSuchKey`, while HeadObject has no body and surfaces as `404`.
        Anything unrecognized becomes `S3UnavailableError`, i.e. treated as a
        transient infrastructure fault rather than a client mistake.
        """
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))

        if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
            return S3ObjectNotFoundError("S3 object was not found.")
        if code in {"403", "AccessDenied"} or status == 403:
            return S3AccessDeniedError("Access to the S3 object was denied.")
        return S3UnavailableError("S3 request failed.")
