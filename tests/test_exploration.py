"""Acceptance tests for dynamic exploration and map generation."""

from __future__ import annotations

import json

from server.generators.map_generator import DynamicMapGenerator
from server.llm.openai_compatible import LLMGatewayError
from server.pipelines.exploration import resolve_exploration
from server.schemas.core import (
    FanficMetaData,
    GameState,
    PlayerState,
    WorldConfig,
    WorldGlossary,
    WorldNode,
    WorldTopology,
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


class FailingStructuredJSONClient:
    """Test double that simulates an unavailable LLM gateway."""

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> str:
        del system_prompt, user_prompt, response_schema
        raise LLMGatewayError("LLM 网关请求失败。")


def build_state() -> GameState:
    return GameState(
        session_id="session_explore_01",
        player=PlayerState(
            stats={"stat_hp": 20},
            attributes={"attr_will": 12},
            inventory={"item_weapon_01": 1},
            temporary_items={},
        ),
        current_location_id="location_start",
        world_config=WorldConfig(
            world_id="world_explore",
            theme="忍界边境黑夜求生",
            fanfic_meta=FanficMetaData(
                base_ip="Naruto",
                universe_type="Dark AU",
                tone_and_style="压抑、诡秘、危险",
            ),
            glossary=WorldGlossary(
                stats={"stat_hp": "生命值"},
                damage_types={"dmg_kinetic": "物理冲击"},
                item_categories={"item_weapon": "武器"},
            ),
            starting_location="木叶村外围营地",
            key_npcs=["叛忍侦察兵"],
            initial_quests=["活下去"],
            topology=WorldTopology(
                start_node_id="location_start",
                nodes={
                    "location_start": WorldNode(
                        node_id="location_start",
                        title="木叶村外围营地",
                        base_desc="一圈微弱火光勉强撑开了夜色。",
                        hidden_detail_dc10="火堆边缘有一串不属于你的脚印。",
                        deep_secret_dc18="营地下方埋着旧时代的封印遗迹。",
                        tags=["starting_area"],
                    ),
                    "location_watchtower": WorldNode(
                        node_id="location_watchtower",
                        title="废弃瞭望塔",
                        base_desc="高处的木塔已经腐朽，却仍能俯瞰整片林地。",
                        hidden_detail_dc10=None,
                        deep_secret_dc18=None,
                        tags=["elevated"],
                    ),
                    "location_sealed_gate": WorldNode(
                        node_id="location_sealed_gate",
                        title="封锁石门",
                        base_desc="石门表面布满斑驳封纹。",
                        hidden_detail_dc10=None,
                        deep_secret_dc18=None,
                        tags=["sealed"],
                    ),
                },
                edges={
                    "location_start": ["location_watchtower"],
                    "location_watchtower": ["location_start"],
                    "location_sealed_gate": [],
                },
            ),
        ),
    )


def test_resolve_exploration_travels_to_existing_connected_node() -> None:
    state = build_state()
    generator = DynamicMapGenerator(FakeStructuredJSONClient([]))

    logs, event = resolve_exploration(
        state,
        {"action_type": "travel"},
        map_generator=generator,
        target_node_id="location_watchtower",
        target_name="废弃瞭望塔",
    )
    next_state = apply_mutations(state, logs)

    assert event.is_success is True
    assert "travel_success" in event.result_tags
    assert next_state.current_location_id == "location_watchtower"


def test_resolve_exploration_discovers_and_stitches_new_location() -> None:
    state = build_state()
    generator = DynamicMapGenerator(
        FakeStructuredJSONClient(
            [
                json.dumps(
                    {
                        "node_id": "ignored_by_runtime",
                        "title": "后山的神秘山洞",
                        "base_desc": "山壁深处裂开一道黑黢黢的洞口，潮湿的冷气正从里面缓缓涌出。",
                        "hidden_detail_dc10": "石壁上残留着被人匆忙擦去的封印痕迹。",
                        "deep_secret_dc18": "更深处的祭坛仍在微弱运转，像在等待新的祭品。",
                        "tags": ["cave", "forbidden"],
                    },
                    ensure_ascii=False,
                )
            ]
        )
    )

    logs, event = resolve_exploration(
        state,
        {"action_type": "travel"},
        map_generator=generator,
        target_node_id="location_dyn_0001",
        target_name="后山的神秘山洞",
    )
    next_state = apply_mutations(state, logs)

    assert event.is_success is True
    assert "new_location_discovered" in event.result_tags
    assert next_state.current_location_id == "location_dyn_0001"
    assert next_state.world_config.topology.nodes["location_dyn_0001"].title == "后山的神秘山洞"
    assert "location_dyn_0001" in next_state.world_config.topology.edges["location_start"]
    assert "location_start" in next_state.world_config.topology.edges["location_dyn_0001"]


def test_resolve_exploration_blocks_unconnected_known_node() -> None:
    state = build_state()
    generator = DynamicMapGenerator(FakeStructuredJSONClient([]))

    logs, event = resolve_exploration(
        state,
        {"action_type": "travel"},
        map_generator=generator,
        target_node_id="location_sealed_gate",
        target_name="封锁石门",
    )

    assert logs == []
    assert event.is_success is False
    assert "path_not_connected" in event.result_tags


def test_resolve_exploration_falls_back_when_map_generator_gateway_fails() -> None:
    state = build_state()
    generator = DynamicMapGenerator(FailingStructuredJSONClient())

    logs, event = resolve_exploration(
        state,
        {"action_type": "travel"},
        map_generator=generator,
        target_node_id="location_dyn_0002",
        target_name="祭坛下的暗道",
    )
    next_state = apply_mutations(state, logs)

    assert event.is_success is True
    assert "new_location_discovered" in event.result_tags
    assert next_state.world_config.topology.nodes["location_dyn_0002"].title == "祭坛下的暗道"
