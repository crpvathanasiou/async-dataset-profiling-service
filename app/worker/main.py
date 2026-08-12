"""
Worker process entrypoint.

Run locally (second terminal alongside uvicorn):

    poetry run python -m app.worker.main

This module is the composition root of the consumer side: it reads
configuration, configures logging, builds the SQS adapter and the worker
service, installs signal handlers, and then hands control to the poll loop.

It is deliberately NOT a FastAPI application. There is no ASGI app, no port, and
no HTTP request driving execution. The process is started independently (own
container / own ECS task) and lives as long as it is allowed to poll:

    API process (uvicorn)   -->  SQS queue  -->  worker process (this module)

Wiring only: message semantics live in `app/messaging`, AWS calls in
`app/infrastructure/sqs.py`, and the loop itself in `app/worker/service.py`.
"""

from __future__ import annotations

import logging
import signal
import sys

from app.infrastructure.sqs import SQSQueue
from app.settings import get_settings
from app.worker.service import WorkerService


def setup_logging(log_level: str) -> None:
    """
    Configure logging to stdout/stderr, matching the API process format.

    Container-friendly by design: logs go to the standard streams and are
    collected by the runtime (Docker, later CloudWatch), so the worker writes no
    log files of its own. An unknown `LOG_LEVEL` falls back to INFO rather than
    failing startup.
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def main() -> int:
    """
    Start the worker: build collaborators, install signal handlers, run the loop.

    Output: the process exit code (0 after a clean shutdown).

    Order matters. Settings are read first, so a missing `SQS_JOB_QUEUE_URL`
    fails immediately at startup instead of being discovered on the first poll.
    Signal handlers are installed before `run()` so a termination signal that
    arrives during the very first long poll is still observed.
    """
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger("async-dataset-profiling-service.worker")

    # The worker builds its own SQS client. It shares the queue with the API
    # process through configuration, not through shared objects or memory.
    sqs = SQSQueue(
        region_name=settings.aws_region,
        queue_url=settings.sqs_job_queue_url,
    )
    worker = WorkerService(sqs=sqs, settings=settings)

    def _handle_signal(signum: int, _frame: object | None) -> None:
        # Signal handlers must stay minimal: this only flips the loop's flag.
        # Work in progress is not interrupted, and the message currently being
        # processed keeps its chance to be completed and deleted.
        logger.info("worker shutdown_signal signum=%s", signum)
        worker.request_shutdown()

    # SIGTERM is what a container runtime sends before stopping a task
    # (deployments, scaling in, task replacement); SIGINT is local Ctrl+C.
    # Handling SIGTERM gives the worker an opportunity to stop receiving new work
    # and finish in-flight processing before the container is forcibly
    # terminated. Messages that are not completed and deleted in that window
    # simply reappear after their visibility timeout.
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # The queue URL itself is not logged; only the fact that one is configured,
    # plus the polling and simulated-processing parameters in effect.
    logger.info(
        "worker starting queue_url_configured=true wait_time_seconds=%s "
        "simulated_processing_seconds=%s",
        sqs.wait_time_seconds,
        settings.worker_simulated_processing_seconds,
    )
    # Blocks until shutdown is requested; the long poll makes this cheap while idle.
    worker.run()
    logger.info("worker exited cleanly")
    return 0


if __name__ == "__main__":
    # `python -m app.worker.main` executes this branch. The exit code is
    # propagated so a supervisor (Docker/ECS) can tell a clean stop from a crash.
    sys.exit(main())
