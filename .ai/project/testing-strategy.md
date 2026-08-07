# Testing Strategy

## Purpose

Every implemented feature must ship with executable verification that proves intended behavior.

## Core philosophy

1. Feature delivery requires verification
2. Tests must prove behavior, not merely exercise code
3. Prefer the smallest correct verification layer
4. A feature and its test belong to the same delivery slice

## Definition of done for a feature

A feature is done when:

- the requested behavior is implemented
- an automated test exists
- the test verifies the intended behavior
- the test can be run with a clear command
- the test would fail if the behavior were broken

## Current coverage (M2)

At minimum:

- application starts
- `GET /health/live`
- `GET /health/ready`
- Stage 1 legacy multipart cases (`tests/test_intake.py`)
- Stage 1 S3-backed path with mocked S3 (`tests/test_intake_s3.py`)

Do not build large test suites for Stage 2 or other functionality that does not yet exist.

## Test levels

- **Unit** — isolated logic and schema validation
- **Integration / API** — FastAPI endpoints and request/response behavior

Use the lightest level that reliably proves the change.

## Commands

```powershell
poetry run pytest
```

## Anti-patterns

- implementation without tests
- tests that always pass regardless of behavior
- postponing tests "until later" without explicit instruction
- mocking away the behavior under test so nothing meaningful is asserted
