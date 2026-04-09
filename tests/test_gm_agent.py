"""Tests for the agentic GM loop and runtime tools."""

from __future__ import annotations

import asyncio
import json

from server.agent.gm import GameMasterAgent
from server.agent.runtime_tools import execute_runtime_tool
from server.runtime.session_store import SessionStore
from server.schemas.core import FanficMetaData, WorldConfig, WorldGlossary


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
                "era_and_timeline": "东京，现代咒术时代",
                "macro_world_state": "咒灵在城市阴影里活跃，术师组织维持着脆弱秩序。",
                "looming_crisis": "涩谷方向的异常波动正在抬升，任何拖延都可能酿成更大伤亡。",
                "opening_scene": "你在废弃站台边缘喘息，远处的咒灵正顺着铁轨逼近。",
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
                "content": "你先压低肩膀装出屈服的样子，下一瞬突然暴起，借着坠落重物逼得对手狼狈后撤，同时一层护持自身的力量也在体内迅速消耗。",
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
            user_input="我假装投降，然后突然用魔杖射击天花板上的吊灯砸他，接着给自己加个护盾。",
        )
    )

    assert "压低肩膀装出屈服" in result.narration
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
    assert tool_names == [
        "roll_d20_check",
        "modify_game_state",
        "inventory_manager",
    ]


def test_inventory_manager_adds_and_removes_temporary_items() -> None:
    record = _build_record()

    add_result = execute_runtime_tool(
        record,
        "inventory_manager",
        {
            "action": "add",
            "item_name": "临时护符",
        },
    )

    item_key = add_result.observation["item_key"]
    assert record.game_state.player.inventory[item_key] == 1
    assert record.game_state.player.temporary_items[item_key] == "临时护符"

    remove_result = execute_runtime_tool(
        record,
        "inventory_manager",
        {
            "action": "remove",
            "item_name": "临时护符",
        },
    )

    assert remove_result.observation["quantity"] == 0
    assert item_key not in record.game_state.player.temporary_items
