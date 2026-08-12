"""
SQS worker loop: long-poll → validate envelope → simulate process → delete on success.

This module is the orchestration layer of the consumer process, mirroring what
`app/intake/service.py` is for the API process. It decides the order of
operations and when a message may be acknowledged; it performs no boto3 calls of
its own and knows nothing about HTTP.

Position in the architecture:

    SQS queue
       |  ReceiveMessage (long poll, via app/infrastructure/sqs.py)
       v
    WorkerService.run
       |
       v
    envelope validation (app/messaging/schemas.py)
       |
       v
    JobProcessor.process
       |
       v
    DeleteMessage  <-- only after processing succeeded

Why the order can never be inverted: DeleteMessage is the acknowledgement that
the work is done. Deleting before processing would mean a crash mid-processing
destroys the only record that the work was requested, and the job would silently
never complete. Deleting after success means a failure simply leaves the message
in the queue; it becomes visible again once the visibility timeout expires, gets
retried, and after the configured number of failed receives SQS itself moves it
to the dead-letter queue. That redrive is queue configuration in AWS — there is
no DLQ logic in this file.

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

# Distinct logger name from the intake logger, so publisher and consumer lines
# can be told apart in aggregated logs even though both write to stdout.
logger = logging.getLogger("async-dataset-profiling-service.worker")


class ProcessingError(Exception):
    """
    Raised when job processing fails; message must NOT be deleted.

    Signals "this attempt failed, let SQS redeliver it" rather than "this message
    is invalid". The loop catches it, logs, and returns without acknowledging.
    """


class JobProcessor(Protocol):
    """
    The contract between the worker loop and whatever performs the actual work.

    A Protocol keeps the two concerns independent: receiving, validating,
    acknowledging, and shutdown behavior stay stable while the processing
    implementation is replaced (simulated now, real Stage 2 work later). Tests
    inject their own processor for the same reason.

    Convention: returning normally means success and permits deletion; raising
    means failure and prevents it.
    """

    def process(self, message: JobCommandMessage) -> None: ...


class SimulatedJobProcessor:
    """
    M3 placeholder: sleep to make async handoff visible. No real profiling.

    The sleep is a stand-in for the future Stage 2 pipeline — read the dataset
    from S3, profile it, produce results, persist durable state — none of which
    is implemented here. Its only purpose is to make the asynchronous nature of
    the system observable: the HTTP request has long returned while this is still
    running.

    Duration comes from `WORKER_SIMULATED_PROCESSING_SECONDS` so the delay can be
    tuned per environment without code changes.
    """

    def __init__(self, *, sleep_seconds: int) -> None:
        self._sleep_seconds = sleep_seconds

    def process(self, message: JobCommandMessage) -> None:
        # Hook point for future durable idempotency keyed by job_id / message_id.
        _ = message.message_id, message.job_id
        time.sleep(self._sleep_seconds)


class WorkerService:
    """
    Long-running poll loop with graceful shutdown.

    Collaborators are injected: the SQS adapter performs the AWS calls, and the
    processor performs the work. That makes the whole loop testable without AWS
    and without waiting for real sleeps.

    `processor` defaults to `SimulatedJobProcessor` so the production entrypoint
    stays a plain construction, while tests and future milestones can substitute
    a real implementation.
    """

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
        """
        Stop accepting new messages after the current receive/process cycle.

        Called from the signal handler in `app/worker/main.py`. It only flips a
        flag: nothing is cancelled, so work already in progress can run to
        completion and still be acknowledged. During a container stop (deployment,
        scale-in, task replacement) that gives in-flight processing an opportunity
        to finish before the runtime forcibly terminates the process; a message
        not completed and deleted in that window reappears after its visibility
        timeout.
        """
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def run(self) -> None:
        """
        Poll the queue until shutdown is requested.

        The worker pulls work; SQS never invokes this process. Each iteration
        long-polls (WaitTimeSeconds=20 by default in the adapter), so an idle
        worker makes few API calls instead of spinning.

        Returns after `request_shutdown()` has been observed. Processor failures do
        not end the loop, because `handle_received_message` handles them per
        message; an unhandled infrastructure error raised here (for example
        `SQSAccessDeniedError` from receive) would propagate and stop the worker.
        """
        while self._running:
            try:
                messages = self._sqs.receive_messages()
            except SQSUnavailableError:
                logger.exception("worker receive_failed")
                # Brief pause avoids a tight error loop if SQS is down.
                time.sleep(1)
                continue

            if not messages:
                # Normal outcome of an expired long poll: no work right now.
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

        Input: one `ReceivedMessage` from the adapter. No return value; the
        outcome is expressed as a side effect (deleted or left in the queue) plus
        log lines carrying `message_id`, `job_id`, and `message_type`.

        Malformed-message and processor failure paths are handled without
        terminating the worker loop; infrastructure or configuration failures that
        are not explicitly handled here (for example `SQSAccessDeniedError` from
        the delete call) may still terminate the worker. Note the consequence for
        a malformed body: it is not deleted, so it will be redelivered until SQS
        redrives it to the DLQ, which is where such messages are meant to be
        inspected.
        """
        try:
            envelope = self._parse_envelope(received.body)
        except ValidationError:
            # Contract violation (unparseable, unknown type, wrong version). It is
            # not retried into the processor, and it is deliberately not deleted.
            logger.exception(
                "worker malformed_message sqs_message_id=%s",
                received.message_id,
            )
            return

        # These identifiers are the correlation keys shared with the publisher's
        # logs; `received.message_id` is the separate identifier SQS assigned.
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
            # A structurally valid envelope can still request work this worker
            # does not implement, which is treated as a processing failure.
            if envelope.message_type != MessageType.PROCESS_DATASET:
                raise ProcessingError(f"Unsupported message_type={message_type}")
            self._processor.process(envelope)
        except Exception:
            # Broad on purpose: any processor failure must leave the message in
            # the queue for redelivery rather than crash the worker.
            logger.exception(
                "worker processing_failure message_id=%s job_id=%s message_type=%s",
                message_id,
                job_id,
                message_type,
            )
            return

        # Processing succeeded, so the message may now be acknowledged.
        try:
            self._sqs.delete_message(receipt_handle=received.receipt_handle)
        except SQSUnavailableError:
            # The work was done but the acknowledgement failed, so SQS will
            # redeliver the message: a duplicate execution is possible here. This
            # is the at-least-once reality that future processors must handle
            # idempotently; nothing in M3 prevents it.
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
        """
        Validate a raw SQS body against the shared message contract.

        The consumer never trusts the queue: the publisher may run a different
        code version, and the body could be malformed or hand-crafted. Validation
        happens here, before any processing, and raises `ValidationError` on any
        contract violation.
        """
        return JobCommandMessage.model_validate_json(body)
