"""Hybrid loot and interaction resolution pipeline."""

from __future__ import annotations

import random
from typing import Any

from server.generators.loot_generator import LootPool
from server.schemas.core import ExecutedEvent, GameState, MutationLog


DEFAULT_ACTION = "loot"
DEFAULT_BONUS_ATTRIBUTES = (
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
        if attribute_key in current_state.player.attributes:
            bonus += _score_to_modifier(current_state.player.attributes[attribute_key])
            break

    return bonus


def _select_awarded_candidates(eligible_candidates: list[Any]) -> list[Any]:
    if len(eligible_candidates) <= 2:
        return eligible_candidates

    # Reward stronger rolls with the most difficult discoveries that were still beaten.
    return sorted(eligible_candidates, key=lambda candidate: candidate.dc, reverse=True)[:2]


def _score_to_modifier(score: int) -> int:
    return (score - 10) // 2


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(str(value))
