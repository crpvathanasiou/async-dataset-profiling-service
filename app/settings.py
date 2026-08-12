"""
Runtime configuration for both processes.

`Settings` is the single place environment variables enter the system. The API
process and the worker process each build their own instance from the same class,
which is how they end up pointing at the same bucket and queue without sharing
code paths or objects.

    environment variables (or .env locally) -> Settings -> routers, services,
                                                          adapters, worker

Design rules visible below:
- infrastructure identity (region, bucket, queue URL) is injected, never
  hardcoded, so one image can run in local/dev/staging/prod unchanged
- AWS credentials are deliberately absent: boto3 resolves them through the
  standard provider chain (env vars, shared profile, or a task role)
- values required in every environment have no Python default, so a
  misconfigured deployment fails at startup instead of at first use

This module holds no business logic; it only describes and validates settings.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Loads configuration from environment variables.

    Why this matters:
    - Production config should be injected at runtime (env vars), not hardcoded.
    - Same code/image can run in many environments with different settings.

    Validation is part of the contract: `Field` aliases pin the exact environment
    variable names, and constraints such as `ge=1` or `min_length=1` mean an
    invalid value is rejected at construction time with a clear error rather than
    causing confusing failures deep inside an AWS call.
    """

    # For local dev only: if a .env file exists, load it.
    # extra="ignore" keeps unrelated variables in the environment from breaking
    # startup, so this class can coexist with other tooling's variables.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # A Literal instead of a plain string: an unknown APP_ENV is a configuration
    # error worth failing on, not a value to pass through.
    app_env: Literal["local", "dev", "staging", "prod"] = Field(default="local", alias="APP_ENV")
    app_version: str = Field(default="0.0.0", alias="APP_VERSION")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    # Business limit enforced by intake validation, expressed in MB for humans
    # and converted to bytes by the property below.
    max_upload_size_mb: int = Field(default=10, alias="MAX_UPLOAD_SIZE_MB", ge=1)

    # Region is explicit rather than left to boto3 discovery, because SigV4
    # presigned URLs must be signed for the bucket's own region.
    aws_region: str = Field(default="eu-north-1", alias="AWS_REGION")
    # Required, no default: a bucket name is environment-specific, and silently
    # falling back to some other bucket would be worse than failing to start.
    s3_input_bucket: str = Field(alias="S3_INPUT_BUCKET", min_length=1)
    # How long a presigned upload URL stays usable. Short-lived by design: the URL
    # is a capability handed to the client.
    presigned_url_expiry_seconds: int = Field(
        default=900,
        alias="PRESIGNED_URL_EXPIRY_SECONDS",
        ge=1,
    )
    # Required, no default; this is the asynchronous boundary both processes must
    # agree on. Publisher and consumer are wired to the same queue only because
    # they read the same variable.
    sqs_job_queue_url: str = Field(alias="SQS_JOB_QUEUE_URL", min_length=1)
    # Duration of the worker's simulated processing. Configurable so the async
    # behavior can be made visible locally and kept short in tests; ge=0 allows
    # disabling the delay entirely.
    worker_simulated_processing_seconds: int = Field(
        default=5,
        alias="WORKER_SIMULATED_PROCESSING_SECONDS",
        ge=0,
    )

    @property
    def max_upload_size_bytes(self) -> int:
        """MB limit converted once here, so callers never repeat the arithmetic."""
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the process-wide settings instance.

    Also the FastAPI dependency used by routers (`Depends(get_settings)`), which
    is why tests can clear the cache (`get_settings.cache_clear()`) to load a
    different environment.
    """
    # Cached so we only parse env vars once per process.
    # S3_INPUT_BUCKET and SQS_JOB_QUEUE_URL are required from the environment
    # (no Python defaults).
    return Settings()  # pyright: ignore[reportCallIssue]
