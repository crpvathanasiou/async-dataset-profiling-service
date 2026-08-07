from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from app.intake.schemas import JobStatus


@dataclass
class IntakeJobRecord:
    job_id: str
    filename: str
    s3_key: str
    status: JobStatus = JobStatus.RECEIVED


class JobRegistry(Protocol):
    """Temporary job context store. Replaceable by persistent storage later."""

    def put(self, record: IntakeJobRecord) -> None: ...

    def get(self, job_id: str) -> IntakeJobRecord | None: ...

    def update_status(self, job_id: str, status: JobStatus) -> None: ...


class InMemoryJobRegistry:
    """Process-local registry for M2 (no Redis/database)."""

    def __init__(self) -> None:
        self._jobs: dict[str, IntakeJobRecord] = {}
        self._lock = threading.Lock()

    def put(self, record: IntakeJobRecord) -> None:
        with self._lock:
            self._jobs[record.job_id] = record

    def get(self, job_id: str) -> IntakeJobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update_status(self, job_id: str, status: JobStatus) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record.status = status

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


_registry = InMemoryJobRegistry()


def get_job_registry() -> InMemoryJobRegistry:
    return _registry
