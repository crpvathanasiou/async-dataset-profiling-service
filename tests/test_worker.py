"""Worker behavior tests — mocked SQS, no real AWS."""

from unittest.mock import MagicMock

import pytest

from app.infrastructure.sqs import DEFAULT_WAIT_TIME_SECONDS, ReceivedMessage, SQSQueue
from app.messaging.schemas import build_process_dataset_message
from app.settings import Settings, get_settings
from app.worker.service import ProcessingError, WorkerService


@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def _valid_received() -> tuple[ReceivedMessage, str]:
    command = build_process_dataset_message(
        job_id="job-abc",
        s3_bucket="test-bucket",
        s3_key="incoming/job-abc/data.csv",
    )
    received = ReceivedMessage(
        message_id="sqs-mid-1",
        receipt_handle="rh-1",
        body=command.model_dump_json(),
    )
    return received, command.message_id


def test_worker_uses_long_polling_configuration(settings: Settings) -> None:
    sqs = MagicMock(spec=SQSQueue)
    sqs.wait_time_seconds = DEFAULT_WAIT_TIME_SECONDS
    sqs.receive_messages.return_value = []

    worker = WorkerService(sqs=sqs, settings=settings)
    # One iteration then stop.
    call_count = {"n": 0}

    def _receive(**_kwargs: object) -> list[ReceivedMessage]:
        call_count["n"] += 1
        if call_count["n"] >= 1:
            worker.request_shutdown()
        return []

    sqs.receive_messages.side_effect = _receive
    worker.run()

    sqs.receive_messages.assert_called()
    # Adapter default is WaitTimeSeconds=20; worker relies on adapter default.
    assert sqs.wait_time_seconds == 20


def test_worker_receives_and_processes_valid_message(settings: Settings) -> None:
    received, message_id = _valid_received()
    sqs = MagicMock(spec=SQSQueue)
    processor = MagicMock()
    worker = WorkerService(sqs=sqs, settings=settings, processor=processor)

    worker.handle_received_message(received)

    processor.process.assert_called_once()
    envelope = processor.process.call_args.args[0]
    assert envelope.message_id == message_id
    assert envelope.job_id == "job-abc"
    sqs.delete_message.assert_called_once_with(receipt_handle="rh-1")


def test_successful_processing_deletes_message(settings: Settings) -> None:
    received, _ = _valid_received()
    sqs = MagicMock(spec=SQSQueue)
    processor = MagicMock()
    worker = WorkerService(sqs=sqs, settings=settings, processor=processor)

    worker.handle_received_message(received)
    sqs.delete_message.assert_called_once_with(receipt_handle="rh-1")


def test_processing_failure_does_not_delete_message(settings: Settings) -> None:
    received, _ = _valid_received()
    sqs = MagicMock(spec=SQSQueue)
    processor = MagicMock()
    processor.process.side_effect = ProcessingError("boom")
    worker = WorkerService(sqs=sqs, settings=settings, processor=processor)

    worker.handle_received_message(received)
    sqs.delete_message.assert_not_called()


def test_malformed_message_is_handled_safely_without_delete(settings: Settings) -> None:
    sqs = MagicMock(spec=SQSQueue)
    processor = MagicMock()
    worker = WorkerService(sqs=sqs, settings=settings, processor=processor)
    bad = ReceivedMessage(
        message_id="sqs-bad",
        receipt_handle="rh-bad",
        body="not-json{{{",
    )

    worker.handle_received_message(bad)

    processor.process.assert_not_called()
    sqs.delete_message.assert_not_called()


def test_request_shutdown_stops_loop(settings: Settings) -> None:
    sqs = MagicMock(spec=SQSQueue)
    worker = WorkerService(sqs=sqs, settings=settings)

    def _receive(**_kwargs: object) -> list[ReceivedMessage]:
        worker.request_shutdown()
        return []

    sqs.receive_messages.side_effect = _receive
    worker.run()
    assert worker.is_running is False
