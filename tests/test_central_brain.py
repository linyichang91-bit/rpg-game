"""Tests for the Central Brain orchestration flow."""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.brain.central import CentralBrain, DecisionValidationError, build_prompt_bundle
from server.llm.openai_compatible import LLMGatewayError
from server.schemas.core import FanficMetaData, GameState, PlayerState, WorldConfig, WorldGlossary
from server.schemas.orchestrator import ContextEntity, OrchestratorDecision


class FakeStructuredJSONClient:
    """Test double that returns predetermined JSON payloads."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
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
        response_schema: dict[str, Any],
    ) -> str:
        del system_prompt, user_prompt, response_schema
        raise LLMGatewayError("LLM 网关请求失败。")


def build_state() -> GameState:
    return GameState(
        session_id="session_02",
        player=PlayerState(
            stats={"stat_hp": 24},
            attributes={"attr_focus": 6},
            inventory={"item_ranged_01": 1},
            temporary_items={},
        ),
        current_location_id="location_ruined_gate",
        active_encounter="encounter_boar",
        world_config=WorldConfig(
            world_id="world_beta",
            theme="post_apocalyptic_frontier",
            fanfic_meta=FanficMetaData(
                base_ip="Original Frontier",
                universe_type="Original",
                tone_and_style="dusty and tense",
            ),
            glossary=WorldGlossary(
                stats={"stat_hp": "Hull"},
                damage_types={"dmg_kinetic": "Impact"},
                item_categories={"item_weapon": "Weapons"},
            ),
            starting_location="location_ruined_gate",
            key_npcs=["npc_gate_keeper_01"],
            initial_quests=["quest_hunt_the_beast"],
        ),
    )


def test_central_brain_routes_valid_combat_intent() -> None:
    client = FakeStructuredJSONClient(
        [
            json.dumps(
                {
                    "pipeline_type": "combat",
                    "confidence": 0.93,
                    "parameters": {
                        "action_type": "attack",
                        "target_id": "enemy_boar_01",
                        "weapon_id": "item_ranged_01",
                    },
                    "clarification_needed": None,
                }
            )
        ]
    )
    brain = CentralBrain(client)

    outcome = brain.decide(
        player_input="I shoot the boar.",
        game_state=build_state(),
        location_summary="A shattered checkpoint outside the settlement walls.",
        active_quest_ids=["quest_hunt_the_beast"],
        nearby_entities=[
            ContextEntity(
                entity_id="enemy_boar_01",
                display_name="mutant boar",
                entity_type="enemy",
                summary="A hostile tusked beast pawing at the rubble.",
            )
        ],
    )

    assert outcome.should_execute is True
    assert outcome.failure_reason is None
    assert outcome.decision == OrchestratorDecision(
        pipeline_type="combat",
        confidence=0.93,
        parameters={
            "action_type": "attack",
            "target_id": "enemy_boar_01",
            "weapon_id": "item_ranged_01",
        },
        clarification_needed=None,
    )
    assert "Available Pipelines & Parameter Specs" in client.calls[0]["user_prompt"]
    assert client.calls[0]["response_schema"]["title"] == "OrchestratorDecision"


def test_central_brain_pauses_when_clarification_is_needed() -> None:
    client = FakeStructuredJSONClient(
        [
            json.dumps(
                {
                    "pipeline_type": "combat",
                    "confidence": 0.41,
                    "parameters": {
                        "action_type": "attack",
                        "raw_target_text": "him",
                    },
                    "clarification_needed": "你想攻击哪一个目标？",
                }
            )
        ]
    )
    brain = CentralBrain(client, confidence_threshold=0.6)

    outcome = brain.decide(
        player_input="I attack him.",
        game_state=build_state(),
    )

    assert outcome.should_execute is False
    assert outcome.failure_reason == "clarification_needed"
    assert outcome.clarification_message == "你想攻击哪一个目标？"


def test_central_brain_retries_after_invalid_json_and_then_succeeds() -> None:
    client = FakeStructuredJSONClient(
        [
            '{"pipeline_type":"combat"}',
            json.dumps(
                {
                    "pipeline_type": "utility",
                    "confidence": 0.98,
                    "parameters": {"query_type": "inventory"},
                    "clarification_needed": None,
                }
            ),
        ]
    )
    brain = CentralBrain(client, max_validation_retries=1)

    outcome = brain.decide(
        player_input="Show me my inventory.",
        game_state=build_state(),
    )

    assert outcome.should_execute is True
    assert outcome.decision.pipeline_type == "utility"
    assert len(client.calls) == 2


def test_central_brain_raises_when_all_validation_attempts_fail() -> None:
    client = FakeStructuredJSONClient(
        [
            '{"pipeline_type":"combat"}',
            '{"pipeline_type":"combat"}',
        ]
    )
    brain = CentralBrain(client, max_validation_retries=1)

    with pytest.raises(DecisionValidationError):
        brain.decide(
            player_input="Open fire.",
            game_state=build_state(),
        )


def test_prompt_bundle_embeds_context_and_examples() -> None:
    prompt_bundle = build_prompt_bundle(
        player_input="Tell me what happened here.",
        context={
            "session_id": "session_02",
            "world_id": "world_beta",
            "world_theme": "post_apocalyptic_frontier",
            "current_location_id": "location_ruined_gate",
            "location_summary": "A broken checkpoint.",
            "active_encounter": None,
            "active_quest_ids": [],
            "nearby_entities": [],
        },
    )

    assert "handle only the first or primary intent" in prompt_bundle.system_prompt
    assert "Simplified Chinese" in prompt_bundle.system_prompt
    assert "Player Input:" in prompt_bundle.user_prompt
    assert '"world_id": "world_beta"' in prompt_bundle.user_prompt


def test_central_brain_accepts_fenced_json_payloads() -> None:
    client = FakeStructuredJSONClient(
        [
            """```json
            {
              "pipeline_type": "utility",
              "confidence": 0.91,
              "parameters": {
                "query_type": "inventory"
              },
              "clarification_needed": null
            }
            ```"""
        ]
    )
    brain = CentralBrain(client)

    outcome = brain.decide(
        player_input="Show me my inventory.",
        game_state=build_state(),
    )

    assert outcome.should_execute is True
    assert outcome.decision.pipeline_type == "utility"


def test_central_brain_falls_back_to_heuristic_combat_for_common_chinese_input() -> None:
    client = FakeStructuredJSONClient(['{"pipeline_type":"combat"}'])
    brain = CentralBrain(client, max_validation_retries=0)

    outcome = brain.decide(
        player_input="我拔出武器攻击敌人",
        game_state=build_state(),
        nearby_entities=[
            ContextEntity(
                entity_id="enemy_boar_01",
                display_name="变异野猪",
                entity_type="enemy",
                summary="一头正要扑上来的野猪。",
            )
        ],
    )

    assert outcome.should_execute is True
    assert outcome.decision.pipeline_type == "combat"
    assert outcome.decision.parameters["action_type"] == "attack"
    assert outcome.decision.parameters["target_id"] == "enemy_boar_01"
    assert outcome.decision.parameters["weapon_key"] == "item_ranged_01"


def test_central_brain_falls_back_to_heuristic_utility_for_chinese_status_query() -> None:
    client = FakeStructuredJSONClient(['{"pipeline_type":"utility"}'])
    brain = CentralBrain(client, max_validation_retries=0)

    outcome = brain.decide(
        player_input="查看我的状态",
        game_state=build_state(),
    )

    assert outcome.should_execute is True
    assert outcome.decision.pipeline_type == "utility"
    assert outcome.decision.parameters["query_type"] == "status"


def test_central_brain_falls_back_to_heuristic_loot_for_chinese_search_input() -> None:
    client = FakeStructuredJSONClient(['{"pipeline_type":"loot"}'])
    brain = CentralBrain(client, max_validation_retries=0)

    outcome = brain.decide(
        player_input="我仔细搜查倒下的敌人尸体",
        game_state=build_state(),
        nearby_entities=[
            ContextEntity(
                entity_id="corpse_enemy_01",
                display_name="变异野猪的尸体",
                entity_type="corpse",
                summary="A fresh corpse on the mud.",
            )
        ],
    )

    assert outcome.should_execute is True
    assert outcome.decision.pipeline_type == "loot"
    assert outcome.decision.parameters["action_type"] == "loot"
    assert outcome.decision.parameters["target_id"] == "corpse_enemy_01"


def test_central_brain_uses_heuristics_when_gateway_request_fails() -> None:
    brain = CentralBrain(FailingStructuredJSONClient(), max_validation_retries=0)

    outcome = brain.decide(
        player_input="我拔出武器攻击敌人",
        game_state=build_state(),
        nearby_entities=[
            ContextEntity(
                entity_id="enemy_boar_01",
                display_name="变异野猪",
                entity_type="enemy",
                summary="一头正要扑上来的野猪。",
            )
        ],
    )

    assert outcome.should_execute is True
    assert outcome.decision.pipeline_type == "combat"
    assert outcome.decision.parameters["target_id"] == "enemy_boar_01"


def test_central_brain_falls_back_to_heuristic_exploration_for_chinese_travel_input() -> None:
    client = FakeStructuredJSONClient(['{"pipeline_type":"exploration"}'])
    brain = CentralBrain(client, max_validation_retries=0)

    outcome = brain.decide(
        player_input="去后山的神秘山洞",
        game_state=build_state(),
    )

    assert outcome.should_execute is True
    assert outcome.decision.pipeline_type == "exploration"
    assert outcome.decision.parameters["action_type"] == "travel"
    assert outcome.decision.parameters["raw_target_text"] == "后山的神秘山洞"
