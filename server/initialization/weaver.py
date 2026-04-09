"""Fanfiction world weaver for compiling sandbox prompts into WorldConfig."""

from __future__ import annotations

import json
from textwrap import dedent
from typing import Any, Protocol, get_args, get_origin

from pydantic import BaseModel, ValidationError

from server.llm.config import LLMSettings
from server.llm.json_payload import normalize_json_payload
from server.llm.openai_compatible import OpenAICompatibleJSONClient
from server.schemas.core import EngineBaseModel, WorldConfig


class WorldWeaverPromptBundle(EngineBaseModel):
    """Prompt payload used to request a world configuration."""

    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any]


class WorldWeaverError(Exception):
    """Base exception for world weaving failures."""


class WorldConfigValidationError(WorldWeaverError):
    """Raised when the model response cannot be validated into WorldConfig."""


class StructuredJSONClient(Protocol):
    """Provider-agnostic JSON generation boundary for the weaver."""

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> str:
        """Generate a JSON string matching the supplied schema."""


class WorldWeaver:
    """Compiles fanfiction prompts into engine-ready world configuration."""

    def __init__(
        self,
        llm_client: StructuredJSONClient,
        *,
        max_validation_retries: int = 1,
    ) -> None:
        self._llm_client = llm_client
        self._max_validation_retries = max_validation_retries

    def generate_world_config(self, fanfic_prompt: str) -> WorldConfig:
        """Generate and validate a WorldConfig from a fanfiction setup prompt."""

        prompt_bundle = build_world_weaver_prompt(fanfic_prompt)
        last_error: Exception | None = None

        for _ in range(self._max_validation_retries + 1):
            raw_response = self._llm_client.generate_json(
                system_prompt=prompt_bundle.system_prompt,
                user_prompt=prompt_bundle.user_prompt,
                response_schema=prompt_bundle.response_schema,
            )
            try:
                normalized_response = normalize_json_payload(raw_response)
                normalized_payload = _normalize_world_config_payload(
                    json.loads(normalized_response)
                )
                return WorldConfig.model_validate(normalized_payload)
            except ValidationError as exc:
                last_error = exc
            except json.JSONDecodeError as exc:
                last_error = exc

        raise WorldConfigValidationError(
            _build_world_config_validation_error_message(last_error)
        ) from last_error


def generate_world_config(
    fanfic_prompt: str,
    *,
    env_file: str = ".env",
) -> WorldConfig:
    """Default env-backed entrypoint for the fanfic world weaver."""

    weaver = build_world_weaver_from_env(env_file=env_file)
    return weaver.generate_world_config(fanfic_prompt)


def build_world_weaver_from_env(*, env_file: str = ".env") -> WorldWeaver:
    """Create a WorldWeaver from environment-backed LLM settings."""

    settings = LLMSettings.from_env(env_file=env_file)
    llm_client = OpenAICompatibleJSONClient.from_settings(settings)
    return WorldWeaver(llm_client)


def build_world_weaver_prompt(fanfic_prompt: str) -> WorldWeaverPromptBundle:
    """Assemble the strict prompt that compiles a fanfiction setup into WorldConfig."""

    system_prompt = dedent(
        """
        You are a high-level fanfiction world architect.
        Your job is to convert a user's fanfiction setup prompt into a strict JSON
        configuration for a game engine.

        Generation requirements:
        1. Identify the base intellectual property and set fanfic_meta.base_ip.
        2. Classify the universe type such as Canon, AU, Modern AU, or Crossover.
        3. Set fanfic_meta.tone_and_style to the user's requested mood and style.
        4. Build a world glossary that maps engine abstract keys into setting-specific terms.
        5. Preload a fitting starting_location, 1-3 key_npcs, and opening initial_quests.
        6. Populate world_book.campaign_context with timeline-locked lore anchors.
        7. Produce engine-facing JSON only. Do not write explanatory text outside the JSON object.

        Lore generation rules for world_book.campaign_context:
        - You must anchor the setting to the exact requested era and timeline.
        - era_and_timeline must name a precise canon era, arc, year, or timeline node.
        - macro_world_state must reflect only factions, institutions, and NPCs who should exist at that time.
        - Never introduce future characters early, never revive dead characters, and never collapse distant eras together.
        - opening_scene must be vivid and immediate: include a concrete physical location, active motion, sensory detail,
          and one urgent hook that forces the player to react.

        Glossary minimums:
        - stats should include at least stat_hp and stat_mp when the setting has a spiritual, magical, psychic, or energy resource.
        - damage_types should include at least dmg_kinetic and dmg_energy.
        - item_categories should include at least item_weapon.

        Hard constraints:
        - You must strictly follow the JSON schema.
        - Do not output any explanatory text outside the JSON object.
        - Keep mechanics abstract and engine-friendly.
        - world_book.campaign_context must obey canon timeline logic as strictly as possible.
        - All player-visible strings should be written in Simplified Chinese whenever possible.
        """
    ).strip()

    user_prompt = dedent(
        f"""
        Fanfiction Prompt:
        {fanfic_prompt}

        Required output fields:
        - world_id
        - theme
        - fanfic_meta.base_ip
        - fanfic_meta.universe_type
        - fanfic_meta.tone_and_style
        - world_book.campaign_context.era_and_timeline
        - world_book.campaign_context.macro_world_state
        - world_book.campaign_context.looming_crisis
        - world_book.campaign_context.opening_scene
        - glossary.stats
        - glossary.damage_types
        - glossary.item_categories
        - starting_location
        - key_npcs
        - initial_quests
        - mechanics

        Abstract keys that should be considered for glossary mapping:
        - stats: stat_hp, stat_mp
        - damage_types: dmg_kinetic, dmg_energy
        - item_categories: item_weapon

        Language requirements:
        - theme, fanfic_meta.universe_type, fanfic_meta.tone_and_style, world_book.campaign_context,
          glossary values, starting_location, key_npcs, and initial_quests should use Simplified Chinese.
        - Prefer established Chinese translations for well-known IP names when appropriate.
        """
    ).strip()

    return WorldWeaverPromptBundle(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=WorldConfig.model_json_schema(),
    )


