"""Pure combat resolution pipeline."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from server.runtime.power_level import compute_attributes_power_level
from server.schemas.core import ExecutedEvent, GameState, MutationLog, RuntimeEntityState


DEFAULT_ATTACKER_ID = "player"
DEFAULT_ACTION = "attack"
DEFAULT_REACTION_ACTION = "counter_attack"
DEFAULT_DAMAGE_TYPE_KEY = "dmg_kinetic"
DEFAULT_HP_STAT_KEY = "stat_hp"
DEFAULT_ATTACK_ATTRIBUTE_KEY = "stat_agility"
DEFAULT_DEFENSE_ATTRIBUTE_KEY = "stat_agility"
DEFAULT_HIT_DC = 12
DEFAULT_BASE_DAMAGE = 5
DEFAULT_ENEMY_BASE_DAMAGE = 4
DEFAULT_UNARMED_WEAPON_KEY = "item_unarmed"
DEFAULT_POWER_GAP_THRESHOLD = 20
DEFAULT_POWER_GAP_STEP_ATTACK_BONUS = 2
DEFAULT_POWER_GAP_STEP_DAMAGE_BONUS = 3
MAX_POWER_GAP_STEPS = 4


@dataclass(frozen=True)
class PowerGapAdjustment:
    attack_bonus: int
    damage_bonus: int
    step_count: int
    overwhelming: bool
    overmatched: bool


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
    attribute_score = _resolve_attribute_score(
        current_state.player.attributes,
        attack_attribute_key,
    )
    attribute_modifier = _score_to_modifier(attribute_score)
    power_gap = _resolve_power_gap_adjustment(
        current_state=current_state,
        attacker_attributes=current_state.player.attributes,
        defender_attributes=target_entity.attributes,
        attacker_level=current_state.player.growth.level,
        attacker_skill_total=_sum_skill_levels(current_state.player.skills),
    )
    attack_bonus = _as_int(parameters.get("attack_bonus"), 0)
    dc = _as_int(
        parameters.get("target_dc"),
        _as_int(current_state.world_config.mechanics.get("combat_hit_dc"), DEFAULT_HIT_DC),
    )

    result_tags = _build_power_gap_tags(power_gap)
    forced_outcome = _resolve_forced_hit_outcome(
        roll=roll,
        power_gap=power_gap,
        result_tags=result_tags,
    )

    if forced_outcome is None:
        is_success = _is_successful_hit(
            roll=roll,
            modifier=attribute_modifier + attack_bonus + power_gap.attack_bonus,
            dc=dc,
            result_tags=result_tags,
        )
    else:
        is_success = forced_outcome

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
    damage_bonus = (
        _as_int(parameters.get("damage_bonus"), 0)
        + max(attribute_modifier, 0)
        + power_gap.damage_bonus
    )
    damage = max(0, base_damage + damage_bonus)

    # Critical hit: add doubled base damage on top of the normal hit
    critical_bonus = _apply_critical_damage(
        roll=roll,
        base_damage=base_damage,
        result_tags=result_tags,
        extra_critical_bonus=_as_int(parameters.get("critical_bonus_damage"), 0),
    )
    damage += critical_bonus
    if power_gap.overwhelming:
        damage = max(damage, base_damage + max(2, power_gap.damage_bonus))

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
    enemy_attribute_score = _resolve_attribute_score(
        enemy_entity.attributes,
        enemy_attack_attribute_key,
    )
    enemy_attack_modifier = _score_to_modifier(enemy_attribute_score)
    power_gap = _resolve_power_gap_adjustment(
        current_state=current_state,
        attacker_attributes=enemy_entity.attributes,
        defender_attributes=current_state.player.attributes,
        defender_level=current_state.player.growth.level,
        defender_skill_total=_sum_skill_levels(current_state.player.skills),
    )
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
    player_defense_score = _resolve_attribute_score(
        current_state.player.attributes,
        player_defense_attribute_key,
    )
    player_defense_modifier = _score_to_modifier(player_defense_score)
    reaction_dc = _as_int(
        current_state.world_config.mechanics.get("combat_enemy_hit_dc"),
        DEFAULT_HIT_DC,
    ) + max(player_defense_modifier, 0)

    roll = random.randint(1, 20)
    result_tags = _build_power_gap_tags(power_gap)
    forced_outcome = _resolve_forced_hit_outcome(
        roll=roll,
        power_gap=power_gap,
        result_tags=result_tags,
    )

    if forced_outcome is None:
        is_success = _is_successful_hit(
            roll=roll,
            modifier=enemy_attack_modifier + enemy_attack_bonus + power_gap.attack_bonus,
            dc=reaction_dc,
            result_tags=result_tags,
        )
    else:
        is_success = forced_outcome

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
    damage_bonus = max(enemy_attack_modifier, 0) + power_gap.damage_bonus
    damage = max(0, base_damage + damage_bonus)

    # Critical hit: enemy doubles base damage on natural 20
    critical_bonus = _apply_critical_damage(
        roll=roll,
        base_damage=base_damage,
        result_tags=result_tags,
    )
    damage += critical_bonus
    if power_gap.overwhelming:
        damage = max(damage, base_damage + max(2, power_gap.damage_bonus))

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


def _resolve_power_gap_adjustment(
    *,
    current_state: GameState,
    attacker_attributes: dict[str, int],
    defender_attributes: dict[str, int],
    attacker_level: int = 1,
    defender_level: int = 1,
    attacker_skill_total: int = 0,
    defender_skill_total: int = 0,
) -> PowerGapAdjustment:
    power_scaling = current_state.world_config.world_book.power_scaling
    danger_gap = max(1, power_scaling.danger_gap_threshold or DEFAULT_POWER_GAP_THRESHOLD)
    impossible_gap = max(danger_gap, power_scaling.impossible_gap_threshold or danger_gap * 2)

    attacker_power = compute_attributes_power_level(
        attacker_attributes,
        level=attacker_level,
        skill_total=attacker_skill_total,
    )
    defender_power = compute_attributes_power_level(
        defender_attributes,
        level=defender_level,
        skill_total=defender_skill_total,
    )
    gap = attacker_power - defender_power
    step_count = max(-MAX_POWER_GAP_STEPS, min(MAX_POWER_GAP_STEPS, int(gap / danger_gap)))

    return PowerGapAdjustment(
        attack_bonus=step_count * DEFAULT_POWER_GAP_STEP_ATTACK_BONUS,
        damage_bonus=step_count * DEFAULT_POWER_GAP_STEP_DAMAGE_BONUS,
        step_count=step_count,
        overwhelming=gap >= impossible_gap,
        overmatched=gap <= -impossible_gap,
    )


def _build_power_gap_tags(power_gap: PowerGapAdjustment) -> list[str]:
    tags: list[str] = []
    if power_gap.step_count > 0:
        tags.append(f"power_gap_advantage:{power_gap.step_count}")
    elif power_gap.step_count < 0:
        tags.append(f"power_gap_penalty:{abs(power_gap.step_count)}")

    if power_gap.overwhelming:
        tags.append("power_gap_overwhelming")
    elif power_gap.overmatched:
        tags.append("power_gap_overmatched")

    return tags


def _resolve_forced_hit_outcome(
    *,
    roll: int,
    power_gap: PowerGapAdjustment,
    result_tags: list[str],
) -> bool | None:
    if roll == 1:
        result_tags.extend(["missed", "critical_miss"])
        return False

    if power_gap.overwhelming:
        if roll == 20 and "critical_hit" not in result_tags:
            result_tags.append("critical_hit")
        return True

    if power_gap.overmatched and roll != 20:
        if "power_gap_blocked" not in result_tags:
            result_tags.append("power_gap_blocked")
        if "missed" not in result_tags:
            result_tags.append("missed")
        return False

    return None


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

    success = roll + modifier >= dc
    if roll == 20 and success:
        result_tags.append("critical_hit")
    elif roll == 20:
        result_tags.append("critical_roll")
    return success


def _apply_critical_damage(
    *,
    roll: int,
    base_damage: int,
    result_tags: list[str],
    extra_critical_bonus: int = 0,
) -> int:
    """Calculate bonus damage for a critical hit.

    On a natural 20 that also beats the DC (``critical_hit`` tag), damage
    is doubled.  A natural 20 that misses the DC (``critical_roll``) still
    lands a glancing blow worth half the base damage (rounded down).

    The *extra_critical_bonus* parameter allows the caller to add an
    additional flat bonus on top of the doubled base (e.g. the GM's
    ``critical_bonus_damage`` parameter).
    """
    if "critical_hit" in result_tags:
        # Full critical: double base damage + optional bonus
        return base_damage + extra_critical_bonus
    if "critical_roll" in result_tags:
        # Glancing critical: half base, minimum 1
        return max(1, base_damage // 2)
    return 0


def _score_to_modifier(score: int) -> int:
    return (score - 10) // 2


def _sum_skill_levels(skills: dict[str, int]) -> int:
    return sum(skills.values()) if skills else 0


def _resolve_attribute_score(attributes: dict[str, int], requested_key: str) -> int:
    if requested_key in attributes:
        return attributes[requested_key]

    for candidate in _attribute_aliases_for(requested_key):
        if candidate in attributes:
            return attributes[candidate]

    return 10


def _attribute_aliases_for(requested_key: str) -> tuple[str, ...]:
    normalized = str(requested_key).strip().lower()
    if normalized in {"stat_agility", "attr_dex", "agility", "dex", "dexterity"}:
        return ("stat_agility", "attr_dex")
    if normalized in {"stat_power", "attr_power", "power", "strength"}:
        return ("stat_power", "attr_power")
    if normalized in {"stat_tenacity", "attr_will", "tenacity", "will"}:
        return ("stat_tenacity", "attr_will")
    if normalized in {"stat_insight", "attr_focus", "insight", "focus", "perception"}:
        return ("stat_insight", "attr_focus")
    if normalized in {"stat_presence", "presence", "charisma"}:
        return ("stat_presence",)
    return ()


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
