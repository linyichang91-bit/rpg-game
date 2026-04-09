"""Runtime tool registry and execution helpers for the GM agent."""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from server.runtime.session_store import SessionRecord
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
                        "attribute_used": {
                            "type": "string",
                            "description": "Human-readable attribute label such as 体能, 敏捷, 魔力, 意志.",
                        },
                        "difficulty_class": {
                            "type": "integer",
                            "description": "DC between 1 and 20 chosen by the GM.",
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    "required": ["action_name", "attribute_used", "difficulty_class"],
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
    ]


def execute_runtime_tool(
    record: SessionRecord,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    """Execute one registered tool against the working session record."""

    if tool_name == "roll_d20_check":
        return _roll_d20_check(record, arguments)
    if tool_name == "modify_game_state":
        return _modify_game_state(record, arguments)
    if tool_name == "inventory_manager":
        return _inventory_manager(record, arguments)

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
    attribute_used = _clean_text(arguments.get("attribute_used"), fallback="generic")
    difficulty_class = max(1, min(20, _coerce_int(arguments.get("difficulty_class"), 10)))
    resolved_attribute, modifier = _resolve_check_modifier(record, attribute_used)

    roll_result = random.randint(1, 20)
    total = roll_result + modifier
    critical = roll_result in {1, 20}
    is_success = roll_result == 20 or (roll_result != 1 and total >= difficulty_class)

    result_tags = [
        f"attribute:{resolved_attribute}",
        f"dc:{difficulty_class}",
        f"roll:{roll_result}",
        f"total:{total}",
    ]
    if roll_result == 20:
        result_tags.append("critical_success")
    elif roll_result == 1:
        result_tags.append("critical_failure")
    else:
        result_tags.append("success" if is_success else "failure")

    return ToolExecutionResult(
        observation={
            "roll_result": roll_result,
            "modifier": modifier,
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


def _apply_logs(record: SessionRecord, logs: list[MutationLog]) -> None:
    if not logs:
        return
    record.game_state = apply_mutations(record.game_state, logs)
    record.sync_after_state_update()


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


def _resolve_check_modifier(record: SessionRecord, attribute_used: str) -> tuple[str, int]:
    normalized = _normalize(attribute_used)
    player_attributes = record.game_state.player.attributes

    if attribute_used in player_attributes:
        return attribute_used, _score_to_modifier(player_attributes[attribute_used])

    alias_groups = (
        (
            "attr_power",
            {
                "\u4f53\u80fd",
                "\u529b\u91cf",
                "\u529b\u6c14",
                "\u7206\u53d1",
                "\u8fd1\u6218",
                "power",
                "strength",
            },
        ),
        (
            "attr_dex",
            {
                "\u654f\u6377",
                "\u901f\u5ea6",
                "\u95ea\u907f",
                "\u7075\u5de7",
                "\u53cd\u5e94",
                "dex",
                "dexterity",
            },
        ),
        (
            "attr_will",
            {
                "\u9b54\u529b",
                "\u6cd5\u529b",
                "\u5492\u529b",
                "\u7cbe\u795e",
                "\u610f\u5fd7",
                "will",
                "mana",
            },
        ),
        (
            "attr_focus",
            {
                "\u611f\u77e5",
                "\u89c2\u5bdf",
                "\u4e13\u6ce8",
                "\u6d1e\u5bdf",
                "focus",
                "perception",
            },
        ),
    )

    for attribute_key, aliases in alias_groups:
        if attribute_key in player_attributes and (
            normalized == _normalize(attribute_key)
            or normalized in {_normalize(alias) for alias in aliases}
        ):
            return attribute_key, _score_to_modifier(player_attributes[attribute_key])

    if "attr_dex" in player_attributes:
        return "attr_dex", _score_to_modifier(player_attributes["attr_dex"])
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


def _clean_text(value: Any, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    stripped = value.strip()
    return stripped or fallback


def _normalize(value: str) -> str:
    return "".join(value.strip().lower().split())


def _score_to_modifier(score: int) -> int:
    return (score - 10) // 2
