"""
Worker process entrypoint.

Run locally (second terminal alongside uvicorn):

    poetry run python -m app.worker.main
"""

from __future__ import annotations

import logging
import signal
import sys

from app.infrastructure.sqs import SQSQueue
from app.settings import get_settings
from app.worker.service import WorkerService


def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger("async-dataset-profiling-service.worker")

    sqs = SQSQueue(
        region_name=settings.aws_region,
        queue_url=settings.sqs_job_queue_url,
    )
    worker = WorkerService(sqs=sqs, settings=settings)

    def _handle_signal(signum: int, _frame: object | None) -> None:
        logger.info("worker shutdown_signal signum=%s", signum)
        worker.request_shutdown()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        "worker starting queue_url_configured=true wait_time_seconds=%s "
        "simulated_processing_seconds=%s",
        sqs.wait_time_seconds,
        settings.worker_simulated_processing_seconds,
    )
    worker.run()
    logger.info("worker exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
