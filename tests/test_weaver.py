"""Tests for the fanfic world weaver."""

from __future__ import annotations

import json
from typing import Any

from server.initialization.weaver import WorldWeaver, build_world_weaver_prompt


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


def test_weaver_generates_modern_au_config() -> None:
    client = FakeStructuredJSONClient(
        [
            json.dumps(
                {
                    "world_id": "lotr_modern_au",
                    "theme": "modern urban fantasy noir",
                    "fanfic_meta": {
                        "base_ip": "Lord of the Rings",
                        "universe_type": "Modern AU",
                        "tone_and_style": "gritty, street-level, tense",
                    },
                    "glossary": {
                        "stats": {
                            "stat_hp": "street stamina",
                            "stat_mp": "nerve",
                        },
                        "damage_types": {
                            "dmg_kinetic": "blunt force",
                            "dmg_energy": "electrical burn",
                        },
                        "item_categories": {
                            "item_weapon": "street weapons",
                        },
                    },
                    "starting_location": "Night dispatch lot in Queens",
                    "key_npcs": ["Strider the dispatcher", "Orc gang runner"],
                    "initial_quests": ["Survive the gang pressure on tonight's route"],
                    "mechanics": {
                        "transport_mode": "for-hire car service",
                    },
                }
            )
        ]
    )
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config(
        "指环王现代AU，主角在纽约开黑车，武器是方向盘锁定杆，半兽人是街头帮派"
    )

    assert config.fanfic_meta.base_ip == "Lord of the Rings"
    assert "AU" in config.fanfic_meta.universe_type or "modern" in config.fanfic_meta.universe_type.lower()
    assert config.glossary.item_categories["item_weapon"] in {"street weapons", "self-defense blunt tools"}


def test_weaver_generates_canon_lord_of_mysteries_terms() -> None:
    client = FakeStructuredJSONClient(
        [
            json.dumps(
                {
                    "world_id": "lotm_seer_path",
                    "theme": "occult mystery and creeping dread",
                    "fanfic_meta": {
                        "base_ip": "Lord of the Mysteries",
                        "universe_type": "Canon",
                        "tone_and_style": "somber, esoteric, suspenseful",
                    },
                    "glossary": {
                        "stats": {
                            "stat_hp": "Health",
                            "stat_mp": "灵性",
                        },
                        "damage_types": {
                            "dmg_kinetic": "ballistic trauma",
                            "dmg_energy": "spiritual backlash",
                        },
                        "item_categories": {
                            "item_weapon": "Beyonder weapons",
                        },
                    },
                    "starting_location": "A dim rented room in Tingen",
                    "key_npcs": ["Dunn Smith", "Leonard Mitchell"],
                    "initial_quests": ["Stabilize as a Sequence 9 Seer"],
                    "mechanics": {
                        "pathway": "seer",
                    },
                }
            )
        ]
    )
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("原汁原味的诡秘之主，主角是占卜家途径序列 9")

    assert config.glossary.stats["stat_mp"] in {"灵性", "Spirituality"}


def test_world_weaver_prompt_contains_json_only_constraints() -> None:
    prompt_bundle = build_world_weaver_prompt("哈利波特AU，伏地魔获胜")

    assert "strictly follow the JSON schema" in prompt_bundle.system_prompt
    assert "Do not write story prose" in prompt_bundle.system_prompt
    assert "Simplified Chinese" in prompt_bundle.system_prompt
    assert "stat_hp, stat_mp" in prompt_bundle.user_prompt


def test_weaver_accepts_fenced_json_payloads() -> None:
    client = FakeStructuredJSONClient(
        [
            """```json
            {
              "world_id": "fenced_world",
              "theme": "arcane noir",
              "fanfic_meta": {
                "base_ip": "Harry Potter",
                "universe_type": "AU",
                "tone_and_style": "grim and suspicious"
              },
              "glossary": {
                "stats": {
                  "stat_hp": "Resolve",
                  "stat_mp": "Magic"
                },
                "damage_types": {
                  "dmg_kinetic": "Impact",
                  "dmg_energy": "Hexfire"
                },
                "item_categories": {
                  "item_weapon": "Implements"
                }
              },
              "starting_location": "Knockturn Alley",
              "key_npcs": ["Snape", "Death Eater patrol"],
              "initial_quests": ["Stay unnoticed"],
              "mechanics": {}
            }
            ```"""
        ]
    )
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("Voldemort wins AU.")

    assert config.world_id == "fenced_world"
    assert config.starting_location == "Knockturn Alley"


def test_weaver_normalizes_nested_location_npc_and_quest_shapes() -> None:
    client = FakeStructuredJSONClient(
        [
            json.dumps(
                {
                    "world_id": "hp_dark_victory",
                    "theme": "survival under totalitarian magical regime",
                    "fanfic_meta": {
                        "base_ip": "Harry Potter",
                        "universe_type": "AU",
                        "tone_and_style": "dark dystopian horror",
                    },
                    "glossary": {
                        "stats": {
                            "stat_hp": "vitality",
                            "stat_mp": "magical reserve",
                        },
                        "damage_types": {
                            "dmg_kinetic": "physical trauma",
                            "dmg_energy": "spell impact",
                        },
                        "item_categories": {
                            "item_weapon": "wand type",
                        },
                    },
                    "starting_location": {
                        "location_id": "knockturn_alley_hovel",
                        "location_name": "Derelict Leased Cellar",
                    },
                    "key_npcs": [
                        {
                            "npc_id": "snape_master",
                            "npc_name": "Severus Snape",
                        },
                        {
                            "npc_id": "street_informer",
                            "npc_name": "Refugee Witch",
                        },
                    ],
                    "initial_quests": [
                        {
                            "quest_id": "quest_shadow_survival",
                            "quest_name": "Maintain Operational Security",
                        },
                        {
                            "quest_id": "quest_dark_training",
                            "quest_name": "Advance Dark Arts Studies",
                        },
                    ],
                    "mechanics": {},
                }
            )
        ]
    )
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("Voldemort wins AU.")

    assert config.starting_location == "Derelict Leased Cellar"
    assert config.key_npcs == ["Severus Snape", "Refugee Witch"]
    assert config.initial_quests == [
        "Maintain Operational Security",
        "Advance Dark Arts Studies",
    ]
