"""Tests for environment-backed LLM configuration and client wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from server.agent.gm import GameMasterAgent, build_gm_agent_from_env
from server.llm.config import LLMSettings, LLMSettingsError
from server.llm.openai_compatible import OpenAICompatibleJSONClient


class FakeCompletionEndpoint:
    """Fake completions endpoint for client tests."""

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeSDKClient:
    """Fake OpenAI-compatible SDK root object."""

    def __init__(self, responses: list[object]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletionEndpoint(responses))


def write_env_file(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "LLM_API_KEY=test-key",
                "LLM_BASE_URL=https://example.invalid/v1",
                "LLM_MODEL_NAME=test/model",
                "LLM_REQUEST_TIMEOUT_SECONDS=42",
                "LLM_JSON_SCHEMA_PREFERRED=true",
            )
        ),
        encoding="utf-8",
    )


def test_llm_settings_load_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    write_env_file(env_file)

    settings = LLMSettings.from_env(env_file=env_file)

    assert settings.api_key.get_secret_value() == "test-key"
    assert settings.base_url == "https://example.invalid/v1"
    assert settings.model_name == "test/model"
    assert settings.request_timeout_seconds == 42
    assert settings.json_schema_preferred is True


def test_llm_settings_raise_on_missing_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_BASE_URL=https://example.invalid/v1\n", encoding="utf-8")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)

    with pytest.raises(LLMSettingsError):
        LLMSettings.from_env(env_file=env_file)


def test_openai_compatible_client_falls_back_to_json_object() -> None:
    settings = LLMSettings.model_validate(
        {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://example.invalid/v1",
            "LLM_MODEL_NAME": "test/model",
            "request_timeout_seconds": 30,
            "json_schema_preferred": True,
        }
    )
    fake_sdk = FakeSDKClient(
        [
            ValueError("json_schema response_format is not supported"),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"pipeline_type":"utility","confidence":0.9,"parameters":{"query_type":"inventory"},"clarification_needed":null}'
                        )
                    )
                ]
            ),
        ]
    )
    client = OpenAICompatibleJSONClient(settings, sdk_client=fake_sdk)

    result = client.generate_json(
        system_prompt="system",
        user_prompt="user",
        response_schema={"type": "object"},
    )

    calls = fake_sdk.chat.completions.calls
    assert result.startswith('{"pipeline_type":"utility"')
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"


def test_build_gm_agent_from_env_returns_ready_instance(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    write_env_file(env_file)

    agent = build_gm_agent_from_env(env_file=str(env_file))

    assert isinstance(agent, GameMasterAgent)
