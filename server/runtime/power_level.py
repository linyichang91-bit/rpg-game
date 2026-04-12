"""Power level calculation and rank resolution utilities."""

from __future__ import annotations

from collections.abc import Mapping

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

_ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "stat_power": ("attr_power", "power", "strength", "force"),
    "stat_agility": ("attr_dex", "attr_agility", "dex", "dexterity", "speed", "agility"),
    "stat_insight": ("attr_focus", "focus", "perception", "insight", "awareness"),
    "stat_tenacity": ("attr_will", "will", "mana", "tenacity", "spirit", "resolve"),
    "stat_presence": ("attr_presence", "presence", "charisma", "leadership", "charm"),
}

_TOTAL_ATTRIBUTE_WEIGHT = sum(_ATTRIBUTE_WEIGHTS.values()) or 1.0

# Bonus per growth level beyond level 1.
_LEVEL_POWER_BONUS = 3.0

# Bonus per skill point (total of all skill values).
_SKILL_POWER_FACTOR = 0.3


def compute_power_level(game_state: GameState) -> int:
    """Derive an abstract combat power score from player attributes, level, and skills."""

    player = game_state.player
    return compute_attributes_power_level(
        player.attributes,
        level=player.growth.level,
        skill_total=sum(player.skills.values()) if player.skills else 0,
    )


def compute_attributes_power_level(
    attributes: Mapping[str, int],
    *,
    level: int = 1,
    skill_total: int = 0,
) -> int:
    """Convert raw attributes into a normalized power score.

    The attribute component uses a weighted average instead of a raw sum, so a
    typical starting sheet remains close to the low-tier benchmarks used by the
    world generator.
    """

    base = _compute_attribute_base_power(attributes)
    level_bonus = max(0, level - 1) * _LEVEL_POWER_BONUS
    skill_bonus = max(0, skill_total) * _SKILL_POWER_FACTOR
    raw_power = base + level_bonus + skill_bonus
    return max(0, round(raw_power))


def resolve_rank_label(power_level: int, power_tiers: list[PowerTier]) -> str:
    """Map a power_level to the highest qualifying tier label.

    Tiers must be sorted by min_power ascending. If the list is empty,
    returns "未定级".
    """
    if not power_tiers:
        return "未定级"

    sorted_tiers = sorted(power_tiers, key=lambda tier: tier.min_power)

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


def _compute_attribute_base_power(attributes: Mapping[str, int]) -> float:
    weighted_total = 0.0
    consumed_keys: set[str] = set()

    for attribute_key, weight in _ATTRIBUTE_WEIGHTS.items():
        attribute_value, matched_key = _resolve_attribute_value(attributes, attribute_key)
        weighted_total += max(0, attribute_value) * weight
        consumed_keys.add(attribute_key)
        if matched_key is not None:
            consumed_keys.add(matched_key)

    # Non-core attributes still matter, but only as a small bonus on top.
    for attribute_key, attribute_value in attributes.items():
        if attribute_key in consumed_keys or attribute_value <= 0:
            continue
        weighted_total += attribute_value * 0.3

    return weighted_total / _TOTAL_ATTRIBUTE_WEIGHT


def _resolve_attribute_value(
    attributes: Mapping[str, int],
    requested_key: str,
) -> tuple[int, str | None]:
    if requested_key in attributes:
        return attributes[requested_key], requested_key

    for alias in _ATTRIBUTE_ALIASES.get(requested_key, ()):
        if alias in attributes:
            return attributes[alias], alias

    return 0, None
