"""Pure combat resolution pipeline."""

from __future__ import annotations

import random
from typing import Any

from server.schemas.core import ExecutedEvent, GameState, MutationLog, RuntimeEntityState


DEFAULT_ATTACKER_ID = "player"
DEFAULT_ACTION = "attack"
DEFAULT_REACTION_ACTION = "counter_attack"
DEFAULT_DAMAGE_TYPE_KEY = "dmg_kinetic"
DEFAULT_HP_STAT_KEY = "stat_hp"
DEFAULT_ATTACK_ATTRIBUTE_KEY = "attr_dex"
DEFAULT_DEFENSE_ATTRIBUTE_KEY = "attr_dex"
DEFAULT_HIT_DC = 12
DEFAULT_BASE_DAMAGE = 5
DEFAULT_ENEMY_BASE_DAMAGE = 4
DEFAULT_UNARMED_WEAPON_KEY = "item_unarmed"


def resolve_combat(
    current_state: GameState,
    parameters: dict[str, Any],
) -> tuple[list[MutationLog], list[ExecutedEvent]]:
    """Resolve a combat turn into mutation logs and ordered fact events."""

    attacker_id = str(parameters.get("attacker_id", DEFAULT_ATTACKER_ID))
    target_id = str(parameters.get("target_id", "")).strip()
    weapon_key = _resolve_weapon_key(parameters)
    action_type = str(parameters.get("action_type", DEFAULT_ACTION))

    if attacker_id != DEFAULT_ATTACKER_ID:
        return [], [
            _build_event(
                is_success=False,
                actor=attacker_id,
                target=target_id or "unknown_target",
                abstract_action=action_type,
                result_tags=["unsupported_attacker"],
            )
        ]

    if not weapon_key or current_state.player.inventory.get(weapon_key, 0) <= 0:
        return [], [
            _build_event(
                is_success=False,
                actor=attacker_id,
                target=target_id or "unknown_target",
                abstract_action=action_type,
                result_tags=["invalid_weapon"],
            )
        ]

    target_entity = current_state.encounter_entities.get(target_id)
    if target_entity is None:
        return [], [
            _build_event(
                is_success=False,
                actor=attacker_id,
                target=target_id or "unknown_target",
                abstract_action=action_type,
                result_tags=["invalid_target"],
            )
        ]

    target_hp_stat_key = _as_str(
        parameters.get("target_hp_stat_key"),
        current_state.world_config.mechanics.get(
            "combat_target_hp_stat_key",
            DEFAULT_HP_STAT_KEY,
        ),
    )
    if target_hp_stat_key not in target_entity.stats:
        return [], [
            _build_event(
                is_success=False,
                actor=attacker_id,
                target=target_id,
                abstract_action=action_type,
                result_tags=["invalid_target_state"],
            )
        ]

    resource_logs, resource_error_tag = _build_resource_cost_logs(current_state, parameters)
    if resource_error_tag is not None:
        return [], [
            _build_event(
                is_success=False,
                actor=attacker_id,
                target=target_id,
                abstract_action=action_type,
                result_tags=[resource_error_tag],
            )
        ]

    logs = list(resource_logs)
    player_event, target_remaining_hp = _resolve_player_action(
        current_state=current_state,
        target_entity=target_entity,
        target_id=target_id,
        target_hp_stat_key=target_hp_stat_key,
        parameters=parameters,
        action_type=action_type,
        logs=logs,
    )
    events = [player_event]

    if target_remaining_hp <= 0:
        return logs, events

    enemy_event = _resolve_enemy_reaction(
        current_state=current_state,
        enemy_entity=target_entity,
        enemy_id=target_id,
        parameters=parameters,
        logs=logs,
    )
    events.append(enemy_event)
    return logs, events


