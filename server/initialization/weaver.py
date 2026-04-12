"""Fanfiction world weaver for compiling sandbox prompts into WorldConfig."""

from __future__ import annotations

import json
import logging
import re
from textwrap import dedent
from typing import Any, Protocol, get_args, get_origin

from pydantic import BaseModel, ValidationError

from server.llm.config import LLMSettings
from server.llm.json_payload import normalize_json_payload
from server.llm.openai_compatible import OpenAICompatibleJSONClient
from server.llm.retry import run_retryable_json_operation
from server.schemas.core import EngineBaseModel, WorldConfig


DEFAULT_PLAYER_CHARACTER_ATTRIBUTES = {
    "stat_power": 10,
    "stat_agility": 12,
    "stat_insight": 10,
    "stat_tenacity": 12,
    "stat_presence": 10,
}

ABSTRACT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
logger = logging.getLogger("uvicorn.error")


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
        max_validation_retries: int = 2,
    ) -> None:
        self._llm_client = llm_client
        self._narrative_client = narrative_client
        self._max_validation_retries = max_validation_retries

    def generate_world_config(self, fanfic_prompt: str) -> WorldConfig:
        """Generate and validate a WorldConfig from a fanfiction setup prompt."""

        prompt_bundle = build_world_weaver_prompt(fanfic_prompt)
        try:
            return run_retryable_json_operation(
                lambda: self._generate_validated_world_config(prompt_bundle),
                max_attempts=self._max_validation_retries + 1,
                retryable_exceptions=(
                    ValidationError,
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                ),
            )
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "WorldWeaver validation failed after retries: %s",
                _build_world_config_validation_error_message(last_error),
            )
            raise WorldConfigValidationError(
                _build_world_config_validation_error_message(last_error)
            ) from last_error

    def _generate_validated_world_config(
        self,
        prompt_bundle: WorldWeaverPromptBundle,
    ) -> WorldConfig:
        logger.info(
            "WorldWeaver attempt started: prompt_chars=%s",
            len(prompt_bundle.user_prompt),
        )
        raw_response = self._llm_client.generate_json(
            system_prompt=prompt_bundle.system_prompt,
            user_prompt=prompt_bundle.user_prompt,
            response_schema=prompt_bundle.response_schema,
        )
        logger.info(
            "WorldWeaver raw response received: chars=%s preview=%s",
            len(raw_response),
            _log_preview(raw_response),
        )
        normalized_response = normalize_json_payload(raw_response)
        parsed_payload = json.loads(normalized_response)
        if isinstance(parsed_payload, dict):
            world_book_payload = parsed_payload.get("world_book", {})
            campaign_context = (
                world_book_payload.get("campaign_context", {})
                if isinstance(world_book_payload, dict)
                else {}
            )
            if isinstance(campaign_context, dict):
                logger.info(
                    "WorldWeaver raw story field types: main_quest=%s current_chapter=%s milestones=%s",
                    type(campaign_context.get("main_quest")).__name__,
                    type(campaign_context.get("current_chapter")).__name__,
                    type(campaign_context.get("milestones")).__name__,
                )
        normalized_payload = _normalize_world_config_payload(parsed_payload)
        if isinstance(normalized_payload, dict):
            normalized_world_book = normalized_payload.get("world_book", {})
            normalized_campaign_context = (
                normalized_world_book.get("campaign_context", {})
                if isinstance(normalized_world_book, dict)
                else {}
            )
            if isinstance(normalized_campaign_context, dict):
                logger.info(
                    "WorldWeaver normalized storyline: main_quest=%s chapter=%s milestone_count=%s",
                    normalized_campaign_context.get("main_quest", {}).get("title")
                    if isinstance(normalized_campaign_context.get("main_quest"), dict)
                    else None,
                    normalized_campaign_context.get("current_chapter", {}).get("title")
                    if isinstance(normalized_campaign_context.get("current_chapter"), dict)
                    else None,
                    len(normalized_campaign_context.get("milestones", []))
                    if isinstance(normalized_campaign_context.get("milestones"), list)
                    else 0,
                )
        return WorldConfig.model_validate(normalized_payload)

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
        4. Build player_character from the requested character card and assign numeric core attributes.
        5. Build a world glossary that maps engine abstract keys into setting-specific terms.
        6. Preload a fitting starting_location, 1-3 key_npcs, and opening initial_quests.
        7. Populate world_book.campaign_context with timeline-locked lore anchors.
        8. Add world_book.campaign_context.main_quest, current_chapter, and milestones.
        9. Add world_book.power_scaling so later scenes can judge impossible power gaps.
        10. Produce engine-facing JSON only. Do not write explanatory text outside the JSON object.

        Lore generation rules for world_book.campaign_context:
        - You must anchor the setting to the exact requested era and timeline.
        - era_and_timeline must name a precise canon era, arc, year, or timeline node.
        - macro_world_state must reflect only factions, institutions, and NPCs who should exist at that time.
        - Never introduce future characters early, never revive dead characters, and never collapse distant eras together.
        - opening_scene must be vivid and immediate: include a concrete physical location, active motion, sensory detail,
          and one urgent hook that forces the player to react.
        - main_quest should describe the campaign's long-term objective in setting language.
        - current_chapter should describe the immediate goal and feel like the next playable arc.
        - milestones should expose 2-4 visible beats the player can later progress through.
        - power_scaling must include a clear impossible gap threshold and at least three benchmark examples.
        - power_scaling.power_tiers must define 5-8 setting-specific rank brackets with ascending min_power values.
          Example for a ninja world: [{min_power:0, label:"下忍"}, {min_power:20, label:"中忍"}, {min_power:45, label:"上忍"}, {min_power:70, label:"影级"}, {min_power:100, label:"六道级"}].
          The labels should use the setting's native terminology. min_power values should create reasonable progression gaps.
        - player_character should reflect the requested role card, tone, and personal drive.
        - player_character.objective should align with what the player wants to achieve.
        - player_character.attributes must include stat_power, stat_agility, stat_insight,
          stat_tenacity, and stat_presence as integer scores, usually in the 6-18 range.

        Glossary minimums:
        - stats should include at least stat_hp and stat_mp when the setting has a spiritual, magical, psychic, or energy resource.
        - attributes should map stat_power, stat_agility, stat_insight, stat_tenacity, and stat_presence.
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
        - player_character
        - world_book.campaign_context.era_and_timeline
        - world_book.campaign_context.macro_world_state
        - world_book.campaign_context.looming_crisis
        - world_book.campaign_context.opening_scene
        - world_book.campaign_context.main_quest
        - world_book.campaign_context.current_chapter
        - world_book.campaign_context.milestones
        - world_book.power_scaling
        - glossary.stats
        - glossary.attributes
        - glossary.damage_types
        - glossary.item_categories
        - starting_location
        - key_npcs
        - initial_quests
        - mechanics

        Abstract keys that should be considered for glossary mapping:
        - stats: stat_hp, stat_mp
        - attributes: stat_power, stat_agility, stat_insight, stat_tenacity, stat_presence
        - player_character.attributes: stat_power, stat_agility, stat_insight, stat_tenacity, stat_presence
        - damage_types: dmg_kinetic, dmg_energy
        - item_categories: item_weapon

        Language requirements:
        - theme, fanfic_meta.universe_type, fanfic_meta.tone_and_style, player_character,
          world_book.campaign_context, glossary values, starting_location, key_npcs,
          and initial_quests should use Simplified Chinese.
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
    player_character = world_config.player_character
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
        - 玩家角色: {player_character.name} / {player_character.role}
        - 角色概况: {player_character.summary}
        - 角色目标: {player_character.objective}
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
    normalized["player_character"] = _normalize_player_character_payload(
        normalized.get("player_character"),
        normalized,
    )
    normalized["glossary"] = _normalize_glossary_payload(normalized.get("glossary"))
    normalized["world_book"] = _normalize_world_book_payload(
        normalized.get("world_book"),
        normalized,
    )
    return _prune_to_model_schema(normalized, WorldConfig)


