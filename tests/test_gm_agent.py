"""Tests for the agentic GM loop and runtime tools."""

from __future__ import annotations

import asyncio
import json

from server.agent.gm import GameMasterAgent
from server.agent.runtime_tools import execute_runtime_tool
from server.generators.loot_generator import LootPool
from server.runtime.session_store import SessionStore
from server.schemas.core import FanficMetaData, WorldConfig, WorldGlossary, WorldNode


class FakeToolCallingClient:
    """Simple test double that returns queued assistant turns."""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def complete_chat(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
            }
        )
        return self._responses.pop(0)


class FakeMapGenerator:
    """Small deterministic map generator for runtime tool tests."""

    def generate_node(
        self,
        current_state,
        *,
        current_node_id: str,
        target_node_id: str,
        target_name: str,
    ) -> WorldNode:
        del current_state, current_node_id
        return WorldNode(
            node_id=target_node_id,
            title=target_name,
            base_desc=f"A newly opened path leads into {target_name}.",
            hidden_detail_dc10=None,
            deep_secret_dc18=None,
            tags=["generated_location"],
        )


class FakeLootGenerator:
    """Small deterministic loot generator for runtime tool tests."""

    def __init__(self, pool: LootPool) -> None:
        self._pool = pool

    def generate_pool(
        self,
        *,
        world_config,
        target_name: str,
        user_input: str,
        temp_key_factory,
    ) -> LootPool:
        del world_config, target_name, user_input, temp_key_factory
        return self._pool


def build_world_config() -> WorldConfig:
    return WorldConfig(
        world_id="world_agent",
        theme="urban_fantasy",
        fanfic_meta=FanficMetaData(
            base_ip="JJK",
            universe_type="同人",
            tone_and_style="紧张、凌厉、沉浸",
        ),
        world_book={
            "campaign_context": {
                "era_and_timeline": "东京现代咒术时代",
                "macro_world_state": "咒灵在城市阴影中活跃，术师组织维持着脆弱秩序。",
                "looming_crisis": "涩谷方向的异常波动正在抬升，任何拖延都可能酿成更大伤亡。",
                "opening_scene": "你在废弃站台边缘喘息，远处的咒灵正沿着铁轨逼近。",
            }
        },
        glossary=WorldGlossary(
            stats={"stat_hp": "生命值", "stat_mp": "咒力"},
            damage_types={"dmg_kinetic": "冲击"},
            item_categories={"item_weapon": "武器"},
        ),
        starting_location="废弃站台",
        key_npcs=["辅助监督", "巡逻咒灵"],
        initial_quests=["活着离开站台区"],
    )

def _build_record():
    store = SessionStore()
    record = store.create_session(build_world_config())
    record.game_state.player.stats["stat_mp"] = 12
    return record


def _make_long_narration(seed: str) -> str:
    fragment = (
        "Impact ripples through the ground as dust and splinters whip past your face. "
        "You force your breathing to stay controlled while tracking every shoulder twitch and foot shift from your opponent. "
        "Each collision sends another shock through your arms, and the spectators around the field fall into sharp, breathless silence. "
        "You feel the strain in your muscles, the sting in your skin, and the pressure of a decision that has to be made now. "
    )
    return f"{seed}\n\n" + (fragment * 10)

def test_session_creation_seeds_runtime_quest_and_encounter_logs() -> None:
    record = _build_record()

    assert "quest_01" in record.game_state.quest_log
    assert record.game_state.quest_log["quest_01"].status == "active"
    assert "encounter_opening" in record.game_state.encounter_log
    assert record.game_state.encounter_log["encounter_opening"].status == "active"