def _resolve_player_action(
    *,
    current_state: GameState,
    target_entity: RuntimeEntityState,
    target_id: str,
    target_hp_stat_key: str,
    parameters: dict[str, Any],
    action_type: str,
    logs: list[MutationLog],
) -> tuple[ExecutedEvent, int]:
    roll = random.randint(1, 20)
    attack_attribute_key = _as_str(
        parameters.get("attack_attribute_key"),
        current_state.world_config.mechanics.get(
            "combat_attack_attribute_key",
            DEFAULT_ATTACK_ATTRIBUTE_KEY,
        ),
    )
    attribute_score = current_state.player.attributes.get(attack_attribute_key, 10)
    attribute_modifier = _score_to_modifier(attribute_score)
    attack_bonus = _as_int(parameters.get("attack_bonus"), 0)
    dc = _as_int(
        parameters.get("target_dc"),
        _as_int(current_state.world_config.mechanics.get("combat_hit_dc"), DEFAULT_HIT_DC),
    )

    result_tags: list[str] = []
    is_success = _is_successful_hit(
        roll=roll,
        modifier=attribute_modifier + attack_bonus,
        dc=dc,
        result_tags=result_tags,
    )

    if not is_success:
        if "missed" not in result_tags:
            result_tags.append("missed")
        return (
            _build_event(
                is_success=False,
                actor=DEFAULT_ATTACKER_ID,
                target=target_id,
                abstract_action=action_type,
                result_tags=result_tags,
            ),
            target_entity.stats[target_hp_stat_key],
        )

    damage_type_key = _as_str(
        parameters.get("damage_type_key"),
        current_state.world_config.mechanics.get(
            "combat_default_damage_type",
            DEFAULT_DAMAGE_TYPE_KEY,
        ),
    )
    base_damage = _as_int(
        parameters.get("base_damage"),
        _as_int(current_state.world_config.mechanics.get("combat_base_damage"), DEFAULT_BASE_DAMAGE),
    )
    damage_bonus = _as_int(parameters.get("damage_bonus"), 0) + max(attribute_modifier, 0)
    damage = max(0, base_damage + damage_bonus)

    if roll == 20:
        damage += _as_int(parameters.get("critical_bonus_damage"), base_damage)

    logs.append(
        MutationLog(
            action="subtract",
            target_path=f"encounter_entities.{target_id}.stats.{target_hp_stat_key}",
            value=damage,
            reason="combat_damage",
        )
    )

    if damage_type_key not in result_tags:
        result_tags.append(damage_type_key)

    target_remaining_hp = target_entity.stats[target_hp_stat_key] - damage
    if target_remaining_hp <= 0:
        result_tags.append("target_killed")
        logs.append(
            MutationLog(
                action="delete",
                target_path=f"encounter_entities.{target_id}",
                value=target_id,
                reason="combat_target_killed",
            )
        )

    return (
        _build_event(
            is_success=True,
            actor=DEFAULT_ATTACKER_ID,
            target=target_id,
            abstract_action=action_type,
            result_tags=result_tags,
        ),
        target_remaining_hp,
    )


