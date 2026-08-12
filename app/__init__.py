"""
async-dataset-profiling-service application package.

Two processes are built from this one package:

    app.main        FastAPI API process   (uvicorn app.main:app)
    app.worker.main worker process        (python -m app.worker.main)

Package layout by architectural layer:

    settings.py     runtime configuration, shared by both processes
    intake/         Stage 1 HTTP surface, orchestration, validation rules
    messaging/      versioned message contract crossing the queue
    infrastructure/ adapters that isolate boto3 (S3, SQS)
    worker/         the long-running SQS consumer

The two processes never call each other; the SQS queue is the asynchronous
boundary between them.
"""
