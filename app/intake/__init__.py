"""
Stage 1 intake: receive a CSV and decide VALIDATED or REJECTED.

Module roles in this package:
    router.py       HTTP surface (routes, status codes, dependency wiring)
    service.py      orchestration of the intake use cases
    validators.py   deterministic, pure validation rules
    job_registry.py temporary in-memory job state
    schemas.py      request/response contracts and error vocabularies
    errors.py       the controlled API error type

Stage 1 ends the moment a validated job is published to SQS; everything after
that boundary belongs to `app/worker`.
"""
