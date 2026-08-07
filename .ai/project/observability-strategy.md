# Observability Strategy

## Purpose

Keep the service understandable in local development and containerized environments.

## Current baseline

- container-friendly logging to stdout/stderr
- configurable log level via `LOG_LEVEL`
- Docker/Compose health checks against `/health/live`
- Stage 1 intake logs `job_id`, filename, validation start/outcome, and rejection codes (never file contents)

## Principles

1. **Observability by default** — meaningful operations should leave useful evidence
2. **Failure clarity** — failures must be visible with enough context to diagnose
3. **Container-friendly logs** — write to stdout/stderr; do not rely on local log files
4. **Health signals** — liveness and readiness endpoints must remain simple and honest

## Health endpoints

- `/health/live` — process is alive
- `/health/ready` — application is ready to receive requests

Do not invent artificial dependency checks when no external dependencies exist.

## Future notes (context only)

When Stage 1 / Stage 2 work begins, introduce request correlation and richer operational metadata as those requirements appear. Do not add tracing or metrics infrastructure preemptively.
