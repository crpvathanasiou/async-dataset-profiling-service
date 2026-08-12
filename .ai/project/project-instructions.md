# Project Instructions

## Project

`async-dataset-profiling-service`

## Current milestone

**M3 — SQS handoff + worker skeleton**

Primary path:

- `POST /api/v1/intake/uploads`
- client PUT to S3
- `POST /api/v1/intake/jobs/{job_id}/validate`
- on `VALIDATED`, publish `PROCESS_DATASET` to SQS
- worker: `python -m app.worker.main`

Stage 1 states: `RECEIVED`, `VALIDATING`, `VALIDATED`, `REJECTED`.

Do not implement Stage 2 API, database persistence, Redis, CI/CD, or real profiling unless explicitly requested.

## Technology stack (current)

- Python 3.11, FastAPI, Pydantic v2
- boto3 for S3 and SQS (credential provider chain)
- stdlib `csv` for structural validation
- Separate worker process (not FastAPI)
- Poetry, Ruff, Pyright, Pytest, Docker

## Constraints

- Bucket/region/queue URL/expiry from settings/env only
- Object keys generated server-side
- No AWS credentials in responses or source
- Keep boto3 inside `app/infrastructure/` (`s3.py`, `sqs.py`)
