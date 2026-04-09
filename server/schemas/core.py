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


class GameState(EngineBaseModel):
    """Single source of truth snapshot for the current session."""

    session_id: StrictStr
    player: PlayerState
    current_location_id: StrictStr
    active_encounter: StrictStr | None = None
    encounter_entities: dict[str, RuntimeEntityState] = Field(default_factory=dict)
    world_config: WorldConfig

    @field_validator("encounter_entities")
    @classmethod
    def validate_encounter_entity_keys(
        cls,
        value: dict[str, RuntimeEntityState],
    ) -> dict[str, RuntimeEntityState]:
        return _validate_abstract_mapping_keys(value, "encounter_entities")


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
    """Objective runtime facts passed to the narrator layer."""

    event_type: StrictStr
    is_success: StrictBool
    actor: StrictStr
    target: StrictStr
    abstract_action: StrictStr
    result_tags: list[StrictStr] = Field(default_factory=list)