def _normalize_glossary_payload(value: Any) -> dict[str, Any]:
    glossary = dict(value) if isinstance(value, dict) else {}
    glossary.setdefault(
        "attributes",
        {
            "stat_power": "力量",
            "stat_agility": "敏捷",
            "stat_insight": "洞察",
            "stat_tenacity": "韧性",
            "stat_presence": "魅力",
        },
    )
    return glossary


def _normalize_player_character_payload(
    value: Any,
    root_payload: dict[str, Any],
) -> dict[str, Any]:
    player_character = dict(value) if isinstance(value, dict) else {}
    theme = _coerce_string_value(root_payload.get("theme"), preferred_keys=("name",))

    player_character["name"] = _coerce_string_value(
        player_character.get("name"),
        preferred_keys=("player_name", "character_name"),
    ) or "未命名旅者"
    player_character["role"] = _coerce_string_value(
        player_character.get("role"),
        preferred_keys=("identity", "class", "archetype"),
    ) or "异乡来客"
    player_character["summary"] = _coerce_string_value(
        player_character.get("summary"),
        preferred_keys=("background", "description", "bio"),
    ) or f"在{theme or '当前世界'}中被卷入风暴中心的关键角色。"
    player_character["objective"] = _coerce_string_value(
        player_character.get("objective"),
        preferred_keys=("goal", "motivation", "desire"),
    ) or "先活下来，再决定如何改写局势。"
    player_character["attributes"] = _normalize_player_attribute_values(
        player_character.get("attributes")
    )
    return player_character


