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


class ProloguePromptBundle(EngineBaseModel):
    """Prompt payload used to request a long-form opening prologue."""

    system_prompt: str
    user_prompt: str


class WorldWeaverResult(EngineBaseModel):
    """Combined world-weaver output used by the API layer."""

    world_config: WorldConfig
    prologue_text: str


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


class NarrativeTextClient(Protocol):
    """Provider-agnostic text generation boundary for long narrative outputs."""

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.85,
    ) -> str:
        """Generate plain narrative text from prompts."""


class WorldWeaver:
    """Compiles fanfiction prompts into engine-ready world configuration."""

    def __init__(
        self,
        llm_client: StructuredJSONClient,
        *,
        narrative_client: NarrativeTextClient | None = None,
        max_validation_retries: int = 1,
    ) -> None:
        self._llm_client = llm_client
        self._narrative_client = narrative_client
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

    def generate_world_bundle(self, fanfic_prompt: str) -> WorldWeaverResult:
        """Generate a world configuration plus a long-form prologue chapter."""

        world_config = self.generate_world_config(fanfic_prompt)
        prologue_text = self._generate_prologue_text(
            fanfic_prompt=fanfic_prompt,
            world_config=world_config,
        )
        return WorldWeaverResult(
            world_config=world_config,
            prologue_text=prologue_text,
        )

    def _generate_prologue_text(
        self,
        *,
        fanfic_prompt: str,
        world_config: WorldConfig,
    ) -> str:
        prompt_bundle = build_prologue_prompt(
            fanfic_prompt=fanfic_prompt,
            world_config=world_config,
        )
        narrative_client = self._resolve_narrative_client()
        if narrative_client is None:
            return _build_prologue_fallback(world_config)

        attempt_prompt = prompt_bundle.user_prompt
        best_attempt = ""

        for _ in range(2):
            try:
                draft = narrative_client.generate_text(
                    system_prompt=prompt_bundle.system_prompt,
                    user_prompt=attempt_prompt,
                    temperature=0.9,
                )
            except Exception:
                break

            normalized = _normalize_generated_text(draft)
            if _count_visible_characters(normalized) >= 800:
                return normalized
            if _count_visible_characters(normalized) > _count_visible_characters(best_attempt):
                best_attempt = normalized

            attempt_prompt = (
                f"{prompt_bundle.user_prompt}\n\n"
                "The previous draft was too short. Rewrite from scratch with at least 800 Chinese characters, "
                "stronger sensory detail, and a sharper hook ending."
            )

        if _count_visible_characters(best_attempt) >= 400:
            return best_attempt
        return _build_prologue_fallback(world_config)

    def _resolve_narrative_client(self) -> NarrativeTextClient | None:
        if self._narrative_client is not None:
            return self._narrative_client

        candidate = self._llm_client
        generate_text = getattr(candidate, "generate_text", None)
        if callable(generate_text):
            return candidate  # type: ignore[return-value]
        return None


def generate_world_config(
    fanfic_prompt: str,
    *,
    env_file: str = ".env",
) -> WorldConfig:
    """Default env-backed entrypoint for the fanfic world weaver."""

    weaver = build_world_weaver_from_env(env_file=env_file)
    return weaver.generate_world_config(fanfic_prompt)


def generate_world_bundle(
    fanfic_prompt: str,
    *,
    env_file: str = ".env",
) -> WorldWeaverResult:
    """Default env-backed entrypoint for world config plus prologue generation."""

    weaver = build_world_weaver_from_env(env_file=env_file)
    return weaver.generate_world_bundle(fanfic_prompt)


def build_world_weaver_from_env(*, env_file: str = ".env") -> WorldWeaver:
    """Create a WorldWeaver from environment-backed LLM settings."""

    settings = LLMSettings.from_env(env_file=env_file)
    llm_client = OpenAICompatibleJSONClient.from_settings(settings)
    return WorldWeaver(
        llm_client,
        narrative_client=llm_client,
    )


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


