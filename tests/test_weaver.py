"""Tests for the fanfic world weaver."""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.initialization.weaver import (
    WorldConfigValidationError,
    WorldWeaver,
    _build_prologue_fallback,
    _looks_like_stock_prologue_opening,
    _normalize_player_attribute_values,
    build_prologue_prompt,
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

    def __init__(self, response_text: str | list[str]) -> None:
        if isinstance(response_text, list):
            self._responses = list(response_text)
        else:
            self._responses = [response_text]
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
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


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
    assert "6-18 for ordinary newcomers" in prompt_bundle.system_prompt
    assert "Do not downgrade legendary" in prompt_bundle.system_prompt
    assert "entry-tier ranks unless the prompt explicitly asks for a weakened start" in prompt_bundle.system_prompt
    assert "world_book.campaign_context.era_and_timeline" in prompt_bundle.user_prompt
    assert "world_book.campaign_context.main_quest" in prompt_bundle.user_prompt
    assert "world_book.power_scaling" in prompt_bundle.user_prompt
    assert "glossary.attributes" in prompt_bundle.user_prompt
    assert "player_character" in prompt_bundle.user_prompt


def test_prologue_prompt_contains_anti_cliche_constraints() -> None:
    world_config = WorldWeaver(
        FakeStructuredJSONClient([json.dumps(_valid_world_payload())])
    ).generate_world_config("Naruto AU")
    prompt_bundle = build_prologue_prompt(
        fanfic_prompt="Naruto AU",
        world_config=world_config,
    )

    assert "不要机械套用“痛。”“冷。”“黑。”“睁开眼”" in prompt_bundle.system_prompt
    assert "第一段必须明确落在给定的开场场景与初始地点" in prompt_bundle.system_prompt
    assert "第一段第一句就必须把玩家直接放进" in prompt_bundle.user_prompt
    assert "不得引入未出现在上述世界锚点中的其他 IP 人物" in prompt_bundle.user_prompt


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
    narrative_client = FakeNarrativeTextClient(
        (
            "Konoha Training Ground 的木桩还在震动，清晨的风从训练场边缘卷起砂土，也把昨夜未散的紧张气味一并吹了过来。"
            "神宫寺凛站在被苦无划出浅痕的木桩前，掌心仍残留着反震后的麻意。"
            "他没有急着收手，而是先看向树影，再看向空地边缘的铁网，像是在确认这片训练场到底还剩多少安全感。"
            "村子表面平静，可那层平静早就像纸一样薄。"
        )
        * 20
    )
    weaver = WorldWeaver(client, narrative_client=narrative_client)

    bundle = weaver.generate_world_bundle("Naruto AU")

    assert bundle.world_config.world_id == "naruto_konoha_early"
    assert len(bundle.prologue_text) >= 800
    assert len(narrative_client.calls) == 1


def test_weaver_retries_when_prologue_uses_stock_opening() -> None:
    client = FakeStructuredJSONClient([json.dumps(_valid_world_payload())])
    generic_draft = (
        "痛。\n\n"
        "不是那种尖锐的、撕裂般的痛，而是更深沉、更黏稠的东西，从骨头缝里一点点往上爬。"
        "空气里有铁锈味，后脑传来钝痛，意识像泡在冷水里，迟迟浮不上来。"
    ) * 30
    anchored_draft = (
        "Konoha Training Ground 的木桩还在震，清晨的风把砂土和汗味一起卷过场边。"
        "神宫寺凛抬手按住发麻的虎口，视线先扫过训练地边缘的铁网，再扫向卡卡西站着的树影。"
        "今天不是适合走神的一天，村子的紧张空气已经比苦无更早抵住了喉咙。"
    ) * 25
    narrative_client = FakeNarrativeTextClient([generic_draft, anchored_draft])
    weaver = WorldWeaver(client, narrative_client=narrative_client)

    bundle = weaver.generate_world_bundle("Naruto AU")

    assert bundle.prologue_text.startswith("Konoha Training Ground")
    assert len(narrative_client.calls) == 2
    assert "上一个版本不合格" in narrative_client.calls[1]["user_prompt"]


def test_looks_like_stock_prologue_opening_flags_cliche_openers() -> None:
    assert _looks_like_stock_prologue_opening("痛。\n\n空气像沥青一样压上来。")
    assert _looks_like_stock_prologue_opening("风里有铁锈的味道。\n\n他睁开眼睛。")
    assert not _looks_like_stock_prologue_opening("Konoha Training Ground 的木桩还在震。")


def test_build_prologue_fallback_starts_from_scene_instead_of_generic_pain() -> None:
    payload = _valid_world_payload()
    fallback = _build_prologue_fallback(WorldWeaver(FakeStructuredJSONClient([json.dumps(payload)])).generate_world_config("Naruto AU"))

    assert fallback.startswith("Konoha Training Ground")
    assert "Morning wind cuts across the training ground" in fallback
    assert "冷气像细针一样扎进你的喉咙" not in fallback


def test_weaver_keeps_high_tier_starting_attributes() -> None:
    payload = _valid_world_payload()
    payload["player_character"]["role"] = "澶嶆椿鍚庣殑椤剁骇鏈€寮烘湳甯?"
    payload["player_character"]["attributes"] = {
        "stat_power": 68,
        "stat_agility": 72,
        "stat_insight": 80,
        "stat_tenacity": 64,
        "stat_presence": 78,
    }

    client = FakeStructuredJSONClient([json.dumps(payload, ensure_ascii=False)])
    weaver = WorldWeaver(client)

    config = weaver.generate_world_config("Jujutsu Kaisen late-arc revival duel")

    assert config.player_character.attributes["stat_power"] == 68
    assert config.player_character.attributes["stat_insight"] == 80
    assert config.player_character.attributes["stat_presence"] == 78


def test_normalize_player_attribute_values_caps_only_extreme_outliers() -> None:
    normalized = _normalize_player_attribute_values(
        {
            "stat_power": 80,
            "stat_agility": 65,
            "stat_insight": 999,
            "stat_tenacity": -5,
            "stat_presence": 40,
        }
    )

    assert normalized["stat_power"] == 80
    assert normalized["stat_agility"] == 65
    assert normalized["stat_insight"] == 120
    assert normalized["stat_tenacity"] == 1
    assert normalized["stat_presence"] == 40
