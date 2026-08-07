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
    """

    # For local dev only: if a .env file exists, load it.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "dev", "staging", "prod"] = Field(default="local", alias="APP_ENV")
    app_version: str = Field(default="0.0.0", alias="APP_VERSION")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_upload_size_mb: int = Field(default=10, alias="MAX_UPLOAD_SIZE_MB", ge=1)

    aws_region: str = Field(default="eu-north-1", alias="AWS_REGION")
    s3_input_bucket: str = Field(alias="S3_INPUT_BUCKET", min_length=1)
    presigned_url_expiry_seconds: int = Field(
        default=900,
        alias="PRESIGNED_URL_EXPIRY_SECONDS",
        ge=1,
    )

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Cached so we only parse env vars once per process.
    # S3_INPUT_BUCKET is required from the environment (no Python default).
    return Settings()  # pyright: ignore[reportCallIssue]
