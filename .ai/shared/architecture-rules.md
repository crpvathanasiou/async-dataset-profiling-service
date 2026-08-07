# Architecture Rules

## Purpose

Architectural constraints for `async-dataset-profiling-service`.

## 1. Current architecture (M2)

- FastAPI application entrypoint (`app/main.py`)
- Pydantic settings (`app/settings.py`)
- Health endpoints
- Stage 1 intake (`app/intake/`) with S3-backed primary path
- S3 adapter (`app/infrastructure/s3.py`)
- Temporary in-process job registry for upload→validate linkage
- Docker multi-stage runtime with 1 Uvicorn worker

### Stage 1

```text
Stage 1 = Intake + Validation
```

Primary flow uses presigned S3 upload. Legacy multipart remains for local/reference only.

States:

```text
RECEIVED → VALIDATING → VALIDATED | REJECTED
```

## 2. Growth rules

When new requirements appear:

1. Prefer extending existing modules first
2. Extract a new module only when a clear responsibility emerges
3. Do not create layers "for completeness"

Forbidden for speculative readiness:

- repository pattern
- unused service/controller layers
- DI frameworks
- abstract interfaces / factories
- event buses
- AWS / DB / queue clients without an explicit requirement

## 3. API rules

- Keep route handlers thin
- Validate with Pydantic where useful
- Business validation failures for Stage 1 return HTTP 200 with `status=REJECTED`
- Transport/API errors use normal HTTP error responses

## 4. Configuration rules

- Runtime configuration comes from environment variables
- Do not hardcode environment-specific values (e.g. max upload size)
- Ignore unknown env keys safely where settings already do so

## 5. Dependency rules

- Do not add Redis, databases, queues, or cloud SDKs unless explicitly requested
- Prefer stdlib solutions when they are sufficient (`csv`, `uuid`)
- Keep the production image small and non-root

## 6. Future architecture — context only

```text
Stage 1: Intake + Validation → asynchronous handoff → Stage 2: Processing + Results
```

Do not implement Stage 2 until asked. Do not pre-build abstractions for it.

## Final rule

> Architecture should emerge from real requirements, not anticipation.