def _normalize_world_config_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    top_level_campaign_context = normalized.pop("campaign_context", None)
    if "world_book" not in normalized and isinstance(top_level_campaign_context, dict):
        normalized["world_book"] = {"campaign_context": top_level_campaign_context}
    elif isinstance(normalized.get("world_book"), dict):
        world_book = dict(normalized["world_book"])
        if "campaign_context" not in world_book and isinstance(top_level_campaign_context, dict):
            world_book["campaign_context"] = top_level_campaign_context
        normalized["world_book"] = world_book

    normalized["starting_location"] = _coerce_string_value(
        normalized.get("starting_location"),
        preferred_keys=("location_name", "name", "location_id", "id"),
    )
    normalized["key_npcs"] = _coerce_string_list(
        normalized.get("key_npcs"),
        preferred_keys=("npc_name", "name", "npc_id", "id"),
    )
    normalized["initial_quests"] = _coerce_string_list(
        normalized.get("initial_quests"),
        preferred_keys=("quest_name", "name", "quest_id", "id", "objective"),
    )
    return _prune_to_model_schema(normalized, WorldConfig)


def _build_world_config_validation_error_message(error: Exception | None) -> str:
    base_message = "世界织布机未能生成合法的世界配置数据。"
    if error is None:
        return base_message

    if isinstance(error, ValidationError):
        issue_summaries: list[str] = []
        for issue in error.errors()[:3]:
            path = ".".join(str(part) for part in issue.get("loc", ())) or "<root>"
            message = issue.get("msg", "校验失败")
            issue_summaries.append(f"{path}: {message}")
        if issue_summaries:
            return f"{base_message} 最后一次校验失败：{'；'.join(issue_summaries)}"

    if isinstance(error, json.JSONDecodeError):
        return f"{base_message} 最后一次返回不是合法 JSON：{error.msg}"

    return f"{base_message} {error}"


def _prune_to_model_schema(payload: Any, model_type: type[BaseModel]) -> Any:
    if not isinstance(payload, dict):
        return payload

    normalized: dict[str, Any] = {}
    for field_name, field_info in model_type.model_fields.items():
        if field_name not in payload:
            continue
        normalized[field_name] = _prune_value_to_annotation(
            payload[field_name],
            field_info.annotation,
        )
    return normalized


def _prune_value_to_annotation(value: Any, annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return _prune_to_model_schema(value, annotation)
        return value

    if origin in (list, tuple):
        if not isinstance(value, list):
            return value
        item_type = get_args(annotation)[0] if get_args(annotation) else Any
        return [_prune_value_to_annotation(item, item_type) for item in value]

    if origin is dict:
        if not isinstance(value, dict):
            return value
        args = get_args(annotation)
        value_type = args[1] if len(args) > 1 else Any
        return {
            key: _prune_value_to_annotation(item, value_type)
            for key, item in value.items()
        }

    union_args = [arg for arg in get_args(annotation) if arg is not type(None)]
    for union_arg in union_args:
        if isinstance(union_arg, type) and issubclass(union_arg, BaseModel):
            return _prune_to_model_schema(value, union_arg)

    return value


def _coerce_string_list(
    value: Any,
    *,
    preferred_keys: tuple[str, ...],
) -> Any:
    if isinstance(value, list):
        return [_coerce_string_value(item, preferred_keys=preferred_keys) for item in value]
    return value


def _coerce_string_value(
    value: Any,
    *,
    preferred_keys: tuple[str, ...],
) -> Any:
    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        for key in preferred_keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

    return value
