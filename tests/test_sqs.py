"""SQS adapter unit tests — mocked boto3 client, no real AWS."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from app.infrastructure.sqs import (
    DEFAULT_WAIT_TIME_SECONDS,
    SQSAccessDeniedError,
    SQSQueue,
    SQSUnavailableError,
)


def _client_error(*, code: str, status: int) -> ClientError:
    return ClientError(
        error_response={
            "Error": {"Code": code, "Message": "x"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation_name="SendMessage",
    )


def test_send_message_returns_sqs_message_id() -> None:
    client = MagicMock()
    client.send_message.return_value = {"MessageId": "mid-1"}
    queue = SQSQueue(
        region_name="eu-north-1",
        queue_url="https://sqs.example/queue",
        client=client,
    )

    mid = queue.send_message(body='{"ok":true}')
    assert mid == "mid-1"
    client.send_message.assert_called_once_with(
        QueueUrl="https://sqs.example/queue",
        MessageBody='{"ok":true}',
    )


def test_send_message_maps_access_denied() -> None:
    client = MagicMock()
    client.send_message.side_effect = _client_error(code="AccessDenied", status=403)
    queue = SQSQueue(
        region_name="eu-north-1",
        queue_url="https://sqs.example/queue",
        client=client,
    )
    with pytest.raises(SQSAccessDeniedError):
        queue.send_message(body="{}")


def test_send_message_maps_unavailable() -> None:
    client = MagicMock()
    client.send_message.side_effect = _client_error(code="ServiceUnavailable", status=503)
    queue = SQSQueue(
        region_name="eu-north-1",
        queue_url="https://sqs.example/queue",
        client=client,
    )
    with pytest.raises(SQSUnavailableError):
        queue.send_message(body="{}")


def test_receive_messages_uses_long_polling_default() -> None:
    client = MagicMock()
    client.receive_message.return_value = {
        "Messages": [
            {
                "MessageId": "sqs-1",
                "ReceiptHandle": "rh-1",
                "Body": '{"x":1}',
            }
        ]
    }
    queue = SQSQueue(
        region_name="eu-north-1",
        queue_url="https://sqs.example/queue",
        client=client,
    )

    messages = queue.receive_messages()
    assert len(messages) == 1
    assert messages[0].message_id == "sqs-1"
    assert messages[0].receipt_handle == "rh-1"
    assert messages[0].body == '{"x":1}'
    assert queue.wait_time_seconds == DEFAULT_WAIT_TIME_SECONDS
    assert DEFAULT_WAIT_TIME_SECONDS == 20
    client.receive_message.assert_called_once_with(
        QueueUrl="https://sqs.example/queue",
        MaxNumberOfMessages=1,
        WaitTimeSeconds=20,
    )


def test_receive_messages_empty_list_when_no_messages() -> None:
    client = MagicMock()
    client.receive_message.return_value = {}
    queue = SQSQueue(
        region_name="eu-north-1",
        queue_url="https://sqs.example/queue",
        client=client,
    )
    assert queue.receive_messages() == []


def test_delete_message_calls_boto() -> None:
    client = MagicMock()
    queue = SQSQueue(
        region_name="eu-north-1",
        queue_url="https://sqs.example/queue",
        client=client,
    )
    queue.delete_message(receipt_handle="rh-9")
    client.delete_message.assert_called_once_with(
        QueueUrl="https://sqs.example/queue",
        ReceiptHandle="rh-9",
    )


def test_change_message_visibility_calls_boto() -> None:
    client = MagicMock()
    queue = SQSQueue(
        region_name="eu-north-1",
        queue_url="https://sqs.example/queue",
        client=client,
    )
    queue.change_message_visibility(receipt_handle="rh-9", visibility_timeout=30)
    client.change_message_visibility.assert_called_once_with(
        QueueUrl="https://sqs.example/queue",
        ReceiptHandle="rh-9",
        VisibilityTimeout=30,
    )


def test_botocore_error_maps_to_unavailable() -> None:
    client = MagicMock()
    client.send_message.side_effect = BotoCoreError()
    queue = SQSQueue(
        region_name="eu-north-1",
        queue_url="https://sqs.example/queue",
        client=client,
    )
    with pytest.raises(SQSUnavailableError):
        queue.send_message(body="{}")