def _normalize_main_quest_payload(
    value: Any,
    *,
    theme: Any,
    looming_crisis: Any,
) -> dict[str, Any]:
    payload = dict(value) if isinstance(value, dict) else {}
    if isinstance(value, list):
        first_text = next(
            (str(item).strip() for item in value if str(item).strip()),
            "",
        )
        if first_text:
            payload.setdefault("title", first_text)
    if isinstance(value, str) and value.strip():
        payload.setdefault("title", value.strip())

    title = _coerce_string_value(
        payload.get("title"),
        preferred_keys=("name", "quest_name", "objective", "goal", "summary"),
    ) or "主线目标"
    final_goal = _coerce_string_value(
        payload.get("final_goal"),
        preferred_keys=("goal", "objective", "target", "summary", "description"),
    ) or looming_crisis or f"围绕{theme or '当前世界'}解决最终危机。"
    summary = _coerce_string_value(
        payload.get("summary"),
        preferred_keys=("description", "details", "objective"),
    ) or looming_crisis or "当前世界的长期叙事驱动力。"

    return {
        "quest_id": _coerce_abstract_key_value(
            payload.get("quest_id"),
            fallback="quest_main",
        ),
        "title": title,
        "final_goal": final_goal,
        "summary": summary,
        "linked_quest_id": _coerce_abstract_key_value(payload.get("linked_quest_id")),
        "progress_percent": _clamp_int(
            _coerce_int_value(payload.get("progress_percent")),
            minimum=0,
            maximum=100,
            fallback=0,
        ),
    }


def _normalize_current_chapter_payload(
    value: Any,
    *,
    opening_scene: Any,
    looming_crisis: Any,
) -> dict[str, Any]:
    payload = dict(value) if isinstance(value, dict) else {}
    if isinstance(value, list):
        first_text = next(
            (str(item).strip() for item in value if str(item).strip()),
            "",
        )
        if first_text:
            payload.setdefault("objective", first_text)
    if isinstance(value, str) and value.strip():
        payload.setdefault("objective", value.strip())

    title = _coerce_string_value(
        payload.get("title"),
        preferred_keys=("name", "chapter_name", "label"),
    ) or "第一章"
    objective = _coerce_string_value(
        payload.get("objective"),
        preferred_keys=("goal", "summary", "description", "title"),
    ) or opening_scene or looming_crisis or "推进当前章节目标。"

    return {
        "chapter_id": _coerce_abstract_key_value(
            payload.get("chapter_id"),
            fallback="chapter_01",
        ),
        "title": title,
        "objective": objective,
        "tension_level": _clamp_int(
            _coerce_int_value(payload.get("tension_level")),
            minimum=1,
            maximum=5,
            fallback=3 if looming_crisis else 2,
        ),
        "progress_percent": _clamp_int(
            _coerce_int_value(payload.get("progress_percent")),
            minimum=0,
            maximum=100,
            fallback=0,
        ),
        "linked_quest_id": _coerce_abstract_key_value(payload.get("linked_quest_id")),
    }