def _resolve_enemy_reaction(
    *,
    current_state: GameState,
    enemy_entity: RuntimeEntityState,
    enemy_id: str,
    parameters: dict[str, Any],
    logs: list[MutationLog],
) -> ExecutedEvent:
    player_hp_stat_key = _as_str(
        parameters.get("player_hp_stat_key"),
        current_state.world_config.mechanics.get(
            "combat_player_hp_stat_key",
            DEFAULT_HP_STAT_KEY,
        ),
    )
    if player_hp_stat_key not in current_state.player.stats:
        return _build_event(
            is_success=False,
            actor=enemy_id,
            target=DEFAULT_ATTACKER_ID,
            abstract_action=DEFAULT_REACTION_ACTION,
            result_tags=["invalid_player_state"],
        )

    enemy_attack_attribute_key = _as_str(
        parameters.get("enemy_attack_attribute_key"),
        current_state.world_config.mechanics.get(
            "combat_enemy_attack_attribute_key",
            DEFAULT_ATTACK_ATTRIBUTE_KEY,
        ),
    )
    enemy_attribute_score = enemy_entity.attributes.get(enemy_attack_attribute_key, 10)
    enemy_attack_modifier = _score_to_modifier(enemy_attribute_score)
    enemy_attack_bonus = _as_int(
        current_state.world_config.mechanics.get("combat_enemy_attack_bonus"),
        0,
    )

    player_defense_attribute_key = _as_str(
        parameters.get("player_defense_attribute_key"),
        current_state.world_config.mechanics.get(
            "combat_player_defense_attribute_key",
            DEFAULT_DEFENSE_ATTRIBUTE_KEY,
        ),
    )
    player_defense_score = current_state.player.attributes.get(player_defense_attribute_key, 10)
    player_defense_modifier = _score_to_modifier(player_defense_score)
    reaction_dc = _as_int(
        current_state.world_config.mechanics.get("combat_enemy_hit_dc"),
        DEFAULT_HIT_DC,
    ) + max(player_defense_modifier, 0)

    roll = random.randint(1, 20)
    result_tags: list[str] = []
    is_success = _is_successful_hit(
        roll=roll,
        modifier=enemy_attack_modifier + enemy_attack_bonus,
        dc=reaction_dc,
        result_tags=result_tags,
    )

    if not is_success:
        if "missed" not in result_tags:
            result_tags.append("missed")
        if "dodged_by_player" not in result_tags:
            result_tags.append("dodged_by_player")
        return _build_event(
            is_success=False,
            actor=enemy_id,
            target=DEFAULT_ATTACKER_ID,
            abstract_action=DEFAULT_REACTION_ACTION,
            result_tags=result_tags,
        )

    damage_type_key = _as_str(
        current_state.world_config.mechanics.get(
            "combat_enemy_damage_type",
            DEFAULT_DAMAGE_TYPE_KEY,
        ),
        DEFAULT_DAMAGE_TYPE_KEY,
    )
    base_damage = _as_int(
        current_state.world_config.mechanics.get(
            "combat_enemy_base_damage",
            DEFAULT_ENEMY_BASE_DAMAGE,
        ),
        DEFAULT_ENEMY_BASE_DAMAGE,
    )
    damage_bonus = max(enemy_attack_modifier, 0)
    damage = max(0, base_damage + damage_bonus)

    if roll == 20:
        damage += base_damage

    logs.append(
        MutationLog(
            action="subtract",
            target_path=f"player.stats.{player_hp_stat_key}",
            value=damage,
            reason="combat_enemy_reaction_damage",
        )
    )

    if damage_type_key not in result_tags:
        result_tags.append(damage_type_key)

    if current_state.player.stats[player_hp_stat_key] - damage <= 0:
        result_tags.append("player_downed")

    return _build_event(
        is_success=True,
        actor=enemy_id,
        target=DEFAULT_ATTACKER_ID,
        abstract_action=DEFAULT_REACTION_ACTION,
        result_tags=result_tags,
    )


def _resolve_weapon_key(parameters: dict[str, Any]) -> str:
    raw_value = parameters.get(
        "weapon_key",
        parameters.get("weapon_id", DEFAULT_UNARMED_WEAPON_KEY),
    )
    if raw_value is None:
        return ""
    return str(raw_value).strip()


def _build_resource_cost_logs(
    current_state: GameState,
    parameters: dict[str, Any],
) -> tuple[list[MutationLog], str | None]:
    resource_key = parameters.get("resource_cost_key")
    resource_amount = _as_int(parameters.get("resource_cost_amount"), 0)
    resource_container = parameters.get("resource_cost_container", "stats")

    if not resource_key or resource_amount <= 0:
        return [], None

    normalized_key = str(resource_key).strip()
    normalized_container = str(resource_container).strip()

    if normalized_container == "inventory":
        current_value = current_state.player.inventory.get(normalized_key)
        target_path = f"player.inventory.{normalized_key}"
    else:
        current_value = current_state.player.stats.get(normalized_key)
        target_path = f"player.stats.{normalized_key}"

    if current_value is None or current_value < resource_amount:
        return [], "insufficient_resources"

    return [
        MutationLog(
            action="subtract",
            target_path=target_path,
            value=resource_amount,
            reason="combat_resource_cost",
        )
    ], None


def _is_successful_hit(
    *,
    roll: int,
    modifier: int,
    dc: int,
    result_tags: list[str],
) -> bool:
    if roll == 1:
        result_tags.extend(["missed", "critical_miss"])
        return False

    if roll == 20:
        result_tags.append("critical_hit")
        return True

    return roll + modifier >= dc


def _score_to_modifier(score: int) -> int:
    return (score - 10) // 2


def _build_event(
    *,
    is_success: bool,
    actor: str,
    target: str,
    abstract_action: str,
    result_tags: list[str],
) -> ExecutedEvent:
    return ExecutedEvent(
        event_type="combat",
        is_success=is_success,
        actor=actor,
        target=target,
        abstract_action=abstract_action,
        result_tags=result_tags,
    )


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(str(value))


def _as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value).strip()
