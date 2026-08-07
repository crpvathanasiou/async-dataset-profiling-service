"""Shared pytest setup.

S3_INPUT_BUCKET is required at settings load / app import time.
Provide a non-account-specific test value so unit tests do not need real AWS config.
"""

import os

os.environ.setdefault("S3_INPUT_BUCKET", "test-intake-bucket")
