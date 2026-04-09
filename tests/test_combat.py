"""Acceptance tests for the combat pipeline."""

from __future__ import annotations

from server.pipelines.combat import resolve_combat
from server.schemas.core import (
    FanficMetaData,
    GameState,
    PlayerState,
    RuntimeEntityState,
    WorldConfig,
    WorldGlossary,
)


def build_state() -> GameState:
    return GameState(
        session_id="session_combat_01",
        player=PlayerState(
            stats={"stat_hp": 30, "stat_stamina": 8},
            attributes={"attr_dex": 14},
            inventory={"item_pistol": 1},
            temporary_items={},
        ),
        current_location_id="location_checkpoint",
        active_encounter="encounter_boar",
        encounter_entities={
            "enemy_01": RuntimeEntityState(
                stats={"stat_hp": 16},
                attributes={"attr_dex": 8},
                tags=["enemy"],
            )
        },
        world_config=WorldConfig(
            world_id="world_gamma",
            theme="frontier_ruins",
            fanfic_meta=FanficMetaData(
                base_ip="Original Frontier",
                universe_type="Original",
                tone_and_style="harsh and desperate",
            ),
            world_book={
                "campaign_context": {
                    "era_and_timeline": "前线遗迹纪年，补给线断裂后的第七周",
                    "macro_world_state": "残存据点正为了最后几条交通线互相提防，野外到处是失控的猎杀者。",
                    "looming_crisis": "检查站守不住的话，整条撤离线都会被撕开缺口。",
                    "opening_scene": "你蹲在半塌的检查站掩体后装弹，远处尘雾里忽然冲出一头带血的獠牙怪物。",
                }
            },
            glossary=WorldGlossary(
                stats={"stat_hp": "Hull", "stat_stamina": "Drive"},
                damage_types={"dmg_kinetic": "Impact"},
                item_categories={"item_weapon": "Weapons"},
            ),
            starting_location="location_checkpoint",
            key_npcs=["npc_scavenger_01", "enemy_01"],
            initial_quests=["quest_hold_the_checkpoint"],
            mechanics={"combat_hit_dc": 12, "combat_base_damage": 5},
        ),
    )


def test_resolve_combat_blocks_hallucinated_weapon(monkeypatch) -> None:
    state = build_state()
    monkeypatch.setattr("server.pipelines.combat.random.randint", lambda _low, _high: 20)

    logs, events = resolve_combat(
        state,
        {
            "attacker_id": "player",
            "target_id": "enemy_01",
            "weapon_key": "item_fake_cannon",
            "action_type": "attack",
        },
    )

    assert logs == []
    assert len(events) == 1
    assert events[0].is_success is False
    assert "invalid_weapon" in events[0].result_tags


def test_resolve_combat_hits_then_enemy_reacts(monkeypatch) -> None:
    state = build_state()
    rolls = iter([15, 10])
    monkeypatch.setattr(
        "server.pipelines.combat.random.randint",
        lambda _low, _high: next(rolls),
    )

    logs, events = resolve_combat(
        state,
        {
            "attacker_id": "player",
            "target_id": "enemy_01",
            "weapon_key": "item_pistol",
            "action_type": "attack",
        },
    )

    hp_logs = [log for log in logs if log.target_path == "encounter_entities.enemy_01.stats.stat_hp"]

    assert len(hp_logs) == 1
    assert hp_logs[0].action == "subtract"
    assert hp_logs[0].value == 7
    assert len(events) == 2
    assert events[0].actor == "player"
    assert events[0].is_success is True
    assert "dmg_kinetic" in events[0].result_tags
    assert events[1].actor == "enemy_01"
    assert events[1].abstract_action == "counter_attack"
    assert events[1].is_success is False
    assert "dodged_by_player" in events[1].result_tags


def test_resolve_combat_handles_critical_miss_then_enemy_hits(monkeypatch) -> None:
    state = build_state()
    rolls = iter([1, 18])
    monkeypatch.setattr(
        "server.pipelines.combat.random.randint",
        lambda _low, _high: next(rolls),
    )

    logs, events = resolve_combat(
        state,
        {
            "attacker_id": "player",
            "target_id": "enemy_01",
            "weapon_key": "item_pistol",
            "action_type": "attack",
        },
    )

    player_hp_logs = [log for log in logs if log.target_path == "player.stats.stat_hp"]

    assert all(log.target_path != "encounter_entities.enemy_01.stats.stat_hp" for log in logs)
    assert len(events) == 2
    assert events[0].is_success is False
    assert "critical_miss" in events[0].result_tags
    assert "missed" in events[0].result_tags
    assert events[1].actor == "enemy_01"
    assert events[1].is_success is True
    assert "dmg_kinetic" in events[1].result_tags
    assert len(player_hp_logs) == 1
    assert player_hp_logs[0].action == "subtract"
    assert player_hp_logs[0].value == 4


def test_resolve_combat_skips_reaction_when_target_is_killed(monkeypatch) -> None:
    state = build_state()
    monkeypatch.setattr("server.pipelines.combat.random.randint", lambda _low, _high: 15)

    logs, events = resolve_combat(
        state,
        {
            "attacker_id": "player",
            "target_id": "enemy_01",
            "weapon_key": "item_pistol",
            "action_type": "attack",
            "base_damage": 15,
        },
    )

    delete_logs = [log for log in logs if log.action == "delete"]

    assert len(events) == 1
    assert events[0].is_success is True
    assert "target_killed" in events[0].result_tags
    assert len(delete_logs) == 1
