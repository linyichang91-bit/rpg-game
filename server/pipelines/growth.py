"""Growth and evolution resolution pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.schemas.core import ExecutedEvent, GameState, MutationLog


DEFAULT_STAT_BOOST_AMOUNT = 2
DEFAULT_MASTERY_DELTA = 1
DEFAULT_XP_REWARDS = {
    "stat_boost": 25,
    "new_skill": 40,
    "mastery_up": 15,
}


@dataclass
class GrowthResolution:
    """Structured result for a triggered growth beat."""

    observation: dict[str, Any]
    executed_event: ExecutedEvent
    mutation_logs: list[MutationLog]


def resolve_growth(
    current_state: GameState,
    parameters: dict[str, Any],
) -> GrowthResolution:
    """Resolve a growth trigger into mutation logs and a fact event."""

    growth_type = _clean_text(parameters.get("growth_type"), fallback="")
    reason = _clean_text(parameters.get("reason"), fallback="growth_trigger")

    if growth_type not in {"stat_boost", "new_skill", "mastery_up"}:
        return GrowthResolution(
            observation={
                "status": "error",
                "reason": "invalid_growth_type",
                "growth_type": growth_type or "unknown",
            },
            executed_event=ExecutedEvent(
                event_type="growth",
                is_success=False,
                actor="player",
                target="self",
                abstract_action="trigger_growth",
                result_tags=["invalid_growth_type"],
            ),
            mutation_logs=[],
        )

    growth_state = current_state.player.growth
    xp_gain = _coerce_int(parameters.get("xp_gain"), DEFAULT_XP_REWARDS[growth_type])
    logs: list[MutationLog] = []
    observation: dict[str, Any] = {
        "status": "updated",
        "growth_type": growth_type,
        "reason": reason,
    }
    result_tags = [f"growth_type:{growth_type}"]

    if growth_type == "stat_boost":
        attribute_key = _clean_text(
            parameters.get("attribute_key") or parameters.get("attribute_used"),
            fallback="stat_power",
        )
        amount = max(1, _coerce_int(parameters.get("amount"), DEFAULT_STAT_BOOST_AMOUNT))
        current_value = current_state.player.attributes.get(attribute_key)
        next_attribute_value = amount if current_value is None else current_value + amount
        logs.append(
            MutationLog(
                action="set" if current_value is None else "add",
                target_path=f"player.attributes.{attribute_key}",
                value=amount if current_value is None else amount,
                reason="growth_stat_boost",
            )
        )
        observation.update(
            {
                "attribute_key": attribute_key,
                "attribute_value": next_attribute_value,
                "amount": amount,
            }
        )
        result_tags.append(f"attribute:{attribute_key}")
    elif growth_type == "new_skill":
        skill_key = _clean_text(parameters.get("skill_key"), fallback="")
        if not skill_key:
            skill_key = _next_skill_key(current_state)
        skill_label = _clean_text(parameters.get("skill_label"), fallback=skill_key)
        next_skill_value = max(1, current_state.player.skills.get(skill_key, 0) or 1)
        logs.extend(
            [
                MutationLog(
                    action="set",
                    target_path=f"player.skills.{skill_key}",
                    value=next_skill_value,
                    reason="growth_new_skill",
                ),
                MutationLog(
                    action="set",
                    target_path=f"player.skill_labels.{skill_key}",
                    value=skill_label,
                    reason="growth_new_skill_label",
                ),
            ]
        )
        observation.update(
            {
                "skill_key": skill_key,
                "skill_label": skill_label,
                "skill_level": next_skill_value,
            }
        )
        result_tags.append(f"skill:{skill_key}")
    else:
        skill_key = _clean_text(parameters.get("skill_key") or parameters.get("attribute_key"), fallback="")
        if not skill_key:
            skill_key = _next_skill_key(current_state)
        skill_label = _clean_text(parameters.get("skill_label"), fallback=skill_key)
        mastery_delta = max(1, _coerce_int(parameters.get("mastery_delta"), DEFAULT_MASTERY_DELTA))
        current_skill_value = current_state.player.skills.get(skill_key, 0)
        next_skill_value = mastery_delta if current_skill_value == 0 else current_skill_value + mastery_delta
        logs.append(
            MutationLog(
                action="set" if current_skill_value == 0 else "add",
                target_path=f"player.skills.{skill_key}",
                value=mastery_delta,
                reason="growth_mastery_up",
            )
        )
        if skill_label and skill_key not in current_state.player.skill_labels:
            logs.append(
                MutationLog(
                    action="set",
                    target_path=f"player.skill_labels.{skill_key}",
                    value=skill_label,
                    reason="growth_mastery_up_label",
                )
            )
        xp_gain += max(0, mastery_delta - 1) * 5
        observation.update(
            {
                "skill_key": skill_key,
                "skill_label": skill_label,
                "skill_level": next_skill_value,
                "mastery_delta": mastery_delta,
            }
        )
        result_tags.append(f"skill:{skill_key}")
        result_tags.append(f"mastery_delta:{mastery_delta}")

    next_xp = max(0, growth_state.xp + xp_gain)
    next_level = _growth_level_from_xp(next_xp)
    next_proficiency_bonus = _proficiency_bonus_from_level(next_level)
    next_unspent_points = growth_state.unspent_stat_points + max(0, next_level - growth_state.level)

    logs[:0] = [
        MutationLog(
            action="set",
            target_path="player.growth.xp",
            value=next_xp,
            reason="growth_xp_update",
        ),
        MutationLog(
            action="set",
            target_path="player.growth.level",
            value=next_level,
            reason="growth_level_update",
        ),
        MutationLog(
            action="set",
            target_path="player.growth.proficiency_bonus",
            value=next_proficiency_bonus,
            reason="growth_proficiency_update",
        ),
        MutationLog(
            action="set",
            target_path="player.growth.unspent_stat_points",
            value=next_unspent_points,
            reason="growth_stat_point_update",
        ),
        MutationLog(
            action="set",
            target_path="player.growth.last_growth_reason",
            value=reason,
            reason="growth_reason_update",
        ),
    ]

    observation.update(
        {
            "xp": next_xp,
            "level": next_level,
            "proficiency_bonus": next_proficiency_bonus,
            "unspent_stat_points": next_unspent_points,
        }
    )
    if next_level > growth_state.level:
        result_tags.append(f"level_up:{next_level - growth_state.level}")
    result_tags.append(f"xp_gain:{xp_gain}")
    result_tags.append(f"proficiency_bonus:{next_proficiency_bonus}")

    return GrowthResolution(
        observation=observation,
        executed_event=ExecutedEvent(
            event_type="growth",
            is_success=True,
            actor="player",
            target=observation.get("attribute_key") or observation.get("skill_key") or "self",
            abstract_action="trigger_growth",
            result_tags=result_tags,
        ),
        mutation_logs=logs,
    )


def _growth_level_from_xp(xp: int) -> int:
    return max(1, 1 + xp // 100)


def _proficiency_bonus_from_level(level: int) -> int:
    return max(2, 2 + max(0, level - 1) // 4)


def _next_skill_key(current_state: GameState) -> str:
    existing_numbers = [
        int(skill_key.removeprefix("skill_"))
        for skill_key in current_state.player.skills
        if skill_key.startswith("skill_") and skill_key.removeprefix("skill_").isdigit()
    ]
    return f"skill_{max(existing_numbers, default=0) + 1:02d}"


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return default
    return int(text)


def _clean_text(value: Any, *, fallback: str) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else fallback
    if value is None:
        return fallback
    cleaned = str(value).strip()
    return cleaned if cleaned else fallback
