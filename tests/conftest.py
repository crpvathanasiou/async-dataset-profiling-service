"""Shared pytest setup.

S3_INPUT_BUCKET and SQS_JOB_QUEUE_URL are required at settings load / app import time.
Provide non-account-specific test values so unit tests do not need real AWS config.
"""

import os

os.environ.setdefault("S3_INPUT_BUCKET", "test-intake-bucket")
os.environ.setdefault(
    "SQS_JOB_QUEUE_URL",
    "https://sqs.eu-north-1.amazonaws.com/123456789012/test-jobs",
)
