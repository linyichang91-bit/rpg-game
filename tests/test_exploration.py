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
        self.calls: list[dict[str, object]] = []

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
            }
        )
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
        raise LLMGatewayError("LLM gateway request failed.")


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
            theme="edge_of_the_hidden_frontier",
            fanfic_meta=FanficMetaData(
                base_ip="Naruto",
                universe_type="Dark AU",
                tone_and_style="tense and secretive",
            ),
            world_book={
                "campaign_context": {
                    "era_and_timeline": "Leaf Year 60, third night of the border lockdown",
                    "macro_world_state": "The village is quietly pulling troops back while scouts distrust every stranger on the road.",
                    "looming_crisis": "If the team cannot find a new shelter before dawn, the hunters behind them will close in.",
                    "opening_scene": "A dying campfire flickers while quick footfalls cut through the trees beyond the clearing.",
                }
            },
            glossary=WorldGlossary(
                stats={"stat_hp": "Vitality"},
                damage_types={"dmg_kinetic": "Impact"},
                item_categories={"item_weapon": "Weapon"},
            ),
            starting_location="Outer Camp",
            key_npcs=["Scout Captain"],
            initial_quests=["Survive the night"],
            topology=WorldTopology(
                start_node_id="location_start",
                nodes={
                    "location_start": WorldNode(
                        node_id="location_start",
                        title="Outer Camp",
                        base_desc="A ring of weak firelight barely keeps the dark at bay.",
                        hidden_detail_dc10="Someone else's tracks circle the ashes.",
                        deep_secret_dc18="An old sealing chamber lies buried under the camp.",
                        tags=["starting_area"],
                    ),
                    "location_watchtower": WorldNode(
                        node_id="location_watchtower",
                        title="Watchtower",
                        base_desc="A rotting tower still overlooks the forest edge.",
                        hidden_detail_dc10=None,
                        deep_secret_dc18=None,
                        tags=["elevated"],
                    ),
                    "location_sealed_gate": WorldNode(
                        node_id="location_sealed_gate",
                        title="Sealed Gate",
                        base_desc="Ancient stone plates are covered in brittle seals.",
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
        target_name="Watchtower",
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
                        "title": "Hidden Ravine",
                        "base_desc": "The ravine opens beneath broken stone and wet roots.",
                        "hidden_detail_dc10": "A wiped seal mark remains on the wall.",
                        "deep_secret_dc18": "A dormant altar still hums under the mud.",
                        "tags": ["cave", "forbidden"],
                    }
                )
            ]
        )
    )

    logs, event = resolve_exploration(
        state,
        {"action_type": "travel"},
        map_generator=generator,
        target_node_id="location_dyn_0001",
        target_name="Hidden Ravine",
    )
    next_state = apply_mutations(state, logs)

    assert event.is_success is True
    assert "new_location_discovered" in event.result_tags
    assert next_state.current_location_id == "location_dyn_0001"
    assert next_state.world_config.topology.nodes["location_dyn_0001"].title == "Hidden Ravine"
    assert "location_dyn_0001" in next_state.world_config.topology.edges["location_start"]
    assert "location_start" in next_state.world_config.topology.edges["location_dyn_0001"]


def test_map_generator_retries_malformed_json_and_recovers() -> None:
    state = build_state()
    client = FakeStructuredJSONClient(
        [
            "{not valid json",
            json.dumps(
                {
                    "title": "Collapsed Tunnel",
                    "base_desc": "Cracked stone opens into a narrow tunnel with fresh scrape marks.",
                    "hidden_detail_dc10": "A snapped charm lies under the dust near the entrance.",
                    "deep_secret_dc18": "A sealed chamber deeper inside is still warm to the touch.",
                    "tags": ["tunnel", "hidden"],
                }
            ),
        ]
    )
    generator = DynamicMapGenerator(client)

    node = generator.generate_node(
        build_state(),
        current_node_id="location_start",
        target_node_id="location_dyn_retry",
        target_name="Collapsed Tunnel",
    )

    assert node.node_id == "location_dyn_retry"
    assert node.title == "Collapsed Tunnel"
    assert len(client.calls) == 2


def test_resolve_exploration_blocks_unconnected_known_node() -> None:
    state = build_state()
    generator = DynamicMapGenerator(FakeStructuredJSONClient([]))

    logs, event = resolve_exploration(
        state,
        {"action_type": "travel"},
        map_generator=generator,
        target_node_id="location_sealed_gate",
        target_name="Sealed Gate",
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
        target_name="Underpass Shrine",
    )
    next_state = apply_mutations(state, logs)

    assert event.is_success is True
    assert "new_location_discovered" in event.result_tags
    assert next_state.world_config.topology.nodes["location_dyn_0002"].title == "Underpass Shrine"


def test_map_generator_falls_back_after_three_invalid_attempts() -> None:
    state = build_state()
    client = FakeStructuredJSONClient(
        [
            "[]",
            "null",
            "{still bad json",
        ]
    )
    generator = DynamicMapGenerator(client)

    node = generator.generate_node(
        state,
        current_node_id="location_start",
        target_node_id="location_dyn_fallback",
        target_name="Fallback Tunnel",
    )

    assert node.node_id == "location_dyn_fallback"
    assert node.title == "Fallback Tunnel"
    assert node.base_desc
    assert len(client.calls) == 3
