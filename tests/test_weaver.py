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
                    "world_book": {
                        "campaign_context": {
                            "era_and_timeline": "现代AU，纽约雨夜的深秋",
                            "macro_world_state": "中土残响以帮派、地下情报网和古老血脉的方式潜伏在现代都市里。",
                            "looming_crisis": "今晚的送车路线已经被半兽人帮派盯上，再拖下去就会被彻底围死。",
                            "opening_scene": "你刚把车停进皇后区的夜班调度场，车窗外就有人用铁棍敲响玻璃，远处警灯正在雨里闪烁。",
                        }
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
    assert "纽约" in config.world_book.campaign_context.era_and_timeline
    assert "皇后区" in config.world_book.campaign_context.opening_scene


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
                    "world_book": {
                        "campaign_context": {
                            "era_and_timeline": "廷根纪元，克莱恩刚成为序列9后的早期阶段",
                            "macro_world_state": "黑夜教会与官方非凡势力仍在维持表面秩序，隐秘教派在阴影中活动，远未到后期大乱局。",
                            "looming_crisis": "你刚踏入非凡世界，任何一次失控或暴露都可能让你直接坠入深渊。",
                            "opening_scene": "昏黄煤气灯下，你在廷根出租屋里按住发胀的太阳穴，门外忽然响起三下克制而急促的敲门声。",
                        }
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
    assert "opening_scene must be vivid and immediate" in prompt_bundle.system_prompt
    assert "Never introduce future characters early" in prompt_bundle.system_prompt
    assert "Simplified Chinese" in prompt_bundle.system_prompt
    assert "stat_hp, stat_mp" in prompt_bundle.user_prompt
    assert "world_book.campaign_context.era_and_timeline" in prompt_bundle.user_prompt


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
              "world_book": {
                "campaign_context": {
                  "era_and_timeline": "伏地魔胜利后的第一年冬天",
                  "macro_world_state": "魔法部已经被食死徒渗透，街头搜捕频繁，但霍格沃茨仍在高压统治下运作。",
                  "looming_crisis": "今晚的宵禁搜查一旦落到你头上，身份立刻会暴露。",
                  "opening_scene": "你缩在翻倒的黑巷壁炉边喘气，巷口忽然扫过一道搜查咒的白光。"
                }
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


def test_weaver_prunes_unknown_nested_fields_before_validation() -> None:
    client = FakeStructuredJSONClient(
        [
            json.dumps(
                {
                    "world_id": "founders_era_world",
                    "theme": "hogwarts_founders_mystery",
                    "fanfic_meta": {
                        "base_ip": "Harry Potter",
                        "universe_type": "Canon",
                        "tone_and_style": "mysterious and tense",
                        "note": "this extra field should be ignored",
                    },
                    "world_book": {
                        "campaign_context": {
                            "era_and_timeline": "霍格沃茨创始人时代，城堡初建期",
                            "macro_world_state": "四位创始人仍在亲自督建城堡，尚无魔法部，也没有伏地魔。",
                            "looming_crisis": "禁林边缘的古老异动正在逼近工地，学徒们必须在夜幕前撤离。",
                            "opening_scene": "你抱着石料清单穿过未封顶的长廊，头顶忽然落下大片碎石，远处传来某种巨兽的低吼。",
                            "forbidden_extra": "ignore me",
                        },
                        "unused_section": {
                            "anything": "goes"
                        },
                    },
                    "glossary": {
                        "stats": {
                            "stat_hp": "生命值",
                            "stat_mp": "魔力",
                        },
                        "damage_types": {
                            "dmg_kinetic": "物理伤害",
                            "dmg_energy": "魔力冲击",
                        },
                        "item_categories": {
                            "item_weapon": "武器",
                        },
                    },
                    "starting_location": "霍格沃茨工地长廊",
                    "key_npcs": ["戈德里克·格兰芬多"],
                    "initial_quests": ["在夜幕前撤离工地"],
                    "mechanics": {},
                    "unused_top_level": True,
                }
            )
        ]
    )
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("霍格沃茨创始人时代。")

    assert config.world_id == "founders_era_world"
    assert config.world_book.campaign_context.era_and_timeline == "霍格沃茨创始人时代，城堡初建期"


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
                    "world_book": {
                        "campaign_context": {
                            "era_and_timeline": "伏地魔胜利后的统治时期",
                            "macro_world_state": "食死徒控制着主要机构，幸存者只能在黑巷和地下据点里苟活。",
                            "looming_crisis": "任何一次不谨慎的移动都可能把追兵引到藏身处。",
                            "opening_scene": "你在漏水的地窖墙边屏住呼吸，楼上木板忽然传来巡逻靴跟碾过的声音。",
                        }
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


def test_weaver_surfaces_validation_path_in_error_message() -> None:
    client = FakeStructuredJSONClient(
        [
            json.dumps(
                {
                    "world_id": "broken_world",
                    "theme": "broken_theme",
                    "fanfic_meta": {
                        "base_ip": "Harry Potter",
                        "universe_type": "Canon",
                        "tone_and_style": "grim",
                    },
                    "glossary": {
                        "stats": {
                            "stat_hp": "生命值",
                        },
                        "damage_types": {
                            "dmg_kinetic": "物理伤害",
                        },
                        "item_categories": {
                            "item_weapon": "武器",
                        },
                    },
                    "starting_location": "霍格沃茨",
                    "key_npcs": [],
                    "initial_quests": [],
                    "mechanics": {},
                }
            )
        ]
    )
    weaver = WorldWeaver(client, max_validation_retries=0)

    with pytest.raises(WorldConfigValidationError) as exc_info:
        weaver.generate_world_config("霍格沃茨创始人时代。")

    assert "world_book" in str(exc_info.value)
