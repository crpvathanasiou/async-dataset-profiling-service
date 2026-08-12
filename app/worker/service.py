"""
SQS worker loop: long-poll → validate envelope → simulate process → delete on success.

M3 idempotency skeleton:
  Processing is structured around message_id / job_id so a later milestone can add a
  durable "already processed" check against shared job state.
  Standard SQS is at-least-once; M3 does NOT provide exactly-once execution.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from pydantic import ValidationError

from app.infrastructure.sqs import ReceivedMessage, SQSQueue, SQSUnavailableError
from app.messaging.schemas import JobCommandMessage, MessageType
from app.settings import Settings

logger = logging.getLogger("async-dataset-profiling-service.worker")


class ProcessingError(Exception):
    """Raised when job processing fails; message must NOT be deleted."""


class JobProcessor(Protocol):
    def process(self, message: JobCommandMessage) -> None: ...


class SimulatedJobProcessor:
    """M3 placeholder: sleep to make async handoff visible. No real profiling."""

    def __init__(self, *, sleep_seconds: int) -> None:
        self._sleep_seconds = sleep_seconds

    def process(self, message: JobCommandMessage) -> None:
        # Hook point for future durable idempotency keyed by job_id / message_id.
        _ = message.message_id, message.job_id
        time.sleep(self._sleep_seconds)


class WorkerService:
    """Long-running poll loop with graceful shutdown."""

    def __init__(
        self,
        *,
        sqs: SQSQueue,
        settings: Settings,
        processor: JobProcessor | None = None,
    ) -> None:
        self._sqs = sqs
        self._settings = settings
        self._processor = processor or SimulatedJobProcessor(
            sleep_seconds=settings.worker_simulated_processing_seconds,
        )
        self._running = True

    def request_shutdown(self) -> None:
        """Stop accepting new messages after the current receive/process cycle."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def run(self) -> None:
        while self._running:
            try:
                messages = self._sqs.receive_messages()
            except SQSUnavailableError:
                logger.exception("worker receive_failed")
                # Brief pause avoids a tight error loop if SQS is down.
                time.sleep(1)
                continue

            if not messages:
                continue

            for received in messages:
                if not self._running:
                    # Leave remaining messages invisible until visibility timeout;
                    # they become available for another worker/process.
                    break
                self.handle_received_message(received)

    def handle_received_message(self, received: ReceivedMessage) -> None:
        """
        Validate envelope, process, delete only on success.

        Failures and malformed bodies do not delete — visibility timeout enables
        retry and eventual DLQ redrive.
        """
        try:
            envelope = self._parse_envelope(received.body)
        except ValidationError:
            logger.exception(
                "worker malformed_message sqs_message_id=%s",
                received.message_id,
            )
            return

        message_id = envelope.message_id
        job_id = envelope.job_id
        message_type = envelope.message_type.value

        logger.info(
            "worker processing_start message_id=%s job_id=%s message_type=%s "
            "sqs_message_id=%s",
            message_id,
            job_id,
            message_type,
            received.message_id,
        )

        try:
            if envelope.message_type != MessageType.PROCESS_DATASET:
                raise ProcessingError(f"Unsupported message_type={message_type}")
            self._processor.process(envelope)
        except Exception:
            logger.exception(
                "worker processing_failure message_id=%s job_id=%s message_type=%s",
                message_id,
                job_id,
                message_type,
            )
            return

        try:
            self._sqs.delete_message(receipt_handle=received.receipt_handle)
        except SQSUnavailableError:
            logger.exception(
                "worker delete_failed message_id=%s job_id=%s message_type=%s",
                message_id,
                job_id,
                message_type,
            )
            return

        logger.info(
            "worker processing_success message_id=%s job_id=%s message_type=%s",
            message_id,
            job_id,
            message_type,
        )
        logger.info(
            "worker message_delete message_id=%s job_id=%s message_type=%s",
            message_id,
            job_id,
            message_type,
        )

    def _parse_envelope(self, body: str) -> JobCommandMessage:
        return JobCommandMessage.model_validate_json(body)