def _normalize_milestones_payload(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        items = [value]
    elif isinstance(value, str) and value.strip():
        items = [value.strip()]
    else:
        items = []

    return [
        _normalize_milestone_payload(item, index=index)
        for index, item in enumerate(items, start=1)
    ]


def _normalize_milestone_payload(value: Any, *, index: int) -> dict[str, Any]:
    payload = dict(value) if isinstance(value, dict) else {}
    if isinstance(value, str) and value.strip():
        payload.setdefault("title", value.strip())

    title = _coerce_string_value(
        payload.get("title"),
        preferred_keys=("name", "milestone_name", "objective", "summary", "description"),
    ) or f"里程碑 {index}"
    summary = _coerce_string_value(
        payload.get("summary"),
        preferred_keys=("description", "objective", "details"),
    ) or ""

    return {
        "milestone_id": _coerce_abstract_key_value(
            payload.get("milestone_id"),
            fallback=f"milestone_{index:02d}",
        ),
        "title": title,
        "summary": summary,
        "is_completed": _coerce_bool_value(payload.get("is_completed"), default=False),
        "linked_quest_id": _coerce_abstract_key_value(payload.get("linked_quest_id")),
    }


def _default_milestones() -> list[dict[str, Any]]:
    return [
        {
            "milestone_id": "milestone_01",
            "title": "察觉危机",
            "summary": "从开场异动中识别当前冲突。",
            "is_completed": False,
        },
        {
            "milestone_id": "milestone_02",
            "title": "稳住局势",
            "summary": "完成第一阶段应对并维持局面不崩盘。",
            "is_completed": False,
        },
        {
            "milestone_id": "milestone_03",
            "title": "逼近真相",
            "summary": "把眼前危机和更大的主线连接起来。",
            "is_completed": False,
        },
    ]


_DEFAULT_POWER_SCALING: dict[str, Any] = {
    "scale_label": "universal_power_curve",
    "danger_gap_threshold": 20,
    "impossible_gap_threshold": 40,
    "benchmark_examples": [
        {
            "subject": "普通对手",
            "offense_rating": 10,
            "defense_rating": 10,
            "notes": "常规威胁，适合基础行动。",
        },
        {
            "subject": "精英敌人",
            "offense_rating": 40,
            "defense_rating": 40,
            "notes": "需要准备、克制与团队配合。",
        },
        {
            "subject": "顶尖强者",
            "offense_rating": 80,
            "defense_rating": 80,
            "notes": "通常只有重大成长后才有正面交锋空间。",
        },
    ],
    "power_tiers": [
        {"min_power": 0, "label": "凡人"},
        {"min_power": 15, "label": "入门"},
        {"min_power": 30, "label": "熟练"},
        {"min_power": 50, "label": "精英"},
        {"min_power": 75, "label": "大师"},
        {"min_power": 100, "label": "传说"},
    ],
}


def _normalize_power_scaling_payload(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(_DEFAULT_POWER_SCALING)

    scale_label = _coerce_string_value(value.get("scale_label"), preferred_keys=("name",)) or _DEFAULT_POWER_SCALING["scale_label"]
    danger_gap = _clamp_int(
        _coerce_int_value(value.get("danger_gap_threshold")),
        minimum=0,
        maximum=999,
        fallback=_DEFAULT_POWER_SCALING["danger_gap_threshold"],
    )
    impossible_gap = _clamp_int(
        _coerce_int_value(value.get("impossible_gap_threshold")),
        minimum=0,
        maximum=999,
        fallback=_DEFAULT_POWER_SCALING["impossible_gap_threshold"],
    )

    benchmark_examples: list[dict[str, Any]] = []
    raw_benchmarks = value.get("benchmark_examples")
    if isinstance(raw_benchmarks, list):
        for item in raw_benchmarks:
            if not isinstance(item, dict):
                continue
            subject = _coerce_string_value(item.get("subject"), preferred_keys=("name",))
            if not subject:
                continue
            benchmark_examples.append({
                "subject": subject,
                "offense_rating": _clamp_int(
                    _coerce_int_value(item.get("offense_rating")),
                    minimum=0,
                    maximum=999,
                    fallback=0,
                ),
                "defense_rating": _clamp_int(
                    _coerce_int_value(item.get("defense_rating")),
                    minimum=0,
                    maximum=999,
                    fallback=0,
                ),
                "notes": _coerce_string_value(item.get("notes"), preferred_keys=()) or "",
            })

    if not benchmark_examples:
        benchmark_examples = _DEFAULT_POWER_SCALING["benchmark_examples"]

    # Normalize power_tiers
    raw_power_tiers = value.get("power_tiers")
    power_tiers: list[dict[str, Any]] = []
    if isinstance(raw_power_tiers, list):
        for item in raw_power_tiers:
            if not isinstance(item, dict):
                continue
            min_power = _clamp_int(
                _coerce_int_value(item.get("min_power")),
                minimum=0,
                maximum=9999,
                fallback=0,
            )
            label = _coerce_string_value(item.get("label"), preferred_keys=("name", "rank", "title")) or ""
            if not label:
                continue
            power_tiers.append({
                "min_power": min_power,
                "label": label,
            })
    if not power_tiers:
        power_tiers = _DEFAULT_POWER_SCALING["power_tiers"]

    return {
        "scale_label": scale_label,
        "danger_gap_threshold": danger_gap,
        "impossible_gap_threshold": impossible_gap,
        "benchmark_examples": benchmark_examples,
        "power_tiers": power_tiers,
    }


def _normalize_world_book_payload(
    value: Any,
    root_payload: dict[str, Any],
) -> dict[str, Any]:
    world_book = dict(value) if isinstance(value, dict) else {}
    campaign_context = dict(world_book.get("campaign_context")) if isinstance(world_book.get("campaign_context"), dict) else {}

    theme = _coerce_string_value(root_payload.get("theme"), preferred_keys=("name",))
    looming_crisis = _coerce_string_value(campaign_context.get("looming_crisis"), preferred_keys=())
    opening_scene = _coerce_string_value(campaign_context.get("opening_scene"), preferred_keys=())
    macro_world_state = _coerce_string_value(campaign_context.get("macro_world_state"), preferred_keys=())
    era_and_timeline = _coerce_string_value(campaign_context.get("era_and_timeline"), preferred_keys=())

    campaign_context["main_quest"] = _normalize_main_quest_payload(
        campaign_context.get("main_quest"),
        theme=theme,
        looming_crisis=looming_crisis,
    )
    campaign_context["current_chapter"] = _normalize_current_chapter_payload(
        campaign_context.get("current_chapter"),
        opening_scene=opening_scene,
        looming_crisis=looming_crisis,
    )
    campaign_context["milestones"] = _normalize_milestones_payload(
        campaign_context.get("milestones")
    )
    if not campaign_context["milestones"]:
        campaign_context["milestones"] = _default_milestones()

    campaign_context["era_and_timeline"] = era_and_timeline or "未知时代节点"
    campaign_context["macro_world_state"] = macro_world_state or "宏观局势尚待展开。"
    campaign_context["looming_crisis"] = looming_crisis or "当前危机尚未完全揭示。"
    campaign_context["opening_scene"] = opening_scene or "故事从一个临界时刻开始。"
    world_book["campaign_context"] = campaign_context
    world_book["power_scaling"] = _normalize_power_scaling_payload(
        world_book.get("power_scaling"),
    )
    return world_book


def _normalize_player_attribute_values(value: Any) -> dict[str, int]:
    attributes: dict[str, int] = {}

    if isinstance(value, dict):
        for key, raw_value in value.items():
            normalized_key = str(key).strip()
            normalized_value = _coerce_int_value(raw_value)
            if normalized_key and normalized_value is not None:
                attributes[normalized_key] = max(1, min(20, normalized_value))

    for key, default_value in DEFAULT_PLAYER_CHARACTER_ATTRIBUTES.items():
        attributes.setdefault(key, default_value)

    return attributes


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


def _coerce_int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _coerce_bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return default


def _coerce_abstract_key_value(value: Any, *, fallback: str | None = None) -> str | None:
    if not isinstance(value, str):
        return fallback

    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    if normalized and ABSTRACT_KEY_PATTERN.fullmatch(normalized):
        return normalized
    return fallback


def _clamp_int(
    value: int | None,
    *,
    minimum: int,
    maximum: int,
    fallback: int,
) -> int:
    if value is None:
        return fallback
    return max(minimum, min(maximum, value))


def _log_preview(value: Any, *, limit: int = 240) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."
