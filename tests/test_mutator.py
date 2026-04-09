"""Acceptance tests for the core state mutator."""

from __future__ import annotations

import pytest

from server.schemas.core import FanficMetaData, GameState, MutationLog, PlayerState, WorldConfig, WorldGlossary
from server.state.mutator import PathResolutionError, apply_mutations


def build_state() -> GameState:
    return GameState(
        session_id="session_01",
        player=PlayerState(
            stats={"stat_hp": 30, "stat_energy": 12},
            attributes={"attr_power": 5},
            inventory={"item_weapon_01": 1},
            temporary_items={},
        ),
        current_location_id="location_start",
        active_encounter="encounter_alpha",
        world_config=WorldConfig(
            world_id="world_alpha",
            theme="cyber_wasteland",
            fanfic_meta=FanficMetaData(
                base_ip="Original Cyberpunk",
                universe_type="Original",
                tone_and_style="grim and tactical",
            ),
            glossary=WorldGlossary(
                stats={"stat_hp": "Integrity", "stat_energy": "Charge"},
                damage_types={"dmg_kinetic": "Kinetic Shock"},
                item_categories={"item_weapon": "Weapons"},
            ),
            starting_location="location_start",
            key_npcs=["npc_handler_01"],
            initial_quests=["quest_survive_shift"],
            mechanics={"dice_sides": 20},
        ),
    )


def test_apply_mutations_subtracts_deep_value_without_mutating_original() -> None:
    original_state = build_state()
    logs = [
        MutationLog(
            action="subtract",
            target_path="player.stats.stat_hp",
            value=7,
            reason="combat_damage",
        )
    ]

    updated_state = apply_mutations(original_state, logs)

    assert updated_state.player.stats["stat_hp"] == 23
    assert original_state.player.stats["stat_hp"] == 30
    assert updated_state is not original_state


def test_apply_mutations_sets_temporary_item_mapping() -> None:
    original_state = build_state()
    logs = [
        MutationLog(
            action="set",
            target_path="player.temporary_items.item_temp_01",
            value="stale_banana",
            reason="player_improvised_weapon",
        )
    ]

    updated_state = apply_mutations(original_state, logs)

    assert updated_state.player.temporary_items["item_temp_01"] == "stale_banana"
    assert "item_temp_01" not in original_state.player.temporary_items


def test_apply_mutations_raises_for_invalid_path_without_polluting_input_state() -> None:
    original_state = build_state()
    logs = [
        MutationLog(
            action="set",
            target_path="player.nonexistent_branch.item_temp_02",
            value="ghost_entry",
            reason="invalid_test",
        )
    ]

    with pytest.raises(PathResolutionError):
        apply_mutations(original_state, logs)

    assert original_state.player.temporary_items == {}


def test_apply_mutations_appends_new_topology_edge() -> None:
    original_state = build_state()
    logs = [
        MutationLog(
            action="append",
            target_path="world_config.topology.edges.location_start",
            value="location_hidden_passage",
            reason="discover_new_path",
        )
    ]

    updated_state = apply_mutations(original_state, logs)

    assert updated_state.world_config.topology.edges["location_start"] == [
        "location_hidden_passage"
    ]
    assert original_state.world_config.topology.edges == {}
