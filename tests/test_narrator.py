"""Tests for narrator prompt construction and async rendering flow."""

from __future__ import annotations

import asyncio

from server.llm.openai_compatible import LLMGatewayError
from server.narrative.narrator import NarratorEngine, build_narration_prompt
from server.schemas.core import (
    FanficMetaData,
    ExecutedEvent,
    GameState,
    PlayerState,
    WorldConfig,
    WorldGlossary,
    WorldNode,
    WorldTopology,
)


class FakeNarrationClient:
    """Async test double for the narrator LLM client."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, str]] = []

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        return self.response_text


class FailingNarrationClient:
    """Async test double that simulates an unavailable LLM gateway."""

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        raise LLMGatewayError("LLM 网关请求失败。")


def build_state() -> GameState:
    return GameState(
        session_id="session_narrator_01",
        player=PlayerState(
            stats={"stat_hp": 11, "stat_focus": 4},
            attributes={"attr_dex": 14},
            inventory={"item_pistol": 1, "item_temp_loot_0001": 1},
            temporary_items={"item_temp_loot_0001": "沾染咒力的制服纽扣"},
        ),
        current_location_id="location_rooftop",
        world_config=WorldConfig(
            world_id="world_cyberpunk",
            theme="cyberpunk_noir",
            fanfic_meta=FanficMetaData(
                base_ip="Original Cyberpunk",
                universe_type="Original",
                tone_and_style="neon noir",
            ),
            glossary=WorldGlossary(
                stats={
                    "stat_hp": "机体完整度",
                    "stat_focus": "神经负载",
                },
                damage_types={"dmg_kinetic": "动能冲击"},
                item_categories={"item_weapon": "武装"},
            ),
            starting_location="location_rooftop",
            key_npcs=["npc_fixer_01"],
            initial_quests=["quest_escape_the_dragnet"],
            topology=WorldTopology(
                start_node_id="location_rooftop",
                nodes={
                    "location_rooftop": WorldNode(
                        node_id="location_rooftop",
                        title="雨夜楼顶",
                        base_desc="霓虹在雨幕里被拉成破碎的光痕。",
                        hidden_detail_dc10=None,
                        deep_secret_dc18=None,
                        tags=["elevated"],
                    )
                },
                edges={"location_rooftop": []},
            ),
        ),
    )


def build_combat_events() -> list[ExecutedEvent]:
    return [
        ExecutedEvent(
            event_type="combat",
            is_success=True,
            actor="player",
            target="enemy_01",
            abstract_action="attack",
            result_tags=["critical_hit", "dmg_kinetic"],
        )
    ]


def build_loot_events() -> list[ExecutedEvent]:
    return [
        ExecutedEvent(
            event_type="loot",
            is_success=True,
            actor="player",
            target="二级咒灵的尸体",
            abstract_action="loot",
            result_tags=["loot_roll_17", "loot_total_18", "found_item_temp_loot_0001"],
        )
    ]


def build_exploration_events() -> list[ExecutedEvent]:
    return [
        ExecutedEvent(
            event_type="exploration",
            is_success=True,
            actor="player",
            target="后山的神秘山洞",
            abstract_action="discovery",
            result_tags=["new_location_discovered", "location_dyn_0001"],
        )
    ]


def test_narrator_prompt_injects_glossary_rules() -> None:
    prompt_bundle = build_narration_prompt(
        current_state=build_state(),
        events=build_combat_events(),
        user_input="我朝雨幕里开火。",
    )

    assert "当事实日志出现 `stat_hp` 时，请改写为“机体完整度”。" in prompt_bundle.system_prompt
    assert "当事实日志出现 `dmg_kinetic` 时，请改写为“动能冲击”。" in prompt_bundle.system_prompt


def test_narrator_prompt_contains_anti_hallucination_constraints() -> None:
    prompt_bundle = build_narration_prompt(
        current_state=build_state(),
        events=build_combat_events(),
        user_input="我朝雨幕里开火。",
    )

    assert "你必须严格服从【事实日志】" in prompt_bundle.system_prompt
    assert "你绝不可发明额外事实" in prompt_bundle.system_prompt
    assert "最终文本必须完全使用简体中文" in prompt_bundle.system_prompt


def test_narrator_prompt_contains_loot_rules_and_temp_item_mapping() -> None:
    prompt_bundle = build_narration_prompt(
        current_state=build_state(),
        events=build_loot_events(),
        user_input="我仔细搜查倒下的敌人尸体。",
    )

    assert "当事件为 loot 且成功时" in prompt_bundle.system_prompt
    assert "当事实日志提到 `item_temp_loot_0001` 时，请写成“沾染咒力的制服纽扣”。" in prompt_bundle.system_prompt


def test_narrator_prompt_contains_exploration_discovery_rules() -> None:
    prompt_bundle = build_narration_prompt(
        current_state=build_state(),
        events=build_exploration_events(),
        user_input="去后山的神秘山洞。",
    )

    assert "new_location_discovered" in prompt_bundle.system_prompt
    assert "base_desc" in prompt_bundle.user_prompt


def test_narrator_engine_returns_llm_text_from_built_prompt() -> None:
    client = FakeNarrationClient("枪声劈开雨幕，在楼顶炸开一道冷光。")
    narrator = NarratorEngine(client)

    text = asyncio.run(
        narrator.generate_narration(
            build_state(),
            build_combat_events(),
            "我朝雨幕里开火。",
        )
    )

    assert text == "枪声劈开雨幕，在楼顶炸开一道冷光。"
    assert "事实日志：" in client.calls[0]["user_prompt"]
    assert "当前世界主题：cyberpunk_noir" in client.calls[0]["system_prompt"]


def test_narrator_engine_falls_back_to_fact_locked_text_for_combat() -> None:
    narrator = NarratorEngine(FailingNarrationClient())

    text = asyncio.run(
        narrator.generate_narration(
            build_state(),
            build_combat_events(),
            "我朝雨幕里开火。",
        )
    )

    assert "动能冲击" in text
    assert "当前状态：" in text
    assert "dmg_kinetic" not in text


def test_narrator_engine_falls_back_to_fact_locked_text_for_loot() -> None:
    narrator = NarratorEngine(FailingNarrationClient())

    text = asyncio.run(
        narrator.generate_narration(
            build_state(),
            build_loot_events(),
            "我仔细搜查倒下的敌人尸体。",
        )
    )

    assert "沾染咒力的制服纽扣" in text
    assert "item_temp_loot_0001" not in text


def test_narrator_engine_falls_back_to_fact_locked_text_for_exploration() -> None:
    narrator = NarratorEngine(FailingNarrationClient())

    text = asyncio.run(
        narrator.generate_narration(
            build_state(),
            build_exploration_events(),
            "去后山的神秘山洞。",
        )
    )

    assert "雨夜楼顶" in text or "后山的神秘山洞" in text
