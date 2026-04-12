"""Tests for the fanfic world weaver."""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.initialization.weaver import (
    WorldConfigValidationError,
    WorldWeaver,
    build_world_weaver_prompt,
)


class FakeStructuredJSONClient:
    """Test double for JSON-producing LLM calls."""

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


class FakeNarrativeTextClient:
    """Test double for prologue text generation calls."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, str]] = []

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.85,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": str(temperature),
            }
        )
        return self.response_text


def _valid_world_payload() -> dict[str, Any]:
    return {
        "world_id": "naruto_konoha_early",
        "theme": "ninja_growth_drama",
        "fanfic_meta": {
            "base_ip": "Naruto",
            "universe_type": "AU",
            "tone_and_style": "tense and cinematic",
        },
        "player_character": {
            "name": "神宫寺凛",
            "role": "感知型见习术师",
            "summary": "擅长感知异常与战场判断，但近身正面对抗经验不足。",
            "objective": "赶在局势失控前找到边境异动的真正源头。",
            "attributes": {
                "stat_power": 9,
                "stat_agility": 13,
                "stat_insight": 15,
                "stat_tenacity": 11,
                "stat_presence": 10,
            },
        },
        "world_book": {
            "campaign_context": {
                "era_and_timeline": "Konoha Team 7 early mission period",
                "macro_world_state": "Village leadership keeps a fragile balance while intelligence conflicts intensify.",
                "looming_crisis": "An unknown squad approaches the perimeter and may strike at dusk.",
                "opening_scene": "Morning wind cuts across the training ground while wooden stakes still vibrate from fresh impacts.",
            }
        },
        "glossary": {
            "stats": {"stat_hp": "Vitality", "stat_mp": "Chakra"},
            "damage_types": {"dmg_kinetic": "Impact", "dmg_energy": "Ninjutsu"},
            "item_categories": {"item_weapon": "Ninja Tools"},
        },
        "starting_location": "Konoha Training Ground",
        "key_npcs": ["Kakashi Hatake", "Naruto Uzumaki"],
        "initial_quests": ["Pass the opening combat drill"],
        "mechanics": {},
    }


def test_weaver_generates_world_config() -> None:
    client = FakeStructuredJSONClient([json.dumps(_valid_world_payload())])
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("Naruto AU in Konoha training ground.")

    assert config.world_id == "naruto_konoha_early"
    assert config.starting_location == "Konoha Training Ground"
    assert config.glossary.stats["stat_hp"] == "Vitality"
    assert config.player_character.attributes["stat_insight"] == 15


def test_world_weaver_prompt_contains_constraints() -> None:
    prompt_bundle = build_world_weaver_prompt("Harry Potter AU")

    assert "strictly follow the JSON schema" in prompt_bundle.system_prompt
    assert "opening_scene must be vivid and immediate" in prompt_bundle.system_prompt
    assert "world_book.campaign_context.era_and_timeline" in prompt_bundle.user_prompt
    assert "world_book.campaign_context.main_quest" in prompt_bundle.user_prompt
    assert "world_book.power_scaling" in prompt_bundle.user_prompt
    assert "glossary.attributes" in prompt_bundle.user_prompt
    assert "player_character" in prompt_bundle.user_prompt


def test_weaver_accepts_fenced_json_payloads() -> None:
    payload = json.dumps(_valid_world_payload(), ensure_ascii=False, indent=2)
    client = FakeStructuredJSONClient([f"```json\n{payload}\n```"])
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("Naruto AU")

    assert config.world_id == "naruto_konoha_early"


def test_weaver_retries_malformed_json_and_recovers() -> None:
    client = FakeStructuredJSONClient(
        [
            "{not valid json",
            json.dumps(_valid_world_payload()),
        ]
    )
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("Naruto AU")

    assert config.world_id == "naruto_konoha_early"
    assert len(client.calls) == 2


def test_weaver_prunes_unknown_fields_before_validation() -> None:
    payload = _valid_world_payload()
    payload["unused_top_level"] = "ignore me"
    payload["fanfic_meta"]["unused"] = "ignore"
    payload["world_book"]["unused"] = {"x": 1}
    payload["world_book"]["campaign_context"]["unused"] = "ignore"

    client = FakeStructuredJSONClient([json.dumps(payload)])
    weaver = WorldWeaver(client)
    config = weaver.generate_world_config("Naruto AU")

    assert config.world_id == "naruto_konoha_early"
    assert config.fanfic_meta.base_ip == "Naruto"


def test_weaver_normalizes_nested_location_npc_and_quest_shapes() -> None:
    payload = _valid_world_payload()
    payload["starting_location"] = {"location_name": "Hidden Cellar"}
    payload["key_npcs"] = [
        {"npc_id": "npc_01", "npc_name": "Shikamaru"},
        {"npc_id": "npc_02", "npc_name": "Sakura"},
    ]
    payload["initial_quests"] = [
        {"quest_id": "q1", "quest_name": "Stabilize the perimeter"},
        {"quest_id": "q2", "objective": "Find the infiltrator"},
    ]

    client = FakeStructuredJSONClient([json.dumps(payload)])
    weaver = WorldWeaver(client)
    config = weaver.generate_world_config("Naruto AU")

    assert config.starting_location == "Hidden Cellar"
    assert config.key_npcs == ["Shikamaru", "Sakura"]
    assert config.initial_quests == ["Stabilize the perimeter", "q2"]


def test_weaver_fills_storyline_and_power_defaults_when_missing() -> None:
    payload = _valid_world_payload()
    payload["world_book"]["campaign_context"].pop("main_quest", None)
    payload["world_book"]["campaign_context"].pop("current_chapter", None)
    payload["world_book"]["campaign_context"].pop("milestones", None)
    payload["world_book"].pop("power_scaling", None)
    payload["glossary"].pop("attributes", None)
    payload.pop("player_character", None)

    client = FakeStructuredJSONClient([json.dumps(payload)])
    weaver = WorldWeaver(client)
    config = weaver.generate_world_config("Naruto AU")

    campaign_context = config.world_book.campaign_context
    assert campaign_context.main_quest.title == "主线目标"
    assert campaign_context.current_chapter.title == "第一章"
    assert len(campaign_context.milestones) >= 1
    assert config.world_book.power_scaling.impossible_gap_threshold == 40
    assert config.glossary.attributes["stat_power"] == "力量"
    assert config.player_character.name == "未命名旅者"
    assert config.player_character.attributes["stat_agility"] == 12


def test_weaver_coerces_string_storyline_shapes_into_structured_models() -> None:
    payload = _valid_world_payload()
    payload["world_book"]["campaign_context"]["main_quest"] = "阻止边境崩盘"
    payload["world_book"]["campaign_context"]["current_chapter"] = "先稳住训练场局势"
    payload["world_book"]["campaign_context"]["milestones"] = [
        "发现第一条线索",
        {"summary": "确认入侵者接近的方向"},
    ]

    client = FakeStructuredJSONClient([json.dumps(payload, ensure_ascii=False)])
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("Naruto AU")
    campaign_context = config.world_book.campaign_context

    assert campaign_context.main_quest.title == "阻止边境崩盘"
    assert campaign_context.current_chapter.objective == "先稳住训练场局势"
    assert len(campaign_context.milestones) == 2
    assert campaign_context.milestones[0].title == "发现第一条线索"
    assert campaign_context.milestones[1].milestone_id == "milestone_02"


def test_weaver_surfaces_validation_path_in_error_message() -> None:
    payload = _valid_world_payload()
    del payload["fanfic_meta"]

    client = FakeStructuredJSONClient([json.dumps(payload)])
    weaver = WorldWeaver(client, max_validation_retries=0)

    with pytest.raises(WorldConfigValidationError) as exc_info:
        weaver.generate_world_config("Broken payload")

    assert "fanfic_meta" in str(exc_info.value)


def test_weaver_stops_after_three_total_attempts_on_repeated_validation_failures() -> None:
    payload = _valid_world_payload()
    del payload["fanfic_meta"]

    client = FakeStructuredJSONClient(
        [
            json.dumps(payload),
            json.dumps(payload),
            json.dumps(payload),
        ]
    )
    weaver = WorldWeaver(client)

    with pytest.raises(WorldConfigValidationError):
        weaver.generate_world_config("Broken payload")

    assert len(client.calls) == 3


def test_weaver_generates_world_bundle_with_long_prologue() -> None:
    client = FakeStructuredJSONClient([json.dumps(_valid_world_payload())])
    narrative_client = FakeNarrativeTextClient("Danger closes in with every breath. " * 40)
    weaver = WorldWeaver(client, narrative_client=narrative_client)

    bundle = weaver.generate_world_bundle("Naruto AU")

    assert bundle.world_config.world_id == "naruto_konoha_early"
    assert len(bundle.prologue_text) >= 800
    assert len(narrative_client.calls) == 1
