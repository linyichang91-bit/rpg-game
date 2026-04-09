"""OpenAI-compatible client implementations."""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI, OpenAI

from server.llm.config import LLMSettings
from server.llm.json_payload import normalize_json_payload


class LLMGatewayError(Exception):
    """Raised when the configured LLM gateway cannot serve a valid response."""


class OpenAICompatibleJSONClient:
    """JSON-producing client for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        sdk_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client = sdk_client or OpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
            max_retries=0,
        )

    @classmethod
    def from_settings(cls, settings: LLMSettings) -> "OpenAICompatibleJSONClient":
        """Create a client from validated settings."""

        return cls(settings)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> str:
        """Request a JSON payload from the configured chat completion gateway."""

        schema_error: Exception | None = None

        if self._settings.json_schema_preferred:
            try:
                response = self._create_completion(
                    messages=self._build_messages(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    ),
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "orchestrator_decision",
                            "strict": True,
                            "schema": response_schema,
                        },
                    },
                )
                payload = self._extract_text_content(response)
                if _looks_like_json_schema_definition(payload):
                    raise LLMGatewayError(
                        "LLM 网关返回了 JSON Schema 定义，而不是实际数据对象。"
                    )
                return payload
            except Exception as exc:
                schema_error = exc

        try:
            response = self._create_completion(
                messages=self._build_messages(
                    system_prompt=system_prompt,
                    user_prompt=_build_json_object_instruction(user_prompt),
                ),
                response_format={"type": "json_object"},
            )
            payload = self._extract_text_content(response)
            if _looks_like_json_schema_definition(payload):
                raise LLMGatewayError(
                    "LLM 网关返回了 JSON Schema 定义，而不是实际数据对象。"
                )
            return payload
        except Exception as exc:
            if self._settings.json_schema_preferred and schema_error is not None:
                raise LLMGatewayError("LLM 网关请求失败。") from exc
            raise LLMGatewayError("LLM 网关请求失败。") from exc

    def _create_completion(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, Any],
    ) -> Any:
        return self._client.chat.completions.create(
            model=self._settings.model_name,
            messages=messages,
            temperature=0,
            response_format=response_format,
        )

    @staticmethod
    def _build_messages(
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        user_content = user_prompt
        if response_schema is not None:
            schema_text = json.dumps(
                response_schema,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            user_content = (
                f"{user_prompt}\n\n"
                "Required Response JSON Schema:\n"
                f"{schema_text}"
            )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    @staticmethod
    def _extract_text_content(response: Any) -> str:
        return _extract_text_content(response)


class OpenAICompatibleTextClient:
    """Async text-generation client for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        sdk_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client = sdk_client or AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
            max_retries=0,
        )

    @classmethod
    def from_settings(cls, settings: LLMSettings) -> "OpenAICompatibleTextClient":
        """Create an async text client from validated settings."""

        return cls(settings)

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Request narrative text from the configured chat completion gateway."""

        try:
            response = await self._client.chat.completions.create(
                model=self._settings.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
            )
        except Exception as exc:
            raise LLMGatewayError("LLM 网关请求失败。") from exc

        return _extract_text_content(response).strip()


def _should_fallback_to_json_object(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "json_schema",
            "response_format",
            "not support",
            "unsupported",
            "invalid parameter",
        )
    )


def _extract_text_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise LLMGatewayError("LLM 网关返回了无法识别的响应结构。") from exc

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        fragments: list[str] = []
        for item in content:
            if isinstance(item, str):
                fragments.append(item)
                continue

            text_value = getattr(item, "text", None)
            if isinstance(text_value, str):
                fragments.append(text_value)
                continue

            if isinstance(item, dict) and isinstance(item.get("text"), str):
                fragments.append(item["text"])
                continue

        if fragments:
            return "".join(fragments)

    raise LLMGatewayError("LLM 网关没有返回可读取的文本内容。")


def _build_json_object_instruction(user_prompt: str) -> str:
    return (
        f"{user_prompt}\n\n"
        "Return exactly one JSON object instance. "
        "Do not return a JSON schema, markdown fences, or explanatory text."
    )


def _looks_like_json_schema_definition(payload: str) -> bool:
    normalized = normalize_json_payload(payload)

    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        return False

    if not isinstance(parsed, dict):
        return False

    schema_keys = {"properties", "required", "title", "type"}
    return schema_keys.issubset(parsed.keys()) or "$defs" in parsed
