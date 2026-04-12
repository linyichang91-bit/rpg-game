"""Runtime tool registry and execution helpers for the GM agent."""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from server.generators.loot_generator import LootGenerator, build_loot_generator_from_env
from server.generators.map_generator import DynamicMapGenerator, build_map_generator_from_env
from server.pipelines.growth import resolve_growth
from server.pipelines.combat import resolve_combat
from server.pipelines.exploration import resolve_exploration
from server.pipelines.loot import resolve_loot
from server.runtime.session_store import SessionRecord
from server.runtime.power_level import recalculate_power_and_rank
from server.schemas.core import ExecutedEvent, MutationLog, WorldNode
from server.state.mutator import apply_mutations


DEFAULT_HP_STAT_KEY = "stat_hp"
DEFAULT_MP_STAT_KEY = "stat_mp"


@dataclass
class ToolExecutionResult:
    """Structured output returned by a runtime tool execution."""

    observation: dict[str, Any]
    executed_events: list[ExecutedEvent]
    mutation_logs: list[MutationLog]


def clone_session_record(record: SessionRecord) -> SessionRecord:
    """Create an isolated working copy for one agent turn."""

    return deepcopy(record)


def commit_session_record(source: SessionRecord, destination: SessionRecord) -> None:
    """Copy the working turn result back into the live session record."""

    destination.game_state = source.game_state
    destination.location_summary = source.location_summary
    destination.nearby_npcs = source.nearby_npcs
    destination.encounter_names = source.encounter_names
    destination.lootable_targets = source.lootable_targets
    destination.temp_item_counter = source.temp_item_counter
    destination.dynamic_location_counter = source.dynamic_location_counter


@lru_cache(maxsize=1)
def get_map_generator() -> DynamicMapGenerator:
    """Return the shared dynamic map generator used by exploration tools."""

    return build_map_generator_from_env()


@lru_cache(maxsize=1)
def get_loot_generator() -> LootGenerator:
    """Return the shared loot generator used by loot tools."""

    return build_loot_generator_from_env()


