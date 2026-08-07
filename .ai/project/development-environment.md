# Development Environment

## Purpose

Defines the current development and runtime foundation for `async-dataset-profiling-service`.

## Existing foundation

- Docker-based runtime
- FastAPI application
- Poetry for dependency management
- Ruff, Pyright, Pytest quality gates

Extend this foundation. Do not replace it unless explicitly instructed.

## Runtime stack

- Python 3.11
- FastAPI
- Uvicorn (1 worker in production containers)
- Pydantic v2 / pydantic-settings
- Poetry
- Docker / Docker Compose

No Redis, database, queue, or cloud SDK dependencies in the current baseline.

## Local development

```powershell
poetry config virtualenvs.in-project true
poetry install
poetry run uvicorn app.main:app --reload
```

Helpers:

```powershell
.\scripts\dev.ps1
.\scripts\lint.ps1
.\scripts\typecheck.ps1
.\scripts\test.ps1
```

## Quality checks

```powershell
poetry run ruff check .
poetry run pyright
poetry run pytest
```

## Docker

Compose expects a local `.env` file (gitignored). Create it from the example first:

```powershell
Copy-Item .env.example .env
docker build .
docker compose up --build
docker compose config
```

Container model:

- 1 container / ECS task = 1 application process
- Dockerfile uses `--workers 1`
- Horizontal scaling is a later concern at the service/task level

## Environment variables

See `.env.example`:

- `APP_ENV`
- `APP_VERSION`
- `LOG_LEVEL`
- `MAX_UPLOAD_SIZE_MB` (default `10`)
- `AWS_REGION`
- `S3_INPUT_BUCKET`
- `PRESIGNED_URL_EXPIRY_SECONDS`

## Rules

- Prefer async patterns for I/O-bound work when introduced later
- Keep local and Docker behavior aligned
- Do not add infrastructure dependencies without an explicit requirement
