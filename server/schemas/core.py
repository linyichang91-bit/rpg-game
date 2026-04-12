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
    attributes: dict[str, StrictStr] = Field(default_factory=dict)
    damage_types: dict[str, StrictStr] = Field(default_factory=dict)
    item_categories: dict[str, StrictStr] = Field(default_factory=dict)

    @field_validator("stats", "attributes", "damage_types", "item_categories")
    @classmethod
    def validate_glossary_keys(cls, value: dict[str, StrictStr], info: Any) -> dict[str, StrictStr]:
        return _validate_abstract_mapping_keys(value, info.field_name)


class MainQuest(EngineBaseModel):
    """Long-term campaign objective that the current chapter feeds into."""

    quest_id: StrictStr = "quest_main"
    title: StrictStr = "主线目标"
    final_goal: StrictStr = "推进世界观中的最终冲突。"
    summary: StrictStr = "当前世界的长期叙事驱动力。"
    linked_quest_id: StrictStr | None = None
    progress_percent: StrictInt = Field(default=0, ge=0, le=100)

    @field_validator("quest_id", "linked_quest_id")
    @classmethod
    def validate_main_quest_keys(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not ABSTRACT_KEY_PATTERN.fullmatch(value):
            raise ValueError("MainQuest keys must be lowercase abstract identifiers.")
        return value


class CurrentChapter(EngineBaseModel):
    """The currently active chapter beat within the main storyline."""

    chapter_id: StrictStr = "chapter_01"
    title: StrictStr = "第一章"
    objective: StrictStr = "推进当前章节目标。"
    tension_level: StrictInt = Field(default=3, ge=1, le=5)
    progress_percent: StrictInt = Field(default=0, ge=0, le=100)
    linked_quest_id: StrictStr | None = None

    @field_validator("chapter_id", "linked_quest_id")
    @classmethod
    def validate_current_chapter_keys(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not ABSTRACT_KEY_PATTERN.fullmatch(value):
            raise ValueError("CurrentChapter keys must be lowercase abstract identifiers.")
        return value


class StoryMilestone(EngineBaseModel):
    """Important story beat used to expose visible campaign progression."""

    milestone_id: StrictStr
    title: StrictStr
    summary: StrictStr = ""
    is_completed: StrictBool = False
    linked_quest_id: StrictStr | None = None

    @field_validator("milestone_id", "linked_quest_id")
    @classmethod
    def validate_story_milestone_keys(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not ABSTRACT_KEY_PATTERN.fullmatch(value):
            raise ValueError("StoryMilestone keys must be lowercase abstract identifiers.")
        return value


class PowerBenchmark(EngineBaseModel):
    """Example power comparison used to explain setting-specific scaling."""

    subject: StrictStr
    offense_rating: StrictInt = Field(default=0, ge=0)
    defense_rating: StrictInt = Field(default=0, ge=0)
    notes: StrictStr = ""


class PowerTier(EngineBaseModel):
    """A single rank bracket in the world-specific power tier ladder."""

    min_power: StrictInt = Field(default=0, ge=0, description="Minimum power_level to qualify for this tier.")
    label: StrictStr = Field(default="凡人", description="Setting-specific rank label, e.g. 下忍, 准将, 一等咒术师.")


class PowerScaling(EngineBaseModel):
    """World-specific guidance for how power gaps should be interpreted."""

    scale_label: StrictStr = "战力刻度"
    danger_gap_threshold: StrictInt = Field(default=20, ge=0)
    impossible_gap_threshold: StrictInt = Field(default=40, ge=0)
    benchmark_examples: list[PowerBenchmark] = Field(default_factory=list)
    power_tiers: list[PowerTier] = Field(default_factory=list)


class PlayerGrowthState(EngineBaseModel):
    """Persistent growth ledger for player progression and evolution."""

    xp: StrictInt = Field(default=0, ge=0)
    level: StrictInt = Field(default=1, ge=1)
    proficiency_bonus: StrictInt = Field(default=2, ge=0)
    unspent_stat_points: StrictInt = Field(default=0, ge=0)
    last_growth_reason: StrictStr | None = None


class FanficMetaData(EngineBaseModel):
    """Fanfiction-specific metadata anchoring a generated world."""

    base_ip: StrictStr
    universe_type: StrictStr
    tone_and_style: StrictStr


class PlayerCharacterSheet(EngineBaseModel):
    """Structured player character card compiled during world generation."""

    name: StrictStr = "未命名旅者"
    role: StrictStr = "异乡来客"
    summary: StrictStr = "一名被卷入当前故事漩涡的关键人物。"
    objective: StrictStr = "先活下来，再决定如何改写局势。"
    attributes: dict[str, StrictInt] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_character_attribute_keys(
        cls,
        value: dict[str, StrictInt],
    ) -> dict[str, StrictInt]:
        return _validate_abstract_mapping_keys(value, "attributes")


class CampaignContext(EngineBaseModel):
    """Narrative anchor points that lock the campaign to a specific timeline and opening."""

    era_and_timeline: StrictStr = Field(..., description="Exact canon timeline anchor for the campaign.")
    macro_world_state: StrictStr = Field(..., description="High-level description of the world at this moment.")
    looming_crisis: StrictStr = Field(..., description="Primary looming conflict that creates urgency.")
    opening_scene: StrictStr = Field(..., description="Immediate opening scene the prologue must begin inside.")
    main_quest: MainQuest = Field(default_factory=MainQuest)
    current_chapter: CurrentChapter = Field(default_factory=CurrentChapter)
    milestones: list[StoryMilestone] = Field(default_factory=list)


class WorldBook(EngineBaseModel):
    """Lore-book style narrative context attached to the generated world."""

    campaign_context: CampaignContext = Field(
        ...,
        description="Narrative campaign context that anchors the generated world.",
    )
    power_scaling: PowerScaling = Field(default_factory=PowerScaling)


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
    player_character: PlayerCharacterSheet = Field(default_factory=PlayerCharacterSheet)
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
    skills: dict[str, StrictInt] = Field(default_factory=dict)
    skill_labels: dict[str, StrictStr] = Field(default_factory=dict)
    growth: PlayerGrowthState = Field(default_factory=PlayerGrowthState)
    inventory: dict[str, StrictInt] = Field(default_factory=dict)
    temporary_items: dict[str, StrictStr] = Field(default_factory=dict)
    power_level: StrictInt = Field(default=0, ge=0, description="Abstract combat power derived from attributes + level + skills.")
    rank_label: StrictStr = Field(default="未定级", description="Current power tier label mapped from power_tiers in world config.")

    @field_validator("stats", "attributes", "skills", "skill_labels", "inventory", "temporary_items")
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