def build_prologue_prompt(
    *,
    fanfic_prompt: str,
    world_config: WorldConfig,
) -> ProloguePromptBundle:
    """Assemble the long-form prologue prompt used for interactive novel mode."""

    campaign_context = world_config.world_book.campaign_context
    key_npcs = ", ".join(world_config.key_npcs[:3]) if world_config.key_npcs else "无"
    initial_quests = ", ".join(world_config.initial_quests[:3]) if world_config.initial_quests else "无"
    glossary_sample = ", ".join(
        list(world_config.glossary.stats.values())[:2]
        + list(world_config.glossary.damage_types.values())[:2]
    )

    system_prompt = dedent(
        """
        你是一位顶级轻小说与网文作者，擅长写高沉浸感的第一章开场。
        你必须写出版级叙事，而不是游戏播报。

        写作硬性要求:
        1. 字数不少于 800 字，优先 900-1300 字。
        2. 必须按这个节奏推进：
           - 感官唤醒（至少两种，如痛觉/气味/触感）
           - 环境与时代交代（世界局势和地点气氛）
           - 主角能力初探（一次可见的能力尝试）
           - 突发危机或主线介入（必须形成强悬念）
        3. 绝对禁止“系统提示”“属性播报”“选项菜单”口吻。
        4. 多用短句和强动词，突出身体反应与心理波动。
        5. 结尾必须停在高张力自然断点，把行动权交给玩家。
        6. 全文使用简体中文。
        """
    ).strip()

    user_prompt = dedent(
        f"""
        用户创世设定:
        {fanfic_prompt}

        世界锚点:
        - 主题: {world_config.theme}
        - IP: {world_config.fanfic_meta.base_ip}
        - 宇宙类型: {world_config.fanfic_meta.universe_type}
        - 风格: {world_config.fanfic_meta.tone_and_style}
        - 时代时间线: {campaign_context.era_and_timeline}
        - 宏观局势: {campaign_context.macro_world_state}
        - 迫近危机: {campaign_context.looming_crisis}
        - 开场场景: {campaign_context.opening_scene}
        - 初始地点: {world_config.starting_location}
        - 关键角色: {key_npcs}
        - 初始目标: {initial_quests}
        - 术语样本: {glossary_sample}

        现在直接写“序章正文”，不要输出标题、不要解释你的写作方法。
        """
    ).strip()

    return ProloguePromptBundle(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
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


def _normalize_generated_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").strip()
    normalized = normalized.removeprefix("```").removesuffix("```").strip()
    return normalized or "序章尚未成型，但风暴已经逼近。"


def _count_visible_characters(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _build_prologue_fallback(world_config: WorldConfig) -> str:
    campaign_context = world_config.world_book.campaign_context
    location = world_config.starting_location
    looming_crisis = campaign_context.looming_crisis
    era = campaign_context.era_and_timeline
    macro_state = campaign_context.macro_world_state
    opening_scene = campaign_context.opening_scene

    return dedent(
        f"""
        冷气像细针一样扎进你的喉咙。你本能地咽下一口带着铁锈味的空气，胸腔却被更重的压迫感堵住。掌心有汗，指节发白，耳边是自己过快的心跳。

        {era}，世界表面仍在运转，可缝隙里全是危险。{macro_state}

        你站在{location}，脚下每一步都像踩在将要断裂的薄冰上。{opening_scene}

        你试着稳住呼吸，调动体内那股尚不稳定的力量。它并不听话，像一头被唤醒却不肯驯服的野兽，在血管里撞击。你知道自己不能再退。再退一步，可能就是万劫不复。

        就在你准备做出下一步动作时，危机先一步贴了上来。{looming_crisis}

        风声忽然停了半秒，像有人按下了世界的静音键。下一瞬，一道陌生而危险的视线落在你身上，带着审判般的冷意。你意识到，从这一刻起，你说出的每一个字、做出的每一个动作，都将决定你是活着走出去，还是被这个时代吞没。
        """
    ).strip()


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
