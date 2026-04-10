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


def test_world_weaver_prompt_contains_constraints() -> None:
    prompt_bundle = build_world_weaver_prompt("Harry Potter AU")

    assert "strictly follow the JSON schema" in prompt_bundle.system_prompt
    assert "opening_scene must be vivid and immediate" in prompt_bundle.system_prompt
    assert "world_book.campaign_context.era_and_timeline" in prompt_bundle.user_prompt


def test_weaver_accepts_fenced_json_payloads() -> None:
    payload = json.dumps(_valid_world_payload(), ensure_ascii=False, indent=2)
    client = FakeStructuredJSONClient([f"```json\n{payload}\n```"])
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("Naruto AU")

    assert config.world_id == "naruto_konoha_early"


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


def test_weaver_surfaces_validation_path_in_error_message() -> None:
    payload = _valid_world_payload()
    del payload["world_book"]

    client = FakeStructuredJSONClient([json.dumps(payload)])
    weaver = WorldWeaver(client, max_validation_retries=0)

    with pytest.raises(WorldConfigValidationError) as exc_info:
        weaver.generate_world_config("Broken payload")

    assert "world_book" in str(exc_info.value)


def test_weaver_generates_world_bundle_with_long_prologue() -> None:
    client = FakeStructuredJSONClient([json.dumps(_valid_world_payload())])
    narrative_client = FakeNarrativeTextClient("Danger closes in with every breath. " * 40)
    weaver = WorldWeaver(client, narrative_client=narrative_client)

    bundle = weaver.generate_world_bundle("Naruto AU")

    assert bundle.world_config.world_id == "naruto_konoha_early"
    assert len(bundle.prologue_text) >= 800
    assert len(narrative_client.calls) == 1
