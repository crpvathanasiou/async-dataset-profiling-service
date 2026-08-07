# Review Checklist

## Purpose

Checklist for reviewing implementations in this repository.

## 1. Scope

- Does the change match the requested scope exactly?
- Is anything from a future milestone included?
- Are there unnecessary abstractions?

## 2. Architecture

- Does the change preserve the minimal FastAPI baseline when that is the current milestone?
- Are responsibilities kept clear?
- Was speculative infrastructure avoided (AWS, DB, queues, caches, DI frameworks)?

## 3. Testing

- Is there a meaningful test for the change?
- Would the test fail if the behavior broke?
- Was a clear test command provided?

## 4. Quality

- Do Ruff, Pyright, and Pytest still pass where relevant?
- Is naming explicit?
- Is the code easy to review?

## 5. Observability and ops

- Are logs still container-friendly?
- Are health endpoints still honest and simple?
- Did Docker/Compose remain coherent if touched?

## 6. Quick pass

- correct scope
- meaningful test
- no architecture violations
- no overengineering

If any fail → do not accept.

## Final principle

> If you cannot explain why the change is correct, observable, and verified, it is not ready.
