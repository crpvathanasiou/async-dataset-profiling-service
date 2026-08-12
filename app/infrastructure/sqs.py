"""
SQS infrastructure adapter.

This module isolates boto3/SQS-specific operations from the application and
worker orchestration layers. It is used by both processes that make up the
system:

    publish side (API process):
        intake service -> SQSQueue.send_message -> boto3 -> AWS SQS

    consume side (worker process):
        worker service -> SQSQueue.receive_messages -> boto3 -> AWS SQS
                       -> SQSQueue.delete_message   -> boto3 -> AWS SQS

The queue is the asynchronous boundary of the whole system. Everything before
`send_message` happens inside the client's HTTP request; everything after
`receive_messages` happens later, in a different process, with no HTTP request
attached to it.

Division of responsibility:
- the service layer decides WHEN a message should be published, and the worker
  decides WHEN a message may be deleted
- this module only knows HOW to perform those AWS API calls and how to report
  failure in application terms (`SQSUnavailableError` / `SQSAccessDeniedError`)

Message-body construction, envelope validation, job orchestration, and retry
policy decisions do not belong here.

SQS delivery semantics that callers must keep in mind (they shape the code in
`app/worker/service.py` and `app/intake/service.py`):

1. Long polling. AWS SQS never calls into our process; the worker asks for
   messages. `WaitTimeSeconds=20` lets a single ReceiveMessage call wait for up
   to 20 seconds for a message to arrive, instead of returning empty
   immediately and being called again in a tight loop.
2. Visibility timeout. ReceiveMessage does NOT remove the message from the
   queue; it hides it from other consumers for the queue's visibility timeout.
   If nobody deletes it within that window, the message becomes visible again
   and will be delivered again.
3. DeleteMessage is the acknowledgement of successful processing, which is why
   the required order is receive -> process -> success -> DeleteMessage.
4. Redrive/DLQ is queue configuration in AWS, not Python logic: after the
   configured number of failed receives, SQS itself moves the message to the
   configured dead-letter queue.
5. Standard queues are at-least-once. The same message can be delivered more
   than once, so real processors must eventually be idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# SQS long polling: reduce empty receives while staying responsive.
DEFAULT_WAIT_TIME_SECONDS = 20
DEFAULT_MAX_MESSAGES = 1


class SQSUnavailableError(Exception):
    """
    SQS could not be reached, or returned an error we do not handle specially.

    Callers treat this as a retryable infrastructure failure: the API turns it
    into a controlled 503, and the worker leaves the message in the queue.
    """


class SQSAccessDeniedError(Exception):
    """
    The caller's AWS identity is not allowed to use the configured queue.

    Usually an IAM policy or wrong queue URL problem rather than an outage, so
    it is kept separate from `SQSUnavailableError` for clearer logs and error
    codes.
    """


@dataclass(frozen=True)
class ReceivedMessage:
    """
    One message returned by ReceiveMessage, expressed in application terms.

    Deliberately not a raw boto3 dict, so worker code never depends on the
    shape of the AWS response.

    Fields:
    - `message_id`: the identifier SQS assigned when it accepted the message.
      This is NOT our application `message_id` inside the envelope body; see
      `app/messaging/schemas.py` for the distinction.
    - `receipt_handle`: token identifying THIS delivery of the message. It is
      required to delete the message or change its visibility, and a new one is
      issued on every receive.
    - `body`: the raw serialized envelope. Parsing/validating it is the
      worker's job, not the adapter's.
    """

    message_id: str
    receipt_handle: str
    body: str


class SQSQueue:
    """
    Thin adapter around the boto3 SQS operations used by publish and consume.

    One instance is bound to one queue URL, so callers never pass queue URLs
    around; the queue identity comes from configuration (`SQS_JOB_QUEUE_URL`)
    and is injected once at construction.

    The `client` parameter exists so tests can inject a stub instead of
    reaching AWS. When it is omitted, a real boto3 client is created with an
    explicit region and the standard AWS credential provider chain (environment
    variables, shared profile, or an ECS/EC2 task role in deployment), which is
    why no credentials appear in this codebase.
    """

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
        # The boto3 client stays private to this adapter: application code
        # depends on the methods below, never on the SDK surface.
        self._client = client or boto3.client("sqs", region_name=region_name)

    @property
    def queue_url(self) -> str:
        return self._queue_url

    @property
    def wait_time_seconds(self) -> int:
        """Long-poll duration this instance uses when the caller does not override it."""
        return self._wait_time_seconds

    def send_message(self, *, body: str) -> str:
        """
        Publish an already-serialized message body to the configured queue.

        Input: `body`, the serialized envelope built by the caller (the intake
        service serializes `JobCommandMessage`). This adapter intentionally
        accepts a string so it stays independent of the message contract.

        Output: the AWS `MessageId`. A successful SendMessage call returning it
        indicates that SQS accepted the message and assigned it an AWS message
        identifier, which is what callers treat as the asynchronous handoff having
        happened. It is a different identifier from the envelope's own
        `message_id`.

        Raises `SQSAccessDeniedError` / `SQSUnavailableError` instead of boto3
        exceptions, so the calling service can map failures to its own error
        codes without importing botocore.
        """
        try:
            response = self._client.send_message(
                QueueUrl=self._queue_url,
                MessageBody=body,
            )
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            # BotoCoreError covers client-side/transport problems (endpoint
            # resolution, connection failures) that never reached the service.
            raise SQSUnavailableError("SQS is temporarily unavailable.") from exc

        message_id = response.get("MessageId")
        if not message_id:
            # Without a MessageId we cannot confirm that SQS accepted the message
            # and assigned it an AWS message identifier, so this is reported as a
            # failure rather than silently returning an empty identifier.
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
        Long-poll the queue for work and return messages in application form.

        Defaults to `WaitTimeSeconds=20` (SQS long polling) so an idle worker
        makes roughly one API call per 20 seconds instead of spinning.

        Receiving does not delete anything: each returned message is only
        hidden from other consumers for the visibility timeout. `visibility_timeout`
        overrides the queue default for this receive; when omitted, the value
        configured on the queue in AWS applies.

        Output: a possibly empty list. An empty list is the normal "no work
        available right now" outcome, not an error.
        """
        params: dict[str, Any] = {
            "QueueUrl": self._queue_url,
            "MaxNumberOfMessages": max_messages,
            "WaitTimeSeconds": (
                self._wait_time_seconds if wait_time_seconds is None else wait_time_seconds
            ),
        }
        if visibility_timeout is not None:
            # Only sent when explicitly requested, so the queue-level default
            # configured in AWS stays authoritative by default.
            params["VisibilityTimeout"] = visibility_timeout

        try:
            response = self._client.receive_message(**params)
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise SQSUnavailableError("SQS is temporarily unavailable.") from exc

        # SQS omits "Messages" entirely when the long poll expires with no work.
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
        """
        Acknowledge successful processing by removing the message from the queue.

        Input: the `receipt_handle` from the delivery that was processed.

        Callers must only reach this after processing succeeded. Deleting first
        and processing afterwards would destroy the retry guarantee: a crash
        mid-processing would leave no trace of the work in the queue, and the
        job would silently never be completed.
        """
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
        """
        Extend or shorten how long a received message stays invisible.

        Provided so consumers can either keep a long-running job hidden while
        still working on it (extend), or release work early for faster retry
        (shorten). The current worker does not call it; it exists so the adapter
        covers the SQS operations this pattern normally needs.
        """
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
        """
        Translate a boto3 `ClientError` into this module's exception types.

        Keeping the mapping in one place means every public method reports
        failure consistently, and AWS error strings stay out of logs/responses
        produced by upper layers. Anything not recognizably a permissions
        problem is reported as unavailable, i.e. worth retrying.
        """
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))

        if code in {"AccessDenied", "AccessDeniedException"} or status == 403:
            return SQSAccessDeniedError("Access to the SQS queue was denied.")
        return SQSUnavailableError("SQS request failed.")