def get_runtime_tool_schemas() -> list[dict[str, Any]]:
    """Return OpenAI-compatible tool definitions for the GM agent."""

    return [
        {
            "type": "function",
            "function": {
                "name": "roll_d20_check",
                "description": (
                    "Roll a d20 for any risky player action such as attacking, dodging, "
                    "lying, climbing, sprinting, or casting under pressure."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action_name": {
                            "type": "string",
                            "description": "Short name of the attempted action.",
                        },
                        "attribute_key": {
                            "type": "string",
                            "description": "Preferred abstract attribute key such as stat_power, stat_agility, stat_insight, stat_tenacity, or stat_presence.",
                        },
                        "attribute_used": {
                            "type": "string",
                            "description": "Human-readable attribute label such as 体能, 敏捷, 魔力, 意志.",
                        },
                        "proficiency_bonus": {
                            "type": "integer",
                            "description": "Optional proficiency bonus override. Defaults to the player's growth state.",
                        },
                        "difficulty_class": {
                            "type": "integer",
                            "description": "DC between 1 and 40 chosen by the GM.",
                            "minimum": 1,
                            "maximum": 40,
                        },
                    },
                    "required": ["action_name", "difficulty_class"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "modify_game_state",
                "description": (
                    "Apply HP, MP, or known-location changes after an action is resolved. "
                    "Use signed deltas: negative for damage or resource spend, positive for healing or recovery."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_entity": {
                            "type": "string",
                            "description": "Use player or a known encounter entity id such as enemy_01.",
                        },
                        "hp_delta": {
                            "type": "integer",
                            "description": "Signed HP delta. Example: -15 means lose 15 HP.",
                        },
                        "mp_delta": {
                            "type": "integer",
                            "description": "Signed MP delta. Example: -5 means spend 5 MP.",
                        },
                        "location_change": {
                            "type": "string",
                            "description": "Known destination location_id when the player successfully moves.",
                        },
                    },
                    "required": ["target_entity"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "inventory_manager",
                "description": (
                    "Add or remove an item from the player inventory. Use this after rewards, "
                    "consumption, theft, breakage, or deliberate item usage."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "remove"],
                        },
                        "item_name": {
                            "type": "string",
                            "description": "Display name or known item key.",
                        },
                    },
                    "required": ["action", "item_name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_quest_state",
                "description": (
                    "Advance, complete, fail, or annotate a runtime quest. Use this whenever the player's actions "
                    "materially change objective progress."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "quest_id": {
                            "type": "string",
                            "description": "Known runtime quest id such as quest_01. Optional if quest_title clearly matches one quest.",
                        },
                        "quest_title": {
                            "type": "string",
                            "description": "Quest title to match or create when the exact quest id is unknown.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["active", "completed", "failed"],
                            "description": "New quest status when progress changes materially.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short in-universe summary of what changed for this quest.",
                        },
                        "progress_delta": {
                            "type": "integer",
                            "description": "Optional positive or negative progress change.",
                        },
                        "progress": {
                            "type": "integer",
                            "description": "Optional absolute quest progress override.",
                        },
                        "create_if_missing": {
                            "type": "boolean",
                            "description": "Set true only when the player clearly introduced a brand new long-term objective.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_encounter_state",
                "description": (
                    "Change encounter pacing state when the scene transitions between active combat and dramatic standoff/dialogue."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "encounter_id": {
                            "type": "string",
                            "description": "Optional encounter id. Defaults to the currently active encounter.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["active", "resolved", "escaped"],
                            "description": "Encounter pacing status after this beat.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short summary of why the encounter state changed.",
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional display label update for the encounter log.",
                        },
                        "clear_hostiles": {
                            "type": "boolean",
                            "description": "Set true only when hostiles truly leave the scene or are neutralized.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "trigger_growth",
                "description": (
                    "Trigger an explicit growth or evolution beat after a milestone, mastery spike, or epiphany."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "growth_type": {
                            "type": "string",
                            "enum": ["stat_boost", "new_skill", "mastery_up"],
                            "description": "Type of evolution to apply.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Narrative reason for the growth trigger.",
                        },
                        "attribute_key": {
                            "type": "string",
                            "description": "Optional abstract attribute or skill key touched by the growth beat.",
                        },
                        "amount": {
                            "type": "integer",
                            "description": "Optional attribute boost amount.",
                        },
                        "skill_key": {
                            "type": "string",
                            "description": "Optional abstract skill key for new_skill or mastery_up.",
                        },
                        "skill_label": {
                            "type": "string",
                            "description": "Optional display label for the new or improved skill.",
                        },
                        "mastery_delta": {
                            "type": "integer",
                            "description": "Optional mastery increase amount.",
                        },
                    },
                    "required": ["growth_type", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "resolve_combat_action",
                "description": (
                    "Resolve a direct combat exchange with the deterministic combat pipeline. "
                    "Use this for attacks, shots, stabs, swings, rushes, or other actions that directly harm a target."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_id": {
                            "type": "string",
                            "description": "Known hostile entity id such as enemy_01.",
                        },
                        "action_type": {
                            "type": "string",
                            "description": "Narrative combat action label such as attack, strike, shoot, or cast_attack.",
                        },
                        "weapon_key": {
                            "type": "string",
                            "description": "Known inventory key for the weapon to use. Optional if weapon_name is supplied.",
                        },
                        "weapon_name": {
                            "type": "string",
                            "description": "Display name for the weapon to use. Optional fallback when the exact key is unknown.",
                        },
                        "base_damage": {
                            "type": "integer",
                            "description": "Optional deterministic damage baseline for especially strong attacks.",
                        },
                        "attack_bonus": {
                            "type": "integer",
                            "description": "Optional bonus added to the hit roll.",
                        },
                        "target_dc": {
                            "type": "integer",
                            "description": "Optional hit DC override when the target is especially easy or hard to hit.",
                        },
                        "damage_type_key": {
                            "type": "string",
                            "description": "Optional damage type abstract key such as dmg_kinetic or dmg_fire.",
                        },
                        "resource_cost_key": {
                            "type": "string",
                            "description": "Optional player resource key spent on the attack, such as stat_mp.",
                        },
                        "resource_cost_amount": {
                            "type": "integer",
                            "description": "Optional positive resource amount spent on the attack.",
                        },
                        "resource_cost_container": {
                            "type": "string",
                            "description": "Where the resource is stored: stats or inventory.",
                            "enum": ["stats", "inventory"],
                        },
                    },
                    "required": ["target_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "resolve_exploration_action",
                "description": (
                    "Resolve travel or discovery with the deterministic exploration pipeline. "
                    "Use this when the player tries to move to another location, follow a trail, or push into a new area."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_location": {
                            "type": "string",
                            "description": "Destination title or runtime location id. Required unless target_node_id is provided.",
                        },
                        "target_node_id": {
                            "type": "string",
                            "description": "Known runtime location id when the destination already exists.",
                        },
                        "action_type": {
                            "type": "string",
                            "description": "Travel-style action label such as travel, chase, or investigate.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "resolve_loot_action",
                "description": (
                    "Resolve searching a corpse, container, or suspicious environment feature with the deterministic loot pipeline."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_id": {
                            "type": "string",
                            "description": "Known lootable target id such as corpse_enemy_01.",
                        },
                        "target_name": {
                            "type": "string",
                            "description": "Loot target description when the exact runtime id is unknown.",
                        },
                        "search_intent": {
                            "type": "string",
                            "description": "Short plain-language reminder of what the player is searching.",
                        },
                        "action_type": {
                            "type": "string",
                            "description": "Search action label such as loot, search, or inspect.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
    ]


def execute_runtime_tool(
    record: SessionRecord,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    """Execute one registered tool against the working session record.

    If *arguments* contains a ``__parse_error`` key (set by the GM agent's
    ``_parse_tool_arguments`` when JSON decoding failed), the tool is **not**
    executed.  Instead, an explicit error result is returned so the GM can
    react to the malformed call instead of silently running with defaults.
    """

    if "__parse_error" in arguments:
        return ToolExecutionResult(
            observation={
                "status": "error",
                "reason": "argument_parse_failed",
                "tool_name": tool_name,
                "parse_error": arguments["__parse_error"],
                "raw_arguments": arguments.get("__raw", ""),
                "detail": arguments.get("__detail", ""),
            },
            executed_events=[
                ExecutedEvent(
                    event_type="tool_error",
                    is_success=False,
                    actor="system",
                    target=tool_name,
                    abstract_action="argument_parse_failed",
                    result_tags=["argument_parse_failed", arguments["__parse_error"]],
                )
            ],
            mutation_logs=[],
        )

    if tool_name == "roll_d20_check":
        return _roll_d20_check(record, arguments)
    if tool_name == "modify_game_state":
        return _modify_game_state(record, arguments)
    if tool_name == "inventory_manager":
        return _inventory_manager(record, arguments)
    if tool_name == "update_quest_state":
        return _update_quest_state(record, arguments)
    if tool_name == "update_encounter_state":
        return _update_encounter_state(record, arguments)
    if tool_name == "trigger_growth":
        return _trigger_growth(record, arguments)
    if tool_name == "resolve_combat_action":
        return _resolve_combat_action(record, arguments)
    if tool_name == "resolve_exploration_action":
        return _resolve_exploration_action(record, arguments)
    if tool_name == "resolve_loot_action":
        return _resolve_loot_action(record, arguments)

    return ToolExecutionResult(
        observation={
            "status": "error",
            "reason": "unknown_tool",
            "tool_name": tool_name,
        },
        executed_events=[
            ExecutedEvent(
                event_type="tool_error",
                is_success=False,
                actor="system",
                target=tool_name,
                abstract_action="unknown_tool",
                result_tags=["unknown_tool"],
            )
        ],
        mutation_logs=[],
    )


def _roll_d20_check(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    action_name = _clean_text(arguments.get("action_name"), fallback="risk_action")
    attribute_key = _clean_text(arguments.get("attribute_key"), fallback="")
    attribute_used = _clean_text(arguments.get("attribute_used"), fallback="generic")
    difficulty_class = max(1, min(40, _coerce_int(arguments.get("difficulty_class"), 10)))
    proficiency_bonus = _coerce_optional_int(arguments.get("proficiency_bonus"))
    if proficiency_bonus is None:
        proficiency_bonus = record.game_state.player.growth.proficiency_bonus
    resolved_attribute, attribute_value = _resolve_check_attribute(record, attribute_key, attribute_used)

    roll_result = random.randint(1, 20)
    attribute_bonus = attribute_value / 10
    modifier = attribute_bonus + proficiency_bonus
    total = roll_result + modifier
    critical = roll_result in {1, 20}
    is_success = roll_result != 1 and total >= difficulty_class

    result_tags = [
        f"attribute:{resolved_attribute}",
        f"dc:{difficulty_class}",
        f"roll:{roll_result}",
        f"total:{total}",
    ]
    if roll_result == 20:
        result_tags.append("critical_success" if is_success else "critical_roll")
    elif roll_result == 1:
        result_tags.append("critical_failure")
    else:
        result_tags.append("success" if is_success else "failure")

    return ToolExecutionResult(
        observation={
            "roll_result": roll_result,
            "modifier": modifier,
            "attribute_value": attribute_value,
            "attribute_bonus": attribute_bonus,
            "proficiency_bonus": proficiency_bonus,
            "total": total,
            "difficulty_class": difficulty_class,
            "is_success": is_success,
            "critical": critical,
            "resolved_attribute": resolved_attribute,
        },
        executed_events=[
            ExecutedEvent(
                event_type="skill_check",
                is_success=is_success,
                actor="player",
                target="world",
                abstract_action=action_name,
                result_tags=result_tags,
            )
        ],
        mutation_logs=[],
    )


def _modify_game_state(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    target_entity = _clean_text(arguments.get("target_entity"), fallback="player")
    hp_delta = _coerce_int(arguments.get("hp_delta"), 0)
    mp_delta = _coerce_int(arguments.get("mp_delta"), 0)
    location_change_raw = arguments.get("location_change")
    location_change = (
        _clean_text(location_change_raw, fallback="")
        if location_change_raw is not None
        else ""
    )

    logs: list[MutationLog] = []
    result_tags: list[str] = []
    observation: dict[str, Any] = {
        "status": "updated",
        "target_entity": target_entity,
    }

    if target_entity == "player":
        hp_result = _queue_stat_update(
            logs=logs,
            container=record.game_state.player.stats,
            stat_key=DEFAULT_HP_STAT_KEY,
            delta=hp_delta,
            target_path=f"player.stats.{DEFAULT_HP_STAT_KEY}",
        )
        mp_result = _queue_stat_update(
            logs=logs,
            container=record.game_state.player.stats,
            stat_key=DEFAULT_MP_STAT_KEY,
            delta=mp_delta,
            target_path=f"player.stats.{DEFAULT_MP_STAT_KEY}",
        )

        if hp_result is not None:
            result_tags.append("hp_changed")
            observation["current_hp"] = hp_result
            if hp_result <= 0:
                result_tags.append("player_downed")
        if mp_result is not None:
            result_tags.append("mp_changed")
            observation["current_mp"] = mp_result
    else:
        target_state = record.game_state.encounter_entities.get(target_entity)
        if target_state is None:
            return _tool_error(
                tool_name="modify_game_state",
                reason="unknown_target_entity",
                target=target_entity,
            )

        hp_result = _queue_stat_update(
            logs=logs,
            container=target_state.stats,
            stat_key=DEFAULT_HP_STAT_KEY,
            delta=hp_delta,
            target_path=f"encounter_entities.{target_entity}.stats.{DEFAULT_HP_STAT_KEY}",
        )
        if hp_result is not None:
            result_tags.append("hp_changed")
            observation["current_hp"] = hp_result
            if hp_result <= 0:
                result_tags.append("target_killed")
                record.register_defeated_enemy_loot_target(target_entity)
                logs.append(
                    MutationLog(
                        action="delete",
                        target_path=f"encounter_entities.{target_entity}",
                        value=target_entity,
                        reason="agent_target_killed",
                    )
                )
                remaining_hostiles = sorted(
                    entity_id
                    for entity_id in record.game_state.encounter_entities.keys()
                    if entity_id != target_entity
                )
                if not remaining_hostiles:
                    _append_active_encounter_logs(
                        record,
                        logs,
                        status="resolved",
                        summary="The immediate threat has been neutralized.",
                        remaining_enemy_ids=[],
                    )
                    logs.append(
                        MutationLog(
                            action="set",
                            target_path="active_encounter",
                            value=None,
                            reason="agent_encounter_resolved",
                        )
                    )

    if location_change:
        destination_id, location_logs = _queue_location_change(record, location_change)
        logs.extend(location_logs)
        result_tags.append("location_changed")
        observation["current_location_id"] = destination_id
        if destination_id != record.game_state.current_location_id:
            result_tags.append("encounter_cleared")

    if not logs:
        observation["status"] = "noop"

    if logs:
        _apply_logs(record, logs)
        if "location_changed" in result_tags:
            record.lootable_targets.clear()

    if "current_location_id" not in observation:
        observation["current_location_id"] = record.game_state.current_location_id

    return ToolExecutionResult(
        observation=observation,
        executed_events=[
            ExecutedEvent(
                event_type="state_change",
                is_success=True,
                actor="system",
                target=target_entity,
                abstract_action="modify_game_state",
                result_tags=result_tags or ["noop"],
            )
        ],
        mutation_logs=logs,
    )


def _inventory_manager(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    action = _clean_text(arguments.get("action"), fallback="add")
    item_name = _clean_text(arguments.get("item_name"), fallback="")
    if action not in {"add", "remove"} or not item_name:
        return _tool_error(
            tool_name="inventory_manager",
            reason="invalid_inventory_arguments",
            target=item_name or "unknown_item",
        )

    logs: list[MutationLog] = []
    result_tags = [f"inventory_{action}"]

    if action == "add":
        item_key = _match_inventory_key(record, item_name)
        if item_key is None:
            item_key = record.next_temp_item_key()
            logs.append(
                MutationLog(
                    action="set",
                    target_path=f"player.temporary_items.{item_key}",
                    value=item_name,
                    reason="agent_inventory_name_registration",
                )
            )
        current_quantity = record.game_state.player.inventory.get(item_key, 0)
        logs.append(
            MutationLog(
                action="set",
                target_path=f"player.inventory.{item_key}",
                value=current_quantity + 1,
                reason="agent_inventory_add",
            )
        )
        _apply_logs(record, logs)
        return ToolExecutionResult(
            observation={
                "status": "updated",
                "action": action,
                "item_key": item_key,
                "item_name": record.game_state.player.temporary_items.get(item_key, item_name),
                "quantity": record.game_state.player.inventory.get(item_key, 0),
            },
            executed_events=[
                ExecutedEvent(
                    event_type="inventory",
                    is_success=True,
                    actor="player",
                    target=item_key,
                    abstract_action="inventory_add",
                    result_tags=result_tags,
                )
            ],
            mutation_logs=logs,
        )

    item_key = _match_inventory_key(record, item_name)
    if item_key is None or record.game_state.player.inventory.get(item_key, 0) <= 0:
        return _tool_error(
            tool_name="inventory_manager",
            reason="item_not_found",
            target=item_name,
        )

    current_quantity = record.game_state.player.inventory[item_key]
    next_quantity = max(0, current_quantity - 1)
    logs.append(
        MutationLog(
            action="set",
            target_path=f"player.inventory.{item_key}",
            value=next_quantity,
            reason="agent_inventory_remove",
        )
    )
    if next_quantity == 0 and item_key in record.game_state.player.temporary_items:
        logs.append(
            MutationLog(
                action="delete",
                target_path=f"player.temporary_items.{item_key}",
                value=item_key,
                reason="agent_inventory_forget_name",
            )
        )

    _apply_logs(record, logs)
    return ToolExecutionResult(
        observation={
            "status": "updated",
            "action": action,
            "item_key": item_key,
            "quantity": record.game_state.player.inventory.get(item_key, 0),
        },
        executed_events=[
            ExecutedEvent(
                event_type="inventory",
                is_success=True,
                actor="player",
                target=item_key,
                abstract_action="inventory_remove",
                result_tags=result_tags,
            )
            ],
            mutation_logs=logs,
        )


def _update_quest_state(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    requested_quest_id = _clean_text(arguments.get("quest_id"), fallback="")
    requested_quest_title = _clean_text(arguments.get("quest_title"), fallback="")
    next_status = _clean_text(arguments.get("status"), fallback="active")
    summary = _clean_text(arguments.get("summary"), fallback="")
    progress = _coerce_optional_int(arguments.get("progress"))
    progress_delta = _coerce_int(arguments.get("progress_delta"), 0)
    create_if_missing = _coerce_bool(arguments.get("create_if_missing"), default=False)

    if next_status not in {"active", "completed", "failed"}:
        next_status = "active"

    quest_id = _resolve_quest_id(record, requested_quest_id, requested_quest_title)
    logs: list[MutationLog] = []

    if quest_id is None:
        if not create_if_missing:
            return _tool_error(
                tool_name="update_quest_state",
                reason="quest_not_found",
                target=requested_quest_id or requested_quest_title or "unknown_quest",
            )

        if not requested_quest_title:
            return _tool_error(
                tool_name="update_quest_state",
                reason="quest_not_found",
                target=requested_quest_id or "unknown_quest",
            )

        quest_id = _next_dynamic_quest_id(record)
        logs.append(
            MutationLog(
                action="set",
                target_path=f"quest_log.{quest_id}",
                value={
                    "quest_id": quest_id,
                    "title": requested_quest_title,
                    "status": next_status,
                    "summary": summary or "A new objective has entered the scene.",
                    "progress": max(0, progress if progress is not None else progress_delta),
                },
                reason="quest_created",
            )
        )
    else:
        quest = record.game_state.quest_log[quest_id]
        logs.append(
            MutationLog(
                action="set",
                target_path=f"quest_log.{quest_id}.status",
                value=next_status,
                reason="quest_status_update",
            )
        )
        if summary:
            logs.append(
                MutationLog(
                    action="set",
                    target_path=f"quest_log.{quest_id}.summary",
                    value=summary,
                    reason="quest_summary_update",
                )
            )
        if progress is not None:
            logs.append(
                MutationLog(
                    action="set",
                    target_path=f"quest_log.{quest_id}.progress",
                    value=max(0, progress),
                    reason="quest_progress_update",
                )
            )
        elif progress_delta != 0:
            logs.append(
                MutationLog(
                    action="set",
                    target_path=f"quest_log.{quest_id}.progress",
                    value=max(0, quest.progress + progress_delta),
                    reason="quest_progress_update",
                )
            )

    _apply_logs(record, logs)
    quest_state = record.game_state.quest_log[quest_id]
    storyline_logs = _sync_storyline_progress_from_quest(record, quest_state)
    if storyline_logs:
        logs.extend(storyline_logs)
        _apply_logs(record, storyline_logs)
        quest_state = record.game_state.quest_log[quest_id]
    result_tags = [f"quest_status:{quest_state.status}"]
    if progress is not None:
        result_tags.append("quest_progress_set")
    elif progress_delta != 0:
        result_tags.append(f"quest_progress_delta:{progress_delta}")
    if storyline_logs:
        result_tags.append("chapter_progress_synced")

    return ToolExecutionResult(
        observation={
            "status": "updated",
            "quest_id": quest_state.quest_id,
            "title": quest_state.title,
            "quest_status": quest_state.status,
            "progress": quest_state.progress,
            "summary": quest_state.summary,
            "chapter_progress_percent": record.game_state.world_config.world_book.campaign_context.current_chapter.progress_percent,
        },
        executed_events=[
            ExecutedEvent(
                event_type="quest",
                is_success=True,
                actor="system",
                target=quest_state.quest_id,
                abstract_action="update_quest_state",
                result_tags=result_tags,
            )
        ],
        mutation_logs=logs,
    )


def _sync_storyline_progress_from_quest(
    record: SessionRecord,
    quest_state,
) -> list[MutationLog]:
    campaign_context = record.game_state.world_config.world_book.campaign_context
    current_chapter = campaign_context.current_chapter
    if (
        current_chapter.linked_quest_id != quest_state.quest_id
        and campaign_context.main_quest.linked_quest_id != quest_state.quest_id
    ):
        return []

    if quest_state.status == "completed":
        next_progress = 100
    else:
        next_progress = min(99, max(current_chapter.progress_percent, quest_state.progress * 10))

    logs: list[MutationLog] = []
    if next_progress != current_chapter.progress_percent:
        logs.append(
            MutationLog(
                action="set",
                target_path="world_config.world_book.campaign_context.current_chapter.progress_percent",
                value=next_progress,
                reason="chapter_progress_update",
            )
        )

    main_quest = campaign_context.main_quest
    if main_quest.linked_quest_id == quest_state.quest_id and next_progress != main_quest.progress_percent:
        logs.append(
            MutationLog(
                action="set",
                target_path="world_config.world_book.campaign_context.main_quest.progress_percent",
                value=next_progress,
                reason="main_quest_progress_update",
            )
        )

    if quest_state.status == "completed":
        for index, milestone in enumerate(campaign_context.milestones):
            if milestone.is_completed:
                continue
            updated_milestones = [
                existing.model_copy(deep=True) if hasattr(existing, "model_copy") else existing
                for existing in campaign_context.milestones
            ]
            updated_milestones[index].is_completed = True
            logs.append(
                MutationLog(
                    action="set",
                    target_path="world_config.world_book.campaign_context.milestones",
                    value=updated_milestones,
                    reason="story_milestone_completed",
                )
            )
            break

    return logs


def _trigger_growth(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    resolution = resolve_growth(record.game_state, arguments)
    if resolution.mutation_logs:
        _apply_logs(record, resolution.mutation_logs)

    return ToolExecutionResult(
        observation=resolution.observation,
        executed_events=[resolution.executed_event],
        mutation_logs=resolution.mutation_logs,
    )


def _update_encounter_state(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    requested_encounter_id = _clean_text(arguments.get("encounter_id"), fallback="")
    next_status = _clean_text(arguments.get("status"), fallback="active")
    summary = _clean_text(arguments.get("summary"), fallback="")
    label = _clean_text(arguments.get("label"), fallback="")
    clear_hostiles = _coerce_bool(arguments.get("clear_hostiles"), default=False)

    if next_status not in {"active", "resolved", "escaped"}:
        next_status = "active"

    encounter_id = requested_encounter_id or (record.game_state.active_encounter or "")
    if not encounter_id or encounter_id not in record.game_state.encounter_log:
        return _tool_error(
            tool_name="update_encounter_state",
            reason="encounter_not_found",
            target=requested_encounter_id or "active_encounter",
        )

    logs: list[MutationLog] = [
        MutationLog(
            action="set",
            target_path=f"encounter_log.{encounter_id}.status",
            value=next_status,
            reason="encounter_status_update",
        )
    ]
    if summary:
        logs.append(
            MutationLog(
                action="set",
                target_path=f"encounter_log.{encounter_id}.summary",
                value=summary,
                reason="encounter_summary_update",
            )
        )
    if label:
        logs.append(
            MutationLog(
                action="set",
                target_path=f"encounter_log.{encounter_id}.label",
                value=label,
                reason="encounter_label_update",
            )
        )

    if clear_hostiles:
        logs.append(
            MutationLog(
                action="set",
                target_path=f"encounter_log.{encounter_id}.enemy_ids",
                value=[],
                reason="encounter_hostiles_cleared",
            )
        )
        logs.append(
            MutationLog(
                action="set",
                target_path="encounter_entities",
                value={},
                reason="encounter_hostiles_cleared",
            )
        )

    if next_status == "active":
        if record.game_state.active_encounter is None:
            logs.append(
                MutationLog(
                    action="set",
                    target_path="active_encounter",
                    value=encounter_id,
                    reason="encounter_reactivated",
                )
            )
    elif record.game_state.active_encounter == encounter_id:
        logs.append(
            MutationLog(
                action="set",
                target_path="active_encounter",
                value=None,
                reason="encounter_deactivated",
            )
        )

    _apply_logs(record, logs)
    encounter_state = record.game_state.encounter_log[encounter_id]
    return ToolExecutionResult(
        observation={
            "status": "updated",
            "encounter_id": encounter_state.encounter_id,
            "encounter_status": encounter_state.status,
            "active_encounter": record.game_state.active_encounter,
            "enemy_count": len(record.game_state.encounter_entities),
            "summary": encounter_state.summary,
        },
        executed_events=[
            ExecutedEvent(
                event_type="encounter",
                is_success=True,
                actor="system",
                target=encounter_state.encounter_id,
                abstract_action="update_encounter_state",
                result_tags=[f"encounter_status:{encounter_state.status}"],
            )
        ],
        mutation_logs=logs,
    )


def _resolve_combat_action(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    target_id = _clean_text(arguments.get("target_id"), fallback="")
    if not target_id:
        return _tool_error(
            tool_name="resolve_combat_action",
            reason="missing_target_id",
            target="unknown_target",
        )

    normalized_arguments = dict(arguments)
    weapon_key = _resolve_combat_weapon_key(record, arguments)
    if weapon_key:
        normalized_arguments["weapon_key"] = weapon_key

    logs, events = resolve_combat(record.game_state, normalized_arguments)
    defeated_target_ids = _extract_defeated_target_ids(events)

    for defeated_target_id in defeated_target_ids:
        if defeated_target_id in record.game_state.encounter_entities:
            record.register_defeated_enemy_loot_target(defeated_target_id)

    remaining_hostiles = set(record.game_state.encounter_entities.keys()) - defeated_target_ids
    if defeated_target_ids and not remaining_hostiles and record.game_state.active_encounter is not None:
        _append_active_encounter_logs(
            record,
            logs,
            status="resolved",
            summary="The last active enemy in this encounter has fallen.",
            remaining_enemy_ids=[],
        )
        logs.append(
            MutationLog(
                action="set",
                target_path="active_encounter",
                value=None,
                reason="combat_encounter_cleared",
            )
        )

    if logs:
        _apply_logs(record, logs)

    observation: dict[str, Any] = {
        "status": "resolved" if events else "noop",
        "target_id": target_id,
        "weapon_key": weapon_key or None,
        "target_defeated": bool(defeated_target_ids),
        "current_location_id": record.game_state.current_location_id,
    }
    if DEFAULT_HP_STAT_KEY in record.game_state.player.stats:
        observation["player_hp"] = record.game_state.player.stats[DEFAULT_HP_STAT_KEY]
    if target_id in record.game_state.encounter_entities:
        target_state = record.game_state.encounter_entities[target_id]
        observation["target_hp"] = target_state.stats.get(DEFAULT_HP_STAT_KEY)
    elif target_id in defeated_target_ids:
        observation["target_hp"] = 0

    return ToolExecutionResult(
        observation=observation,
        executed_events=events,
        mutation_logs=logs,
    )


def _resolve_exploration_action(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    target_location = _clean_text(arguments.get("target_location"), fallback="")
    explicit_target_node_id = _clean_text(arguments.get("target_node_id"), fallback="")

    if not target_location and not explicit_target_node_id:
        return _tool_error(
            tool_name="resolve_exploration_action",
            reason="missing_target_location",
            target="unknown_location",
        )

    target_node_id = explicit_target_node_id
    if not target_node_id and target_location:
        target_node_id = _resolve_location_id(record, target_location) or record.next_dynamic_location_id()

    known_node = record.game_state.world_config.topology.nodes.get(target_node_id)
    target_name = target_location or (known_node.title if known_node is not None else target_node_id)

    normalized_arguments = dict(arguments)
    logs, event = resolve_exploration(
        record.game_state,
        normalized_arguments,
        map_generator=get_map_generator(),
        target_node_id=target_node_id,
        target_name=target_name,
    )

    moved_to_new_location = any(
        log.target_path == "current_location_id" and log.value != record.game_state.current_location_id
        for log in logs
    )
    if event.is_success and moved_to_new_location:
        _append_encounter_clear_logs(
            record,
            logs,
            encounter_status="escaped",
            encounter_summary="The player disengaged and broke away from the encounter by changing location.",
            active_reason="exploration_clear_encounter",
            entities_reason="exploration_clear_encounter_entities",
        )

    if logs:
        _apply_logs(record, logs)
        if moved_to_new_location:
            record.lootable_targets.clear()

    current_node = record.current_location_node
    return ToolExecutionResult(
        observation={
            "status": "resolved" if event.is_success else "blocked",
            "target_location_id": target_node_id,
            "target_location": target_name,
            "current_location_id": record.game_state.current_location_id,
            "current_location_title": current_node.title if current_node is not None else record.game_state.current_location_id,
            "discovered_new_location": "new_location_discovered" in event.result_tags,
        },
        executed_events=[event],
        mutation_logs=logs,
    )


def _resolve_loot_action(
    record: SessionRecord,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    user_input = _build_loot_search_text(arguments)
    logs, event, consumed_target_id = _resolve_loot_turn(
        record,
        arguments,
        user_input=user_input,
    )

    if logs:
        _apply_logs(record, logs)
    if consumed_target_id is not None:
        record.consume_loot_target(consumed_target_id)

    observation = {
        "status": "resolved" if event.is_success else "failed",
        "target": event.target,
        "consumed_target_id": consumed_target_id,
        "awarded_item_keys": _extract_inventory_item_keys(logs),
    }
    return ToolExecutionResult(
        observation=observation,
        executed_events=[event],
        mutation_logs=logs,
    )


def _apply_logs(record: SessionRecord, logs: list[MutationLog]) -> None:
    if not logs:
        return
    record.game_state = apply_mutations(record.game_state, logs)
    record.sync_after_state_update()
    _sync_power_level(record)


def _queue_stat_update(
    *,
    logs: list[MutationLog],
    container: dict[str, int],
    stat_key: str,
    delta: int,
    target_path: str,
) -> int | None:
    if delta == 0:
        return None
    if stat_key not in container:
        return None
    next_value = max(0, container[stat_key] + delta)
    logs.append(
        MutationLog(
            action="set",
            target_path=target_path,
            value=next_value,
            reason="agent_stat_update",
        )
    )
    return next_value


def _queue_location_change(
    record: SessionRecord,
    location_change: str,
) -> tuple[str, list[MutationLog]]:
    current_location_id = record.game_state.current_location_id
    topology = record.game_state.world_config.topology
    destination_id = _resolve_location_id(record, location_change)
    logs: list[MutationLog] = []

    if destination_id is None:
        destination_id = record.next_dynamic_location_id()
        logs.append(
            MutationLog(
                action="set",
                target_path=f"world_config.topology.nodes.{destination_id}",
                value=WorldNode(
                    node_id=destination_id,
                    title=location_change,
                    base_desc=f"这里是{location_change}，空气里仍残留着未散尽的危险与余震。",
                    hidden_detail_dc10=None,
                    deep_secret_dc18=None,
                    tags=["agent_generated"],
                ).model_dump(),
                reason="agent_create_location",
            )
        )

    current_edges = topology.edges.get(current_location_id)
    if current_edges is None:
        logs.append(
            MutationLog(
                action="set",
                target_path=f"world_config.topology.edges.{current_location_id}",
                value=[destination_id],
                reason="agent_link_location",
            )
        )
    elif destination_id not in current_edges:
        logs.append(
            MutationLog(
                action="append",
                target_path=f"world_config.topology.edges.{current_location_id}",
                value=destination_id,
                reason="agent_link_location",
            )
        )

    if destination_id != current_location_id:
        logs.append(
            MutationLog(
                action="set",
                target_path="current_location_id",
                value=destination_id,
                reason="agent_location_change",
            )
        )
        if record.game_state.active_encounter is not None:
            _append_active_encounter_logs(
                record,
                logs,
                status="escaped",
                summary="The player left the current location and broke contact with the encounter.",
                remaining_enemy_ids=[],
            )
            logs.append(
                MutationLog(
                    action="set",
                    target_path="active_encounter",
                    value=None,
                    reason="agent_clear_encounter_on_move",
                )
            )
            logs.append(
                MutationLog(
                    action="set",
                    target_path="encounter_entities",
                    value={},
                    reason="agent_clear_encounter_entities_on_move",
                )
            )

    return destination_id, logs


def _resolve_combat_weapon_key(record: SessionRecord, arguments: dict[str, Any]) -> str:
    explicit_weapon_key = _clean_text(arguments.get("weapon_key"), fallback="")
    if explicit_weapon_key:
        return explicit_weapon_key

    weapon_name = _clean_text(arguments.get("weapon_name"), fallback="")
    if weapon_name:
        matched_item_key = _match_inventory_key(record, weapon_name)
        if matched_item_key is not None:
            return matched_item_key

    preferred_weapons = [
        item_key
        for item_key, quantity in record.game_state.player.inventory.items()
        if quantity > 0 and item_key.startswith("item_weapon")
    ]
    if preferred_weapons:
        return preferred_weapons[0]

    for item_key, quantity in record.game_state.player.inventory.items():
        if quantity > 0:
            return item_key
    return ""


def _extract_defeated_target_ids(events: list[ExecutedEvent]) -> set[str]:
    return {
        event.target
        for event in events
        if event.event_type == "combat" and "target_killed" in event.result_tags
    }


def _append_encounter_clear_logs(
    record: SessionRecord,
    logs: list[MutationLog],
    *,
    encounter_status: str,
    encounter_summary: str,
    active_reason: str,
    entities_reason: str,
) -> None:
    _append_active_encounter_logs(
        record,
        logs,
        status=encounter_status,
        summary=encounter_summary,
        remaining_enemy_ids=[],
    )
    if record.game_state.active_encounter is not None:
        logs.append(
            MutationLog(
                action="set",
                target_path="active_encounter",
                value=None,
                reason=active_reason,
            )
        )
    if record.game_state.encounter_entities:
        logs.append(
            MutationLog(
                action="set",
                target_path="encounter_entities",
                value={},
                reason=entities_reason,
            )
        )


def _append_active_encounter_logs(
    record: SessionRecord,
    logs: list[MutationLog],
    *,
    status: str,
    summary: str,
    remaining_enemy_ids: list[str] | None,
) -> None:
    active_encounter_id = record.game_state.active_encounter
    if active_encounter_id is None:
        return
    if active_encounter_id not in record.game_state.encounter_log:
        return

    logs.append(
        MutationLog(
            action="set",
            target_path=f"encounter_log.{active_encounter_id}.status",
            value=status,
            reason="encounter_status_update",
        )
    )
    logs.append(
        MutationLog(
            action="set",
            target_path=f"encounter_log.{active_encounter_id}.summary",
            value=summary,
            reason="encounter_summary_update",
        )
    )
    if remaining_enemy_ids is not None:
        logs.append(
            MutationLog(
                action="set",
                target_path=f"encounter_log.{active_encounter_id}.enemy_ids",
                value=remaining_enemy_ids,
                reason="encounter_enemy_ids_update",
            )
        )


def _resolve_loot_turn(
    record: SessionRecord,
    parameters: dict[str, Any],
    *,
    user_input: str,
) -> tuple[list[MutationLog], ExecutedEvent, str | None]:
    target_id, target_label, consumed_target_id, is_valid_target = _resolve_loot_target(record, parameters)
    if not is_valid_target:
        return [], ExecutedEvent(
            event_type="loot",
            is_success=False,
            actor="player",
            target=target_label,
            abstract_action=str(parameters.get("action_type", "loot")),
            result_tags=["invalid_loot_target"],
        ), None

    loot_pool = get_loot_generator().generate_pool(
        world_config=record.game_state.world_config,
        target_name=target_label,
        user_input=user_input,
        temp_key_factory=record.next_temp_item_key,
    )
    logs, event = resolve_loot(
        record.game_state,
        parameters,
        loot_pool=loot_pool,
        target_label=target_label,
    )
    return logs, event, consumed_target_id


def _resolve_loot_target(
    record: SessionRecord,
    parameters: dict[str, Any],
) -> tuple[str | None, str, str | None, bool]:
    raw_target_id = parameters.get("target_id")
    if isinstance(raw_target_id, str) and raw_target_id.strip():
        normalized_target_id = raw_target_id.strip()
        loot_target = record.get_loot_target(normalized_target_id)
        if loot_target is not None:
            return loot_target.target_id, loot_target.display_name, loot_target.target_id, True
        return None, normalized_target_id, None, False

    if len(record.lootable_targets) == 1:
        only_target = next(iter(record.lootable_targets.values()))
        return only_target.target_id, only_target.display_name, only_target.target_id, True

    raw_target_text = parameters.get("target_name") or parameters.get("search_intent")
    if isinstance(raw_target_text, str) and raw_target_text.strip():
        normalized_target_text = raw_target_text.strip()
        corpse_markers = ("尸体", "残骸", "遗体", "尸首", "corpse", "body", "remains")
        if any(marker in normalized_target_text.lower() for marker in corpse_markers):
            return None, normalized_target_text, None, False
        return None, normalized_target_text, None, True

    return None, record.game_state.current_location_id, None, True


def _resolve_quest_id(
    record: SessionRecord,
    requested_quest_id: str,
    requested_quest_title: str,
) -> str | None:
    canonical_requested_id = _canonicalize_quest_id(requested_quest_id)
    if requested_quest_id and requested_quest_id in record.game_state.quest_log:
        return requested_quest_id
    if canonical_requested_id and canonical_requested_id in record.game_state.quest_log:
        return canonical_requested_id

    normalized_title = _normalize_for_match(requested_quest_title) if requested_quest_title else ""
    if normalized_title:
        for quest_id, quest_state in record.game_state.quest_log.items():
            if _normalize_for_match(quest_state.title) == normalized_title:
                return quest_id
        if len(normalized_title) >= 4:
            for quest_id, quest_state in record.game_state.quest_log.items():
                normalized_existing_title = _normalize_for_match(quest_state.title)
                if normalized_title in normalized_existing_title or normalized_existing_title in normalized_title:
                    return quest_id

    active_quests = [
        quest_id
        for quest_id, quest_state in record.game_state.quest_log.items()
        if quest_state.status == "active"
    ]
    if not requested_quest_id and not requested_quest_title and len(active_quests) == 1:
        return active_quests[0]
    return None


def _next_dynamic_quest_id(record: SessionRecord) -> str:
    existing_numbers = [
        int(quest_id.removeprefix("quest_"))
        for quest_id in record.game_state.quest_log
        if quest_id.startswith("quest_") and quest_id.removeprefix("quest_").isdigit()
    ]
    next_number = max(existing_numbers, default=0) + 1
    return f"quest_{next_number:02d}"


def _canonicalize_quest_id(quest_id: str) -> str:
    normalized = _clean_text(quest_id, fallback="")
    if not normalized.startswith("quest_"):
        return normalized

    numeric_suffix = normalized.removeprefix("quest_")
    if not numeric_suffix.isdigit():
        return normalized

    return f"quest_{int(numeric_suffix):02d}"


def _resolve_check_attribute(
    record: SessionRecord,
    attribute_key: str,
    attribute_used: str,
) -> tuple[str, int]:
    player_attributes = record.game_state.player.attributes
    glossary = record.game_state.world_config.glossary.attributes

    alias_groups = (
        (
            "stat_power",
            ("attr_power", "power", "strength", "force", "power"),
        ),
        (
            "stat_agility",
            ("attr_dex", "attr_agility", "dex", "dexterity", "speed", "agility"),
        ),
        (
            "stat_insight",
            ("attr_focus", "focus", "perception", "insight", "awareness"),
        ),
        (
            "stat_tenacity",
            ("attr_will", "will", "mana", "tenacity", "spirit", "resolve"),
        ),
        (
            "stat_presence",
            ("attr_presence", "presence", "charisma", "leadership", "charm"),
        ),
    )

    normalized_lookup = {
        _normalize(key): key for key in player_attributes
    }
    normalized_lookup.update({_normalize(label): key for key, label in glossary.items()})

    def _match_candidate(candidate: str) -> tuple[str, int] | None:
        if not candidate:
            return None
        if candidate in player_attributes:
            return candidate, player_attributes[candidate]
        normalized_candidate = _normalize(candidate)
        if normalized_candidate in normalized_lookup:
            resolved_key = normalized_lookup[normalized_candidate]
            return resolved_key, player_attributes.get(resolved_key, 0)
        for canonical_key, aliases in alias_groups:
            if candidate == canonical_key or normalized_candidate == _normalize(canonical_key):
                if canonical_key in player_attributes:
                    return canonical_key, player_attributes[canonical_key]
                for alias in aliases:
                    if alias in player_attributes:
                        return alias, player_attributes[alias]
                break
            if normalized_candidate in {_normalize(alias) for alias in aliases}:
                if canonical_key in player_attributes:
                    return canonical_key, player_attributes[canonical_key]
                for alias in aliases:
                    if alias in player_attributes:
                        return alias, player_attributes[alias]
        return None

    for candidate in (attribute_key, attribute_used):
        resolved = _match_candidate(candidate)
        if resolved is not None:
            return resolved

    for canonical_key, aliases in alias_groups:
        if canonical_key in player_attributes:
            return canonical_key, player_attributes[canonical_key]
        for alias in aliases:
            if alias in player_attributes:
                return alias, player_attributes[alias]

    if player_attributes:
        fallback_key = next(iter(player_attributes))
        return fallback_key, player_attributes[fallback_key]
    return "flat", 0


def _resolve_location_id(record: SessionRecord, location_change: str) -> str | None:
    normalized_target = _normalize(location_change)
    for node_id, node in record.game_state.world_config.topology.nodes.items():
        if _normalize(node_id) == normalized_target:
            return node_id
        if _normalize(node.title) == normalized_target:
            return node_id
    return None


def _match_inventory_key(record: SessionRecord, item_name: str) -> str | None:
    normalized_target = _normalize(item_name)

    for item_key, quantity in record.game_state.player.inventory.items():
        if quantity <= 0:
            continue
        if _normalize(item_key) == normalized_target:
            return item_key

    for item_key, display_name in record.game_state.player.temporary_items.items():
        if _normalize(display_name) == normalized_target or _normalize(item_key) == normalized_target:
            return item_key

    return None


def _build_loot_search_text(arguments: dict[str, Any]) -> str:
    for key in ("search_intent", "target_name", "target_id"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "search nearby loot"


def _extract_inventory_item_keys(logs: list[MutationLog]) -> list[str]:
    awarded_keys: list[str] = []
    for log in logs:
        if log.target_path.startswith("player.inventory.") and log.action in {"add", "set"}:
            awarded_keys.append(log.target_path.removeprefix("player.inventory."))
    return awarded_keys


def _tool_error(tool_name: str, reason: str, target: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        observation={
            "status": "error",
            "reason": reason,
            "target": target,
        },
        executed_events=[
            ExecutedEvent(
                event_type="tool_error",
                is_success=False,
                actor="system",
                target=target,
                abstract_action=tool_name,
                result_tags=[reason],
            )
        ],
        mutation_logs=[],
    )


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
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


def _clean_text(value: Any, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    stripped = value.strip()
    return stripped or fallback


def _normalize(value: str) -> str:
    return "".join(value.strip().lower().split())


def _normalize_for_match(value: str) -> str:
    lowered = value.strip().lower()
    normalized_chars: list[str] = []
    for char in lowered:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            normalized_chars.append(char)
    return "".join(normalized_chars)


def _score_to_modifier(score: int) -> int:
    return (score - 10) // 2


def _sync_power_level(record: SessionRecord) -> None:
    """Recalculate power_level and rank_label after any state mutation."""
    try:
        power_level, rank_label = recalculate_power_and_rank(record.game_state)
        record.game_state.player.power_level = power_level
        record.game_state.player.rank_label = rank_label
    except Exception:
        # Never let power level recalc break the main flow.
        pass
