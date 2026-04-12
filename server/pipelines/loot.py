"""Hybrid loot and interaction resolution pipeline."""

from __future__ import annotations

import random
from typing import Any

from server.generators.loot_generator import LootPool
from server.schemas.core import ExecutedEvent, GameState, MutationLog


DEFAULT_ACTION = "loot"
DEFAULT_BONUS_ATTRIBUTES = (
    "stat_insight",
    "stat_tenacity",
    "attr_perception",
    "attr_luck",
    "attr_focus",
    "attr_will",
)

DEFAULT_CATEGORY_LABELS = {
    "item_weapon": "武器",
    "item_consumable": "消耗品",
    "item_material": "素材",
    "item_junk": "杂物",
    "item_clue": "线索",
}


def resolve_loot(
    current_state: GameState,
    parameters: dict[str, Any],
    *,
    loot_pool: LootPool,
    target_label: str,
) -> tuple[list[MutationLog], ExecutedEvent]:
    """Resolve a loot action into mutation logs and one fact event."""

    roll = random.randint(1, 20)
    bonus = _resolve_loot_bonus(current_state, parameters)
    total = roll + bonus
    action_type = str(parameters.get("action_type", DEFAULT_ACTION))

    eligible_candidates = [
        candidate for candidate in loot_pool.candidates if candidate.dc <= total
    ]
    selected_candidates = _select_awarded_candidates(eligible_candidates)

    if not selected_candidates:
        result_tags = [f"loot_roll_{roll}", f"loot_total_{total}", "found_nothing"]
        if roll == 1:
            result_tags.append("critical_search_failure")
        return [], ExecutedEvent(
            event_type="loot",
            is_success=False,
            actor="player",
            target=target_label,
            abstract_action=action_type,
            result_tags=result_tags,
        )

    logs: list[MutationLog] = []
    result_tags = [f"loot_roll_{roll}", f"loot_total_{total}"]
    for candidate in selected_candidates:
        logs.append(
            MutationLog(
                action="set",
                target_path=f"player.temporary_items.{candidate.temp_key}",
                value=candidate.name,
                reason="loot_temp_item_registration",
            )
        )

        if candidate.type not in current_state.world_config.glossary.item_categories:
            logs.append(
                MutationLog(
                    action="set",
                    target_path=f"world_config.glossary.item_categories.{candidate.type}",
                    value=DEFAULT_CATEGORY_LABELS.get(candidate.type, "杂项"),
                    reason="loot_category_registration",
                )
            )

        logs.append(
            MutationLog(
                action="add",
                target_path=f"player.inventory.{candidate.temp_key}",
                value=1,
                reason="loot_item_gain",
            )
        )
        result_tags.append(f"found_{candidate.temp_key}")

    return logs, ExecutedEvent(
        event_type="loot",
        is_success=True,
        actor="player",
        target=target_label,
        abstract_action=action_type,
        result_tags=result_tags,
    )


def _resolve_loot_bonus(current_state: GameState, parameters: dict[str, Any]) -> int:
    if parameters.get("attribute_key") is not None:
        attribute_keys = (str(parameters["attribute_key"]).strip(),)
    else:
        attribute_keys = DEFAULT_BONUS_ATTRIBUTES

    bonus = _as_int(parameters.get("bonus"), 0)
    for attribute_key in attribute_keys:
        resolved_score = _resolve_attribute_score(current_state.player.attributes, attribute_key)
        if resolved_score is not None:
            bonus += _score_to_modifier(resolved_score)
            break

    return bonus


def _select_awarded_candidates(eligible_candidates: list[Any]) -> list[Any]:
    """Select which eligible candidates are actually awarded.

    Weighted random selection: lower-DC (more common) items have higher weight,
    but even high-DC items can be picked.  At most 2 items are awarded; if
    only 1 or 2 candidates are eligible, all are awarded.
    """
    if len(eligible_candidates) <= 2:
        return list(eligible_candidates)

    # Weight proportional to (21 - dc).  DC 1 → weight 20, DC 20 → weight 1.
    # This makes common items more likely while still allowing rare finds.
    weights = [max(1, 21 - c.dc) for c in eligible_candidates]
    total_weight = sum(weights)

    selected: list[Any] = []
    remaining_indices = list(range(len(eligible_candidates)))
    remaining_weights = list(weights)

    for _ in range(2):
        if not remaining_indices:
            break
        # Re-normalise weights after each pick
        current_total = sum(remaining_weights)
        r = random.uniform(0, current_total)
        cumulative = 0.0
        picked_index_in_remaining = 0
        for i, w in enumerate(remaining_weights):
            cumulative += w
            if r <= cumulative:
                picked_index_in_remaining = i
                break

        original_index = remaining_indices.pop(picked_index_in_remaining)
        remaining_weights.pop(picked_index_in_remaining)
        selected.append(eligible_candidates[original_index])

    return selected


def _score_to_modifier(score: int) -> int:
    return (score - 10) // 2


def _resolve_attribute_score(attributes: dict[str, int], requested_key: str) -> int | None:
    candidate_groups = (
        ("stat_insight", "attr_focus", "attr_perception"),
        ("stat_tenacity", "attr_will"),
        ("stat_agility", "attr_dex"),
        ("stat_power", "attr_power"),
        ("stat_presence", "attr_presence", "charisma"),
    )

    if requested_key in attributes:
        return attributes[requested_key]

    normalized = str(requested_key).strip().lower()
    for candidates in candidate_groups:
        normalized_candidates = {candidate.lower() for candidate in candidates}
        if normalized not in normalized_candidates:
            continue
        for candidate in candidates:
            if candidate in attributes:
                return attributes[candidate]

    return None


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(str(value))
