from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# SQS long polling: reduce empty receives while staying responsive.
DEFAULT_WAIT_TIME_SECONDS = 20
DEFAULT_MAX_MESSAGES = 1


class SQSUnavailableError(Exception):
    """SQS could not be reached or returned an unexpected error."""


class SQSAccessDeniedError(Exception):
    """Caller is not allowed to access the configured queue."""


@dataclass(frozen=True)
class ReceivedMessage:
    """One message returned by ReceiveMessage (application-facing, not boto3)."""

    message_id: str
    receipt_handle: str
    body: str


class SQSQueue:
    """Thin adapter around boto3 SQS operations used by API publish and the worker."""

    def __init__(
        self,
        *,
        region_name: str,
        queue_url: str,
        client: Any | None = None,
        wait_time_seconds: int = DEFAULT_WAIT_TIME_SECONDS,
    ) -> None:
        self._queue_url = queue_url
        self._wait_time_seconds = wait_time_seconds
        self._client = client or boto3.client("sqs", region_name=region_name)

    @property
    def queue_url(self) -> str:
        return self._queue_url

    @property
    def wait_time_seconds(self) -> int:
        return self._wait_time_seconds

    def send_message(self, *, body: str) -> str:
        """Publish a message body to the configured queue. Returns the SQS MessageId."""
        try:
            response = self._client.send_message(
                QueueUrl=self._queue_url,
                MessageBody=body,
            )
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise SQSUnavailableError("SQS is temporarily unavailable.") from exc

        message_id = response.get("MessageId")
        if not message_id:
            raise SQSUnavailableError("SQS did not return a MessageId.")
        return str(message_id)

    def receive_messages(
        self,
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        wait_time_seconds: int | None = None,
        visibility_timeout: int | None = None,
    ) -> list[ReceivedMessage]:
        """
        Long-poll for messages.

        Defaults to WaitTimeSeconds=20 (SQS long polling).
        """
        params: dict[str, Any] = {
            "QueueUrl": self._queue_url,
            "MaxNumberOfMessages": max_messages,
            "WaitTimeSeconds": (
                self._wait_time_seconds if wait_time_seconds is None else wait_time_seconds
            ),
        }
        if visibility_timeout is not None:
            params["VisibilityTimeout"] = visibility_timeout

        try:
            response = self._client.receive_message(**params)
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise SQSUnavailableError("SQS is temporarily unavailable.") from exc

        raw_messages = response.get("Messages") or []
        result: list[ReceivedMessage] = []
        for item in raw_messages:
            result.append(
                ReceivedMessage(
                    message_id=str(item["MessageId"]),
                    receipt_handle=str(item["ReceiptHandle"]),
                    body=str(item["Body"]),
                )
            )
        return result

    def delete_message(self, *, receipt_handle: str) -> None:
        """Acknowledge successful processing by deleting the message."""
        try:
            self._client.delete_message(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
            )
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise SQSUnavailableError("SQS is temporarily unavailable.") from exc

    def change_message_visibility(self, *, receipt_handle: str, visibility_timeout: int) -> None:
        """Extend or reduce how long a received message stays invisible."""
        try:
            self._client.change_message_visibility(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=visibility_timeout,
            )
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise SQSUnavailableError("SQS is temporarily unavailable.") from exc

    def _map_client_error(self, exc: ClientError) -> Exception:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))

        if code in {"AccessDenied", "AccessDeniedException"} or status == 403:
            return SQSAccessDeniedError("Access to the SQS queue was denied.")
        return SQSUnavailableError("SQS request failed.")
