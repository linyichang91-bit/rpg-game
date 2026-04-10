"""Core Pydantic schemas for the narrative world engine."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator


ABSTRACT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
TARGET_PATH_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


def _validate_abstract_mapping_keys(value: dict[str, Any], field_name: str) -> dict[str, Any]:
    for key in value:
        if not ABSTRACT_KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"{field_name} contains an invalid abstract key '{key}'. "
                "Only lowercase abstract identifiers are allowed."
            )
    return value


def _validate_abstract_key_list(values: list[str], field_name: str) -> list[str]:
    for value in values:
        if not ABSTRACT_KEY_PATTERN.fullmatch(value):
            raise ValueError(
                f"{field_name} contains an invalid abstract key '{value}'. "
                "Only lowercase abstract identifiers are allowed."
            )
    return values


class EngineBaseModel(BaseModel):
    """Shared strict model defaults for all core schemas."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class WorldGlossary(EngineBaseModel):
    """Maps engine-level abstract keys to world-specific presentation terms."""

    stats: dict[str, StrictStr] = Field(default_factory=dict)
    damage_types: dict[str, StrictStr] = Field(default_factory=dict)
    item_categories: dict[str, StrictStr] = Field(default_factory=dict)

    @field_validator("stats", "damage_types", "item_categories")
    @classmethod
    def validate_glossary_keys(cls, value: dict[str, StrictStr], info: Any) -> dict[str, StrictStr]:
        return _validate_abstract_mapping_keys(value, info.field_name)


class FanficMetaData(EngineBaseModel):
    """Fanfiction-specific metadata anchoring a generated world."""

    base_ip: StrictStr
    universe_type: StrictStr
    tone_and_style: StrictStr


class CampaignContext(EngineBaseModel):
    """Narrative anchor points that lock the campaign to a specific timeline and opening."""

    era_and_timeline: StrictStr = Field(
        ...,
        description=(
            "精确的时代与时间线节点。例如：'木叶60年，中忍考试前夕' "
            "或 '掠夺者时代，1971年9月'"
        ),
    )
    macro_world_state: StrictStr = Field(
        ...,
        description=(
            "当前世界的宏观局势与常识。例如：'大蛇丸正暗中谋划崩溃木叶，各村表面和平但暗流涌动' "
            "或 '伏地魔势力初现，魔法界人心惶惶'"
        ),
    )
    looming_crisis: StrictStr = Field(
        ...,
        description=(
            "悬在主角头顶的终极危机或主线阴影，用于制造紧迫感。"
            "例如：'距离中忍考试死亡森林篇只剩3天，必须尽快提升实力'"
        ),
    )
    opening_scene: StrictStr = Field(
        ...,
        description=(
            "【第一章特化】开局的具体地点、感官细节与突发事件。"
            "例如：'玩家在木叶忍者学校的阴暗走廊醒来，手里死死攥着一张不及格的忍术试卷，"
            "窗外突然传来巨大的爆炸声。'"
        ),
    )


class WorldBook(EngineBaseModel):
    """Lore-book style narrative context attached to the generated world."""

    campaign_context: CampaignContext = Field(
        ...,
        description="该同人宇宙的叙事大纲与时空背景。",
    )


class WorldNode(EngineBaseModel):
    """A single discoverable location in the runtime topology graph."""

    node_id: StrictStr
    title: StrictStr
    base_desc: StrictStr
    hidden_detail_dc10: StrictStr | None = None
    deep_secret_dc18: StrictStr | None = None
    tags: list[StrictStr] = Field(default_factory=list)

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        if not ABSTRACT_KEY_PATTERN.fullmatch(value):
            raise ValueError("WorldNode.node_id must be a lowercase abstract key.")
        return value


class WorldTopology(EngineBaseModel):
    """Directed runtime graph of discovered world locations."""

    start_node_id: StrictStr = "location_start"
    nodes: dict[str, WorldNode] = Field(default_factory=dict)
    edges: dict[str, list[StrictStr]] = Field(default_factory=dict)

    @field_validator("start_node_id")
    @classmethod
    def validate_start_node_id(cls, value: str) -> str:
        if not ABSTRACT_KEY_PATTERN.fullmatch(value):
            raise ValueError("WorldTopology.start_node_id must be a lowercase abstract key.")
        return value

    @field_validator("nodes")
    @classmethod
    def validate_node_keys(
        cls,
        value: dict[str, WorldNode],
    ) -> dict[str, WorldNode]:
        return _validate_abstract_mapping_keys(value, "nodes")

    @field_validator("edges")
    @classmethod
    def validate_edge_keys(
        cls,
        value: dict[str, list[StrictStr]],
    ) -> dict[str, list[StrictStr]]:
        _validate_abstract_mapping_keys(value, "edges")
        for edge_key, targets in value.items():
            _validate_abstract_key_list(targets, f"edges[{edge_key}]")
        return value


