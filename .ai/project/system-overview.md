# System Overview

## Project

`async-dataset-profiling-service`

## Current milestone

**M2 — S3-backed Stage 1 intake with presigned upload**

```text
Client
  ↓
POST /api/v1/intake/uploads
  ↓
job_id + S3 key + presigned PUT URL
  ↓
Client uploads CSV to S3
  ↓
POST /api/v1/intake/jobs/{job_id}/validate
  ↓
VALIDATED / REJECTED
```

Stage 1 states:

```text
RECEIVED
VALIDATING
VALIDATED
REJECTED
```

Job context for the S3 key is held in a temporary in-process registry (replaceable later). No SQS, workers, Stage 2, or database persistence yet.

Legacy multipart `POST /api/v1/intake/jobs` remains for local/reference testing only.

## Future direction — context only

```text
Stage 1: Intake + Validation
    → asynchronous handoff
Stage 2: Processing + Results
```
