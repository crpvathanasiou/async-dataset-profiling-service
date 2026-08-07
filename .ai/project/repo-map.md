# Repo Map

## Layout (M2)

```text
.
├── app/
│   ├── main.py
│   ├── settings.py
│   ├── intake/
│   │   ├── router.py
│   │   ├── schemas.py
│   │   ├── service.py
│   │   ├── validators.py
│   │   └── job_registry.py   # temporary in-process job context
│   └── infrastructure/
│       └── s3.py             # boto3 adapter (presign/head/get)
├── tests/
│   ├── test_health.py
│   ├── test_intake.py       # legacy multipart
│   └── test_intake_s3.py    # S3-backed path (mocked)
├── Dockerfile
├── docker-compose.yaml
├── pyproject.toml
└── README.md
```

## Ownership

- `intake/` — Stage 1 HTTP, contracts, orchestration, validation, temporary job registry
- `infrastructure/s3.py` — boto3-only S3 operations; no business rules
