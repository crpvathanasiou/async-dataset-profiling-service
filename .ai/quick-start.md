# Quick Start

## Project

`async-dataset-profiling-service`

## Current milestone

Clean FastAPI production baseline.

## 1. Start the environment

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Or locally:

```powershell
poetry install
poetry run uvicorn app.main:app --reload
```

## 2. Open the project in Cursor

Ensure the `.ai/` folder is visible.

## 3. Load baseline context

Read:

- `.ai/shared/engineering-principles.md`
- `.ai/shared/architecture-rules.md`
- `.ai/shared/delivery-method.md`
- `.ai/shared/agent-behavior-contract.md`
- `.ai/project/project-instructions.md`
- `.ai/project/system-overview.md`
- `.ai/project/repo-map.md`
- `.ai/project/development-environment.md`
- `.ai/project/testing-strategy.md`

## 4. Workflow loop

- ask for plan
- review plan
- allow implementation
- verify output

## 5. Mandatory verification

AI output must include:

- implementation
- tests
- test/quality commands
- what passing checks prove

## 6. Review

Use `.ai/review-checklist.md`.

## Final principle

Work in small, verifiable steps. If it cannot be tested, it is not complete.
