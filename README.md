# async-dataset-profiling-service

FastAPI service for asynchronous dataset profiling.

## Current milestone

**M2 — S3-backed Stage 1 intake with presigned upload**

Primary flow:

1. `POST /api/v1/intake/uploads` → `job_id` + presigned S3 PUT URL
2. Client uploads the CSV directly to S3
3. `POST /api/v1/intake/jobs/{job_id}/validate` → `VALIDATED` / `REJECTED`

Legacy multipart `POST /api/v1/intake/jobs` remains for local/reference testing only.

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
poetry run uvicorn app.main:app --reload
```

## Stage 1 API

### Primary (S3)

- `POST /api/v1/intake/uploads` — create upload (`{"filename":"transactions.csv"}`)
- `POST /api/v1/intake/jobs/{job_id}/validate` — validate object in S3

### Legacy / local

- `POST /api/v1/intake/jobs` — multipart CSV upload (not the production path)

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

## Future direction (context only)

```text
Stage 1: Intake + Validation  (M1 local + M2 S3)
    → asynchronous handoff
Stage 2: Processing + Results  (not implemented)
```
