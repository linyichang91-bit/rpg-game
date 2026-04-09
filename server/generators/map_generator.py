"""Dynamic map node generation for just-in-time world expansion."""

from __future__ import annotations

import json
from textwrap import dedent
from typing import Any, Protocol

from pydantic import ValidationError

from server.llm.config import LLMSettings
from server.llm.json_payload import normalize_json_payload
from server.llm.openai_compatible import LLMGatewayError, OpenAICompatibleJSONClient
from server.schemas.core import EngineBaseModel, GameState, WorldNode


class MapPromptBundle(EngineBaseModel):
    """Prompt payload sent to the structured map generator."""

    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any]


class StructuredJSONClient(Protocol):
    """Provider-agnostic JSON generation boundary for map nodes."""

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> str:
        """Generate a JSON string matching the supplied schema."""


class DynamicMapGenerator:
    """Generate new topology nodes while preserving world-theme consistency."""

    def __init__(
        self,
        llm_client: StructuredJSONClient,
        *,
        max_validation_retries: int = 1,
    ) -> None:
        self._llm_client = llm_client
        self._max_validation_retries = max_validation_retries

    def generate_node(
        self,
        current_state: GameState,
        *,
        current_node_id: str,
        target_node_id: str,
        target_name: str,
    ) -> WorldNode:
        """Generate a new world node, falling back to deterministic content if needed."""

        prompt_bundle = build_map_prompt(
            current_state=current_state,
            current_node_id=current_node_id,
            target_name=target_name,
        )
        last_error: Exception | None = None

        for _ in range(self._max_validation_retries + 1):
            try:
                raw_response = self._llm_client.generate_json(
                    system_prompt=prompt_bundle.system_prompt,
                    user_prompt=prompt_bundle.user_prompt,
                    response_schema=prompt_bundle.response_schema,
                )
                payload = json.loads(normalize_json_payload(raw_response))
                normalized = _normalize_world_node_payload(
                    payload,
                    node_id=target_node_id,
                    target_name=target_name,
                )
                return WorldNode.model_validate(normalized)
            except (ValidationError, json.JSONDecodeError, TypeError, ValueError, LLMGatewayError) as exc:
                last_error = exc

        return _build_fallback_node(
            current_state=current_state,
            target_node_id=target_node_id,
            target_name=target_name,
            _last_error=last_error,
        )


def build_map_generator_from_env(*, env_file: str = ".env") -> DynamicMapGenerator:
    """Create a dynamic map generator from environment-backed LLM settings."""

    settings = LLMSettings.from_env(env_file=env_file)
    llm_client = OpenAICompatibleJSONClient.from_settings(settings)
    return DynamicMapGenerator(llm_client)


def build_map_prompt(
    *,
    current_state: GameState,
    current_node_id: str,
    target_name: str,
) -> MapPromptBundle:
    """Assemble the strict prompt for dynamic node generation."""

    current_node = current_state.world_config.topology.nodes.get(current_node_id)
    current_title = current_node.title if current_node is not None else current_state.current_location_id
    current_desc = current_node.base_desc if current_node is not None else current_state.world_config.starting_location

    system_prompt = dedent(
        f"""
        你是一个地理架构师。
        玩家正在从「{current_title}」前往「{target_name}」。

        你的任务是输出一个全新的地点节点 JSON。

        强约束：
        1. 这个地点必须与当前世界主题「{current_state.world_config.theme}」逻辑一致。
        2. 这个地点必须与当前地点自然连通，不能突然跳到其他作品宇宙。
        3. 只输出 JSON，不要输出任何解释文本。
        4. title、base_desc、hidden_detail_dc10、deep_secret_dc18 必须使用简体中文。
        """
    ).strip()

    user_prompt = dedent(
        f"""
        当前地点简介：
        {current_desc}

        目标地点名称：
        {target_name}

        世界配置摘要：
        {current_state.world_config.model_dump_json(indent=2)}
        """
    ).strip()

    return MapPromptBundle(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=WorldNode.model_json_schema(),
    )


def _normalize_world_node_payload(
    payload: Any,
    *,
    node_id: str,
    target_name: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("World node payload must be a JSON object.")

    title = _coerce_text(payload.get("title")) or target_name
    base_desc = _coerce_text(payload.get("base_desc")) or f"这里是{target_name}。"
    hidden_detail_dc10 = _coerce_text(payload.get("hidden_detail_dc10"))
    deep_secret_dc18 = _coerce_text(payload.get("deep_secret_dc18"))
    tags = payload.get("tags")
    normalized_tags = [str(tag).strip() for tag in tags] if isinstance(tags, list) else []

    return {
        "node_id": node_id,
        "title": title,
        "base_desc": base_desc,
        "hidden_detail_dc10": hidden_detail_dc10,
        "deep_secret_dc18": deep_secret_dc18,
        "tags": normalized_tags,
    }


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _build_fallback_node(
    *,
    current_state: GameState,
    target_node_id: str,
    target_name: str,
    _last_error: Exception | None = None,
) -> WorldNode:
    del _last_error
    theme = current_state.world_config.theme
    return WorldNode(
        node_id=target_node_id,
        title=target_name,
        base_desc=f"你踏入了{target_name}，这里的一切都延续着「{theme}」的阴影与气息。",
        hidden_detail_dc10=f"{target_name}里有一些容易被忽略的细节，稍微留神就能发现端倪。",
        deep_secret_dc18=f"{target_name}深处埋着更危险也更关键的秘密，只有极少数人能够触及。",
        tags=["generated_location"],
    )