class WorldConfig(EngineBaseModel):
    """Configuration bundle that defines a single playable world."""

    world_id: StrictStr
    theme: StrictStr
    fanfic_meta: FanficMetaData
    world_book: WorldBook
    glossary: WorldGlossary
    starting_location: StrictStr
    key_npcs: list[StrictStr] = Field(default_factory=list)
    initial_quests: list[StrictStr] = Field(default_factory=list)
    mechanics: dict[str, Any] = Field(default_factory=dict)
    topology: WorldTopology = Field(default_factory=WorldTopology)


class PlayerState(EngineBaseModel):
    """Player-facing runtime state using abstract engine keys only."""

    stats: dict[str, StrictInt] = Field(default_factory=dict)
    attributes: dict[str, StrictInt] = Field(default_factory=dict)
    inventory: dict[str, StrictInt] = Field(default_factory=dict)
    temporary_items: dict[str, StrictStr] = Field(default_factory=dict)

    @field_validator("stats", "attributes", "inventory", "temporary_items")
    @classmethod
    def validate_state_keys(cls, value: dict[str, Any], info: Any) -> dict[str, Any]:
        return _validate_abstract_mapping_keys(value, info.field_name)


class RuntimeEntityState(EngineBaseModel):
    """Encounter-addressable entity state for combat and other pipelines."""

    stats: dict[str, StrictInt] = Field(default_factory=dict)
    attributes: dict[str, StrictInt] = Field(default_factory=dict)
    tags: list[StrictStr] = Field(default_factory=list)

    @field_validator("stats", "attributes")
    @classmethod
    def validate_entity_keys(cls, value: dict[str, Any], info: Any) -> dict[str, Any]:
        return _validate_abstract_mapping_keys(value, info.field_name)


class ContextEntity(EngineBaseModel):
    """Nearby entity details exposed to the GM agent for target resolution."""

    entity_id: StrictStr
    display_name: StrictStr
    entity_type: StrictStr
    summary: StrictStr | None = None


QuestStatus = Literal["active", "completed", "failed"]
EncounterStatus = Literal["active", "resolved", "escaped"]


class QuestState(EngineBaseModel):
    """Runtime quest tracked independently from static world generation seeds."""

    quest_id: StrictStr
    title: StrictStr
    status: QuestStatus = "active"
    summary: StrictStr | None = None
    progress: StrictInt = 0

    @field_validator("quest_id")
    @classmethod
    def validate_quest_id(cls, value: str) -> str:
        if not ABSTRACT_KEY_PATTERN.fullmatch(value):
            raise ValueError("QuestState.quest_id must be a lowercase abstract key.")
        return value


class EncounterState(EngineBaseModel):
    """Persistent record describing an encounter beyond the live enemy map."""

    encounter_id: StrictStr
    label: StrictStr
    status: EncounterStatus = "active"
    location_id: StrictStr
    enemy_ids: list[StrictStr] = Field(default_factory=list)
    summary: StrictStr | None = None

    @field_validator("encounter_id", "location_id")
    @classmethod
    def validate_encounter_keys(cls, value: str) -> str:
        if not ABSTRACT_KEY_PATTERN.fullmatch(value):
            raise ValueError("EncounterState keys must be lowercase abstract identifiers.")
        return value

    @field_validator("enemy_ids")
    @classmethod
    def validate_enemy_ids(cls, value: list[str]) -> list[str]:
        return _validate_abstract_key_list(value, "enemy_ids")


class GameState(EngineBaseModel):
    """Single source of truth snapshot for the current session."""

    session_id: StrictStr
    player: PlayerState
    current_location_id: StrictStr
    active_encounter: StrictStr | None = None
    encounter_entities: dict[str, RuntimeEntityState] = Field(default_factory=dict)
    quest_log: dict[str, QuestState] = Field(default_factory=dict)
    encounter_log: dict[str, EncounterState] = Field(default_factory=dict)
    world_config: WorldConfig

    @field_validator("encounter_entities", "quest_log", "encounter_log")
    @classmethod
    def validate_runtime_mapping_keys(
        cls,
        value: dict[str, Any],
        info: Any,
    ) -> dict[str, Any]:
        return _validate_abstract_mapping_keys(value, info.field_name)


MutationAction = Literal["add", "subtract", "set", "delete", "append"]


class MutationLog(EngineBaseModel):
    """Atomic mutation request applied by the state mutator."""

    action: MutationAction
    target_path: StrictStr
    value: Any
    reason: StrictStr

    @field_validator("target_path")
    @classmethod
    def validate_target_path(cls, value: str) -> str:
        if not TARGET_PATH_PATTERN.fullmatch(value):
            raise ValueError(
                "target_path must be an abstract path such as "
                "'current_location_id' or 'player.stats.stat_hp'."
            )
        return value


class ExecutedEvent(EngineBaseModel):
    """Objective runtime facts emitted by tools and preserved for audit."""

    event_type: StrictStr
    is_success: StrictBool
    actor: StrictStr
    target: StrictStr
    abstract_action: StrictStr
    result_tags: list[StrictStr] = Field(default_factory=list)