def test_gm_agent_resolves_compound_turn_with_multiple_tool_calls(monkeypatch) -> None:
    record = _build_record()
    client = FakeToolCallingClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "roll_d20_check",
                        "arguments": json.dumps(
                            {
                                "action_name": "假装投降",
                                "attribute_used": "意志",
                                "difficulty_class": 12,
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "id": "call_2",
                        "name": "roll_d20_check",
                        "arguments": json.dumps(
                            {
                                "action_name": "吊灯砸击",
                                "attribute_used": "敏捷",
                                "difficulty_class": 14,
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "id": "call_3",
                        "name": "modify_game_state",
                        "arguments": json.dumps(
                            {
                                "target_entity": "enemy_01",
                                "hp_delta": -8,
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "id": "call_4",
                        "name": "modify_game_state",
                        "arguments": json.dumps(
                            {
                                "target_entity": "player",
                                "mp_delta": -5,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            },
            {
                "content": _make_long_narration(
                    "你先压低肩线伪装示弱，下一瞬骤然前冲，借着坠落重物的掩护逼得对手后撤。"
                ),
                "tool_calls": [],
            },
        ]
    )
    agent = GameMasterAgent(client)

    rolls = iter([15, 18])
    monkeypatch.setattr(
        "server.agent.runtime_tools.random.randint",
        lambda _start, _end: next(rolls),
    )

    result = asyncio.run(
        agent.run_turn(
            record=record,
            user_input="我假装投降，然后突然出手攻击并立刻给自己加护盾。",
        )
    )

    assert "你先压低肩线伪装示弱" in result.narration
    assert len(result.executed_events) == 4
    assert [event.event_type for event in result.executed_events[:2]] == [
        "skill_check",
        "skill_check",
    ]
    assert any(event.event_type == "state_change" for event in result.executed_events)
    assert record.game_state.player.stats["stat_mp"] == 7
    assert record.game_state.encounter_entities["enemy_01"].stats["stat_hp"] == 8
    assert len(client.calls) == 2
    tool_names = [tool["function"]["name"] for tool in client.calls[0]["tools"]]
    assert {
        "roll_d20_check",
        "modify_game_state",
        "inventory_manager",
        "update_encounter_state",
        "resolve_combat_action",
        "resolve_exploration_action",
        "resolve_loot_action",
    }.issubset(set(tool_names))

def test_gm_agent_can_finish_a_turn_with_specialized_combat_tool(monkeypatch) -> None:
    record = _build_record()
    client = FakeToolCallingClient(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "resolve_combat_action",
                        "arguments": json.dumps(
                            {
                                "target_id": "enemy_01",
                                "action_type": "attack",
                            }
                        ),
                    }
                ],
            },
            {
                "content": _make_long_narration(
                    "你猛地贴近，冲击在瞬间炸开，敌人被你硬生生逼退两步，场面骤然失衡。"
                ),
                "tool_calls": [],
            },
        ]
    )
    agent = GameMasterAgent(client)

    rolls = iter([15, 10])
    monkeypatch.setattr(
        "server.pipelines.combat.random.randint",
        lambda _start, _end: next(rolls),
    )

    result = asyncio.run(
        agent.run_turn(
            record=record,
            user_input="我冲上去正面打击。",
        )
    )

    assert "你猛地贴近" in result.narration
    assert any(event.event_type == "combat" for event in result.executed_events)
    assert record.game_state.encounter_entities["enemy_01"].stats["stat_hp"] < 16

def test_resolve_combat_action_uses_pipeline_and_registers_loot(monkeypatch) -> None:
    record = _build_record()
    monkeypatch.setattr("server.pipelines.combat.random.randint", lambda _low, _high: 15)

    result = execute_runtime_tool(
        record,
        "resolve_combat_action",
        {
            "target_id": "enemy_01",
            "action_type": "attack",
            "base_damage": 15,
        },
    )

    assert any(event.event_type == "combat" for event in result.executed_events)
    assert "enemy_01" not in record.game_state.encounter_entities
    assert record.game_state.active_encounter is None
    assert "corpse_enemy_01" in record.lootable_targets


def test_resolve_exploration_action_discovers_new_location(monkeypatch) -> None:
    record = _build_record()
    monkeypatch.setattr(
        "server.agent.runtime_tools.get_map_generator",
        lambda: FakeMapGenerator(),
    )

    result = execute_runtime_tool(
        record,
        "resolve_exploration_action",
        {
            "target_location": "service_tunnel",
            "action_type": "travel",
        },
    )

    assert result.observation["discovered_new_location"] is True
    assert record.game_state.current_location_id.startswith("location_dyn_")
    assert record.current_location_node is not None
    assert record.current_location_node.title == "service_tunnel"
    assert record.game_state.active_encounter is None
    assert record.game_state.encounter_entities == {}


def test_resolve_loot_action_generates_items_and_consumes_corpse(monkeypatch) -> None:
    record = _build_record()
    record.register_defeated_enemy_loot_target("enemy_01")
    monkeypatch.setattr("server.pipelines.loot.random.randint", lambda _low, _high: 19)
    monkeypatch.setattr(
        "server.agent.runtime_tools.get_loot_generator",
        lambda: FakeLootGenerator(
            LootPool.model_validate(
                {
                    "candidates": [
                        {
                            "temp_key": "item_temp_loot_0001",
                            "name": "cursed_badge_fragment",
                            "dc": 10,
                            "type": "item_clue",
                        }
                    ]
                }
            )
        ),
    )

    result = execute_runtime_tool(
        record,
        "resolve_loot_action",
        {
            "target_id": "corpse_enemy_01",
            "action_type": "loot",
            "search_intent": "search the fallen corpse carefully",
        },
    )

    assert result.executed_events[0].event_type == "loot"
    assert "corpse_enemy_01" not in record.lootable_targets
    assert record.game_state.player.inventory["item_temp_loot_0001"] == 1
    assert record.game_state.player.temporary_items["item_temp_loot_0001"] == "cursed_badge_fragment"


def test_update_quest_state_advances_existing_runtime_quest() -> None:
    record = _build_record()

    result = execute_runtime_tool(
        record,
        "update_quest_state",
        {
            "quest_id": "quest_01",
            "status": "completed",
            "summary": "The player successfully broke out of the opening danger zone.",
            "progress": 1,
            "progress_delta": 1,
        },
    )

    assert result.executed_events[0].event_type == "quest"
    assert record.game_state.quest_log["quest_01"].status == "completed"
    assert record.game_state.quest_log["quest_01"].progress == 1


def test_update_quest_state_accepts_zero_padded_quest_id() -> None:
    record = _build_record()

    result = execute_runtime_tool(
        record,
        "update_quest_state",
        {
            "quest_id": "quest_001",
            "status": "completed",
            "summary": "The opening test is complete.",
            "progress_delta": 1,
        },
    )

    assert result.executed_events[0].event_type == "quest"
    assert "quest_001" not in record.game_state.quest_log
    assert record.game_state.quest_log["quest_01"].status == "completed"
    assert record.game_state.quest_log["quest_01"].progress == 1


def test_update_quest_state_requires_explicit_create_flag() -> None:
    record = _build_record()
    original_count = len(record.game_state.quest_log)

    result = execute_runtime_tool(
        record,
        "update_quest_state",
        {
            "quest_title": "A completely new objective",
            "status": "active",
            "summary": "Should not create implicitly.",
            "progress_delta": 1,
        },
    )

    assert result.executed_events[0].event_type == "tool_error"
    assert len(record.game_state.quest_log) == original_count


def test_update_encounter_state_can_pause_combat_scene() -> None:
    record = _build_record()

    result = execute_runtime_tool(
        record,
        "update_encounter_state",
        {
            "status": "resolved",
            "summary": "Kakashi lowers his hand and shifts to direct questioning.",
            "label": "Kakashi Standoff",
            "clear_hostiles": False,
        },
    )

    assert result.executed_events[0].event_type == "encounter"
    assert record.game_state.active_encounter is None
    assert record.game_state.encounter_log["encounter_opening"].status == "resolved"
    assert record.game_state.encounter_log["encounter_opening"].label == "Kakashi Standoff"

def test_gm_agent_rewrites_rigid_menu_ending_into_natural_hook() -> None:
    record = _build_record()
    client = FakeToolCallingClient(
        [
            {
                "content": "卡卡西后撤半步，盯着你。请选择你的行动：A攻击 B防守 C逃跑。",
                "tool_calls": [],
            },
            {
                "content": _make_long_narration(
                    "卡卡西后撤半步，视线像刀一样落在你手上未散去的查克拉纹路上。训练场安静得只剩风声，他缓缓抬起苦无，声音压得很低：‘这招你从哪学来的？’"
                ),
                "tool_calls": [],
            },
        ]
    )
    agent = GameMasterAgent(client)

    result = asyncio.run(
        agent.run_turn(
            record=record,
            user_input="我抬手凝聚查克拉，停在原地看着卡卡西。",
        )
    )

    assert "请选择" not in result.narration
    assert "这招你从哪学来的" in result.narration
    assert len(client.calls) == 2

def test_inventory_manager_adds_and_removes_temporary_items() -> None:
    record = _build_record()

    add_result = execute_runtime_tool(
        record,
        "inventory_manager",
        {
            "action": "add",
            "item_name": "涓存椂鎶ょ",
        },
    )

    item_key = add_result.observation["item_key"]
    assert record.game_state.player.inventory[item_key] == 1
    assert record.game_state.player.temporary_items[item_key] == "涓存椂鎶ょ"

    remove_result = execute_runtime_tool(
        record,
        "inventory_manager",
        {
            "action": "remove",
            "item_name": "涓存椂鎶ょ",
        },
    )

    assert remove_result.observation["quantity"] == 0
    assert item_key not in record.game_state.player.temporary_items







