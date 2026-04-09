"""Acceptance tests for the loot generator and loot pipeline."""

from __future__ import annotations

import json

from server.api.app import _resolve_loot_turn
from server.generators.loot_generator import LootGenerator, LootPool
from server.pipelines.loot import resolve_loot
from server.runtime.session_store import SessionStore
from server.schemas.core import (
    FanficMetaData,
    GameState,
    PlayerState,
    RuntimeEntityState,
    WorldConfig,
    WorldGlossary,
)
from server.state.mutator import apply_mutations


class FakeStructuredJSONClient:
    """Test double that returns predetermined JSON payloads."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> str:
        del system_prompt, user_prompt, response_schema
        return self._responses.pop(0)


def build_state() -> GameState:
    return GameState(
        session_id="session_loot_01",
        player=PlayerState(
            stats={"stat_hp": 20},
            attributes={"attr_will": 12},
            inventory={"item_weapon_01": 1},
            temporary_items={},
        ),
        current_location_id="location_ruined_corridor",
        active_encounter=None,
        encounter_entities={
            "enemy_01": RuntimeEntityState(
                stats={"stat_hp": 0},
                attributes={"attr_dex": 8},
                tags=["enemy"],
            )
        },
        world_config=WorldConfig(
            world_id="world_loot",
            theme="dark_fantasy_ruin",
            fanfic_meta=FanficMetaData(
                base_ip="Jujutsu Kaisen",
                universe_type="AU",
                tone_and_style="ominous and tense",
            ),
            world_book={
                "campaign_context": {
                    "era_and_timeline": "咒术高专冬季封锁周，第4天夜里",
                    "macro_world_state": "废弃城区已经被诅咒污染，高专派出的清剿小队在黑夜里逐片推进。",
                    "looming_crisis": "污染核心正在苏醒，如果不能尽快摸清遗迹内部结构，整片街区都会失控。",
                    "opening_scene": "你踩着碎玻璃进入坍塌走廊，远处墙缝渗出黑色咒雾，脚边忽然滚来一枚带血的校徽。",
                }
            },
            glossary=WorldGlossary(
                stats={"stat_hp": "生命值"},
                damage_types={"dmg_kinetic": "冲击"},
                item_categories={"item_weapon": "武器"},
            ),
            starting_location="location_ruined_corridor",
            key_npcs=["二级咒灵"],
            initial_quests=["活下去"],
        ),
    )


def test_loot_generator_normalizes_candidates_and_assigns_unique_temp_keys() -> None:
    generator = LootGenerator(
        FakeStructuredJSONClient(
            [
                json.dumps(
                    {
                        "candidates": [
                            {
                                "temp_key": "ignored_model_key",
                                "name": "干瘪的宿傩手指（残片）",
                                "dc": 18,
                                "type": "item_material",
                            },
                            {
                                "name": "沾染咒力的制服纽扣",
                                "dc": 8,
                                "type": "item_junk",
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
    )
    counter = 0

    def temp_key_factory() -> str:
        nonlocal counter
        counter += 1
        return f"item_temp_loot_{counter:04d}"

    pool = generator.generate_pool(
        world_config=build_state().world_config,
        target_name="倒下的咒灵残骸",
        user_input="我仔细搜查倒下的敌人尸体",
        temp_key_factory=temp_key_factory,
    )

    assert [candidate.temp_key for candidate in pool.candidates] == [
        "item_temp_loot_0001",
        "item_temp_loot_0002",
    ]
    assert pool.candidates[0].name == "干瘪的宿傩手指（残片）"
    assert pool.candidates[1].type == "item_junk"


def test_resolve_loot_registers_temporary_items_and_inventory(monkeypatch) -> None:
    state = build_state()
    monkeypatch.setattr("server.pipelines.loot.random.randint", lambda _low, _high: 17)

    loot_pool = LootPool.model_validate(
        {
            "candidates": [
                {
                    "temp_key": "item_temp_loot_0001",
                    "name": "干瘪的宿傩手指（残片）",
                    "dc": 18,
                    "type": "item_material",
                },
                {
                    "temp_key": "item_temp_loot_0002",
                    "name": "沾染咒力的制服纽扣",
                    "dc": 8,
                    "type": "item_junk",
                },
            ]
        }
    )

    logs, event = resolve_loot(
        state,
        {"action_type": "loot"},
        loot_pool=loot_pool,
        target_label="二级咒灵的尸体",
    )
    next_state = apply_mutations(state, logs)

    assert event.event_type == "loot"
    assert event.is_success is True
    assert "loot_roll_17" in event.result_tags
    assert "loot_total_18" in event.result_tags
    assert "found_item_temp_loot_0001" in event.result_tags
    assert "found_item_temp_loot_0002" in event.result_tags
    assert next_state.player.inventory["item_temp_loot_0001"] == 1
    assert next_state.player.inventory["item_temp_loot_0002"] == 1
    assert next_state.player.temporary_items["item_temp_loot_0001"] == "干瘪的宿傩手指（残片）"
    assert next_state.player.temporary_items["item_temp_loot_0002"] == "沾染咒力的制服纽扣"
    assert next_state.world_config.glossary.item_categories["item_material"] == "素材"
    assert next_state.world_config.glossary.item_categories["item_junk"] == "杂物"


def test_resolve_loot_can_fail_cleanly_when_roll_is_too_low(monkeypatch) -> None:
    state = build_state()
    monkeypatch.setattr("server.pipelines.loot.random.randint", lambda _low, _high: 3)

    loot_pool = LootPool.model_validate(
        {
            "candidates": [
                {
                    "temp_key": "item_temp_loot_0001",
                    "name": "沾染咒力的制服纽扣",
                    "dc": 8,
                    "type": "item_junk",
                }
            ]
        }
    )

    logs, event = resolve_loot(
        state,
        {"action_type": "loot"},
        loot_pool=loot_pool,
        target_label="二级咒灵的尸体",
    )

    assert logs == []
    assert event.is_success is False
    assert "found_nothing" in event.result_tags
    assert "loot_roll_3" in event.result_tags


def test_resolve_loot_turn_blocks_nonexistent_corpse_target() -> None:
    store = SessionStore()
    record = store.create_session(build_state().world_config)

    logs, event, consumed_target_id = _resolve_loot_turn(
        record,
        {
            "action_type": "loot",
            "raw_target_text": "倒下的敌人尸体",
        },
        user_input="我仔细搜查倒下的敌人尸体",
    )

    assert logs == []
    assert consumed_target_id is None
    assert event.is_success is False
    assert "invalid_loot_target" in event.result_tags


def test_resolve_loot_turn_blocks_non_lootable_explicit_target_id() -> None:
    store = SessionStore()
    record = store.create_session(build_state().world_config)

    logs, event, consumed_target_id = _resolve_loot_turn(
        record,
        {
            "action_type": "loot",
            "target_id": "enemy_01",
        },
        user_input="我搜查敌人尸体",
    )

    assert logs == []
    assert consumed_target_id is None
    assert event.is_success is False
    assert "invalid_loot_target" in event.result_tags
