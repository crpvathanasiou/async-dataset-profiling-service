# async-dataset-profiling-service

FastAPI service for asynchronous dataset profiling — reusable production-grade AWS async application template.

## Current milestone

**M3 — SQS handoff + worker skeleton**

Primary flow:

1. `POST /api/v1/intake/uploads` → `job_id` + presigned S3 PUT URL
2. Client uploads the CSV directly to S3
3. `POST /api/v1/intake/jobs/{job_id}/validate` → `VALIDATED` / `REJECTED`
4. On `VALIDATED`, publish one `PROCESS_DATASET` command to SQS
5. Worker process long-polls SQS, simulates processing, deletes the message on success

Legacy multipart `POST /api/v1/intake/jobs` remains for local/reference testing only (does not publish to SQS).

## Requirements

- Python 3.11
- Poetry
- Docker + Docker Compose
- AWS credentials via the standard provider chain (CLI profile / env / future task role)

## Local setup (Windows PowerShell)

```powershell
poetry config virtualenvs.in-project true
poetry install
Copy-Item .env.example .env
```

Run **two processes**:

Terminal 1 — API:

```powershell
poetry run uvicorn app.main:app --reload
```

Terminal 2 — worker:

```powershell
poetry run python -m app.worker.main
```

## Stage 1 API

### Primary (S3 + SQS handoff)

- `POST /api/v1/intake/uploads` — create upload (`{"filename":"transactions.csv"}`)
- `POST /api/v1/intake/jobs/{job_id}/validate` — validate object in S3; on success publish to SQS

### Legacy / local

- `POST /api/v1/intake/jobs` — multipart CSV upload (not the production path; no SQS publish)

OpenAPI: `/docs`

## Health

- `GET /health/live`
- `GET /health/ready`

## Configuration

See `.env.example`:

- `APP_ENV`, `APP_VERSION`, `LOG_LEVEL`
- `MAX_UPLOAD_SIZE_MB` (default `10`)
- `AWS_REGION` (default `eu-north-1`)
- `S3_INPUT_BUCKET` (**required**, no code default)
- `PRESIGNED_URL_EXPIRY_SECONDS` (default `900`)
- `SQS_JOB_QUEUE_URL` (**required**, no code default)
- `WORKER_SIMULATED_PROCESSING_SECONDS` (default `5`)

Do not put AWS access keys in the repository. Use the AWS credential provider chain.

## Quality checks

```powershell
poetry run ruff check .
poetry run pyright
poetry run pytest
```

## Docker

```powershell
Copy-Item .env.example .env
docker build .
docker compose up --build
```

Compose runs the API and a separate worker process against the same image.

## Architecture notes (M3)

- FastAPI stays thin: routers → services → infrastructure adapters (`s3.py`, `sqs.py`)
- Versioned message envelope: `app/messaging/schemas.py`
- Worker deletes SQS messages only after successful processing (retries / DLQ on failure)
- No transactional outbox yet (no persistent DB) — deliberate M3 technical debt
- Standard SQS is at-least-once; durable idempotency requires shared job state later

## Future direction (context only)

```text
Stage 1: Intake + Validation + SQS handoff  (M1–M3)
    → durable job state / outbox / Stage 2 results  (not implemented)
```
