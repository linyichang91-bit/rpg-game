"""Environment-backed configuration for LLM access."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ConfigDict, Field, SecretStr, ValidationError, field_validator

from server.schemas.core import EngineBaseModel


class LLMSettingsError(Exception):
    """Raised when environment-backed LLM settings are missing or invalid."""


class LLMSettings(EngineBaseModel):
    """Validated settings for connecting to an OpenAI-compatible gateway."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
    )

    api_key: SecretStr = Field(alias="LLM_API_KEY")
    base_url: str = Field(alias="LLM_BASE_URL")
    model_name: str = Field(alias="LLM_MODEL_NAME")
    request_timeout_seconds: float = Field(default=60.0, gt=0.0)
    json_schema_preferred: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL must start with http:// or https://.")
        return normalized

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("LLM_MODEL_NAME cannot be empty.")
        return normalized

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "LLMSettings":
        """Load settings from process environment with optional .env support."""

        env_path = Path(env_file)
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)

        try:
            timeout_raw = os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "60")
            json_schema_raw = os.getenv("LLM_JSON_SCHEMA_PREFERRED", "true")
            return cls.model_validate(
                {
                    "LLM_API_KEY": os.getenv("LLM_API_KEY"),
                    "LLM_BASE_URL": os.getenv("LLM_BASE_URL"),
                    "LLM_MODEL_NAME": os.getenv("LLM_MODEL_NAME"),
                    "request_timeout_seconds": float(timeout_raw),
                    "json_schema_preferred": json_schema_raw.strip().lower()
                    not in {"0", "false", "no"},
                }
            )
        except ValidationError as exc:
            raise LLMSettingsError("Invalid LLM settings in environment or .env file.") from exc
        except ValueError as exc:
            raise LLMSettingsError(
                "LLM_REQUEST_TIMEOUT_SECONDS must be a positive number."
            ) from exc
