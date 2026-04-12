"""Power level calculation and rank resolution utilities."""

from __future__ import annotations

from server.schemas.core import GameState, PowerTier


# Weight multipliers for each core attribute when computing power_level.
# These are tuned so a starting character (attributes ~10-12, level 1) lands
# around power_level 10-15, which sits in the lowest non-trivial tier.
_ATTRIBUTE_WEIGHTS: dict[str, float] = {
    "stat_power": 1.0,
    "stat_agility": 0.8,
    "stat_insight": 0.7,
    "stat_tenacity": 0.8,
    "stat_presence": 0.6,
}

# Bonus per growth level beyond level 1.
_LEVEL_POWER_BONUS = 3.0

# Bonus per skill point (total of all skill values).
_SKILL_POWER_FACTOR = 0.3


def compute_power_level(game_state: GameState) -> int:
    """Derive an abstract combat power score from player attributes, level, and skills.

    Formula:
        base = sum(attr_value * weight for each core attribute)
        level_bonus = (growth.level - 1) * LEVEL_POWER_BONUS
        skill_bonus = sum(skill_values) * SKILL_POWER_FACTOR
        power_level = round(base + level_bonus + skill_bonus)

    The result is always >= 0.
    """
    player = game_state.player
    attributes = player.attributes
    growth = player.growth
    skills = player.skills

    base = 0.0
    for attr_key, weight in _ATTRIBUTE_WEIGHTS.items():
        attr_value = attributes.get(attr_key, 0)
        base += attr_value * weight

    # Add contribution from any non-core attributes (diminished weight).
    for attr_key, attr_value in attributes.items():
        if attr_key not in _ATTRIBUTE_WEIGHTS and attr_value > 0:
            base += attr_value * 0.3

    level_bonus = max(0, growth.level - 1) * _LEVEL_POWER_BONUS

    skill_total = sum(skills.values()) if skills else 0
    skill_bonus = skill_total * _SKILL_POWER_FACTOR

    raw_power = base + level_bonus + skill_bonus
    return max(0, round(raw_power))


def resolve_rank_label(power_level: int, power_tiers: list[PowerTier]) -> str:
    """Map a power_level to the highest qualifying tier label.

    Tiers must be sorted by min_power ascending. If the list is empty,
    returns "未定级".
    """
    if not power_tiers:
        return "未定级"

    # Sort tiers by min_power ascending just in case.
    sorted_tiers = sorted(power_tiers, key=lambda t: t.min_power)

    # Find the highest tier the player qualifies for.
    matched_label = sorted_tiers[0].label
    for tier in sorted_tiers:
        if power_level >= tier.min_power:
            matched_label = tier.label
        else:
            break

    return matched_label


def recalculate_power_and_rank(game_state: GameState) -> tuple[int, str]:
    """Convenience: compute power_level and resolve rank_label together."""
    power_level = compute_power_level(game_state)
    power_tiers = game_state.world_config.world_book.power_scaling.power_tiers
    rank_label = resolve_rank_label(power_level, power_tiers)
    return power_level, rank_label
