"""Hybrid loot candidate generation with strict JSON validation."""

from __future__ import annotations

import json
import re
from textwrap import dedent
from typing import Any, Callable, Protocol

from pydantic import Field, ValidationError, field_validator

from server.llm.config import LLMSettings
from server.llm.json_payload import normalize_json_payload
from server.llm.openai_compatible import OpenAICompatibleJSONClient
from server.schemas.core import ABSTRACT_KEY_PATTERN, EngineBaseModel, WorldConfig


TempKeyFactory = Callable[[], str]


class LootCandidate(EngineBaseModel):
    """A single loot candidate that may be awarded by the runtime pipeline."""

    temp_key: str
    name: str
    dc: int = Field(..., ge=1, le=20)
    type: str

    @field_validator("temp_key", "type")
    @classmethod
    def validate_abstract_keys(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not ABSTRACT_KEY_PATTERN.fullmatch(normalized):
            raise ValueError(f"Invalid abstract key '{value}'.")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Loot candidate name cannot be empty.")
        return normalized


class LootPool(EngineBaseModel):
    """Candidate pool returned by the loot generator."""

    candidates: list[LootCandidate] = Field(default_factory=list)


class LootPromptBundle(EngineBaseModel):
    """Prompt payload sent to the structured loot generator."""

    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any]


class StructuredJSONClient(Protocol):
    """Provider-agnostic JSON generation boundary for loot candidates."""

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> str:
        """Generate a JSON payload that matches the supplied schema."""


class LootGenerator:
    """Generate world-appropriate loot candidates without deciding final drops."""

    def __init__(
        self,
        llm_client: StructuredJSONClient,
        *,
        max_validation_retries: int = 1,
    ) -> None:
        self._llm_client = llm_client
        self._max_validation_retries = max_validation_retries

    def generate_pool(
        self,
        *,
        world_config: WorldConfig,
        target_name: str,
        user_input: str,
        temp_key_factory: TempKeyFactory,
    ) -> LootPool:
        """Return a validated loot pool, falling back to deterministic candidates if needed."""

        prompt_bundle = build_loot_prompt(
            world_config=world_config,
            target_name=target_name,
            user_input=user_input,
        )
        last_error: Exception | None = None

        for _ in range(self._max_validation_retries + 1):
            try:
                raw_response = self._llm_client.generate_json(
                    system_prompt=prompt_bundle.system_prompt,
                    user_prompt=prompt_bundle.user_prompt,
                    response_schema=prompt_bundle.response_schema,
                )
                normalized_payload = _normalize_loot_payload(
                    json.loads(normalize_json_payload(raw_response)),
                    temp_key_factory=temp_key_factory,
                )
                loot_pool = LootPool.model_validate(normalized_payload)
                if loot_pool.candidates:
                    return loot_pool
            except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = exc

        return _build_fallback_loot_pool(
            world_config=world_config,
            target_name=target_name,
            temp_key_factory=temp_key_factory,
            _last_error=last_error,
        )


def build_loot_generator_from_env(*, env_file: str = ".env") -> LootGenerator:
    """Create a loot generator from environment-backed LLM settings."""

    settings = LLMSettings.from_env(env_file=env_file)
    llm_client = OpenAICompatibleJSONClient.from_settings(settings)
    return LootGenerator(llm_client)


def build_loot_prompt(
    *,
    world_config: WorldConfig,
    target_name: str,
    user_input: str,
) -> LootPromptBundle:
    """Assemble the strict prompt for generating a candidate loot pool."""

    system_prompt = dedent(
        f"""
        你是一个掉落候选池生成器。
        当前世界主题是：{world_config.theme}

        你的任务只有一个：
        根据世界设定与当前搜刮目标，输出一个“可能存在的候选物品池”JSON。

        强约束：
        1. 你绝对不能决定玩家最终获得了什么。
        2. 你只能生成 candidates 候选列表，真正是否掉落由后端代码掷骰结算。
        3. 你必须使用简体中文填写物品名 name。
        4. type 必须使用抽象 key，例如 item_material、item_junk、item_consumable、item_weapon、item_clue。
        5. dc 是 1-20 的整数，数值越高越难搜到。
        6. 只输出 JSON，不要输出任何解释、前言或 Markdown。
        7. 候选数量控制在 2 到 4 个之间。
        """
    ).strip()

    user_prompt = dedent(
        f"""
        玩家原话：
        {user_input}

        搜刮目标：
        {target_name}

        世界配置摘要：
        {world_config.model_dump_json(indent=2)}
        """
    ).strip()

    return LootPromptBundle(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=LootPool.model_json_schema(),
    )


