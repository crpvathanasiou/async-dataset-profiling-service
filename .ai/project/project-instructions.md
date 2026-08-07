# Project Instructions

## Project

`async-dataset-profiling-service`

## Current milestone

**M2 — S3-backed Stage 1 intake with presigned upload**

Primary path:

- `POST /api/v1/intake/uploads`
- client PUT to S3
- `POST /api/v1/intake/jobs/{job_id}/validate`

Stage 1 states: `RECEIVED`, `VALIDATING`, `VALIDATED`, `REJECTED`.

Do not implement Stage 2, SQS, workers, or database persistence unless explicitly requested.

## Technology stack (current)

- Python 3.11, FastAPI, Pydantic v2
- boto3 for S3 (credential provider chain)
- stdlib `csv` for structural validation
- Poetry, Ruff, Pyright, Pytest, Docker

## Constraints

- Bucket/region/expiry from settings/env only
- Object keys generated server-side
- No AWS credentials in responses or source
- Keep boto3 inside `app/infrastructure/s3.py`
