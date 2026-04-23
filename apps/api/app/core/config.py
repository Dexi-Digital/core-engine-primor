"""Application settings loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Motor Central API"
    app_version: str = "0.1.0"
    environment: str = Field(default="development")

    database_url: str = Field(
        default="postgresql+asyncpg://primor:primor@localhost:5432/primor",
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    secret_key: str = Field(default="change-me-in-production")
    access_token_expire_minutes: int = 60 * 8

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Object storage (MinIO / S3).
    s3_endpoint: str = Field(default="http://localhost:9000")
    s3_access_key: str = Field(default="minio")
    s3_secret_key: str = Field(default="minio123")
    s3_bucket: str = Field(default="primor-docs")


@lru_cache
def get_settings() -> Settings:
    return Settings()