def _normalize_loot_payload(
    payload: Any,
    *,
    temp_key_factory: TempKeyFactory,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("Loot payload must be a JSON object.")

    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise TypeError("Loot payload must contain a candidates list.")

    normalized_candidates: list[dict[str, Any]] = []
    for raw_candidate in raw_candidates[:4]:
        if not isinstance(raw_candidate, dict):
            continue

        name = _coerce_name(raw_candidate)
        if name is None:
            continue

        normalized_candidates.append(
            {
                "temp_key": temp_key_factory(),
                "name": name,
                "dc": _coerce_dc(raw_candidate.get("dc")),
                "type": _coerce_type(raw_candidate.get("type")),
            }
        )

    if not normalized_candidates:
        raise ValueError("Loot payload did not contain any usable candidates.")

    return {"candidates": normalized_candidates}


def _coerce_name(candidate: dict[str, Any]) -> str | None:
    for key in ("name", "item_name", "label", "title"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _coerce_dc(value: Any) -> int:
    try:
        dc = int(value)
    except (TypeError, ValueError):
        dc = 10
    return min(20, max(1, dc))


def _coerce_type(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if normalized and ABSTRACT_KEY_PATTERN.fullmatch(normalized):
            return normalized
    return "item_material"


def _build_fallback_loot_pool(
    *,
    world_config: WorldConfig,
    target_name: str,
    temp_key_factory: TempKeyFactory,
    _last_error: Exception | None = None,
) -> LootPool:
    del _last_error

    target = target_name.lower()
    base_ip = world_config.fanfic_meta.base_ip.lower()

    if any(token in target for token in ("尸体", "残骸", "corpse", "body", "remains")):
        names = _fallback_corpse_candidates(base_ip)
    elif any(token in target for token in ("箱", "柜", "抽屉", "宝箱", "desk", "locker", "chest")):
        names = _fallback_container_candidates(base_ip)
    else:
        names = _fallback_environment_candidates(base_ip)

    candidates = [
        LootCandidate(
            temp_key=temp_key_factory(),
            name=name,
            dc=dc,
            type=item_type,
        )
        for name, dc, item_type in names
    ]
    return LootPool(candidates=candidates)


def _fallback_corpse_candidates(base_ip: str) -> list[tuple[str, int, str]]:
    if "harry potter" in base_ip or "哈利" in base_ip:
        return [
            ("沾灰的魔杖碎片", 7, "item_material"),
            ("食死徒徽记纽扣", 11, "item_clue"),
            ("残留魔力的飞路粉小袋", 16, "item_consumable"),
        ]
    if "naruto" in base_ip or "火影" in base_ip:
        return [
            ("磨损的苦无", 6, "item_weapon"),
            ("染血的护额碎片", 10, "item_clue"),
            ("残缺起爆符", 15, "item_consumable"),
        ]
    if "咒术" in base_ip or "jujutsu" in base_ip:
        return [
            ("沾染咒力的制服纽扣", 8, "item_material"),
            ("干瘪的咒物残片", 16, "item_material"),
            ("裂开的封印纸", 11, "item_clue"),
        ]

    return [
        ("染血的身份牌", 7, "item_clue"),
        ("尚可使用的补给包", 12, "item_consumable"),
        ("难以辨认的怪异碎片", 17, "item_material"),
    ]


def _fallback_container_candidates(base_ip: str) -> list[tuple[str, int, str]]:
    if "harry potter" in base_ip or "哈利" in base_ip:
        return [
            ("一卷发霉的魔法笔记", 7, "item_clue"),
            ("药剂残液小瓶", 12, "item_consumable"),
            ("银丝镶边的旧钥匙", 16, "item_material"),
        ]
    if "naruto" in base_ip or "火影" in base_ip:
        return [
            ("备用兵粮丸", 6, "item_consumable"),
            ("记录暗号的纸条", 11, "item_clue"),
            ("小型苦无束", 15, "item_weapon"),
        ]

    return [
        ("沾灰的工具盒", 7, "item_material"),
        ("被人藏起的便携药剂", 11, "item_consumable"),
        ("刻着编号的金属铭牌", 16, "item_clue"),
    ]


def _fallback_environment_candidates(base_ip: str) -> list[tuple[str, int, str]]:
    if "harry potter" in base_ip or "哈利" in base_ip:
        return [
            ("掉在地上的铜纳特", 5, "item_junk"),
            ("残留余温的咒语练习纸", 10, "item_clue"),
            ("一小撮会发光的粉末", 16, "item_material"),
        ]
    if "naruto" in base_ip or "火影" in base_ip:
        return [
            ("磨旧的绷带", 5, "item_consumable"),
            ("半截断裂的手里剑", 10, "item_weapon"),
            ("记录路线的泥水纸片", 15, "item_clue"),
        ]

    return [
        ("积灰的零件", 6, "item_material"),
        ("被踩扁的补给罐", 11, "item_junk"),
        ("藏着线索的破纸角", 16, "item_clue"),
    ]
