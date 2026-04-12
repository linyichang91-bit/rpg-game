"""Tests for FastAPI app entrypoints."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from server.api.app import (
    GameResetRequest,
    GameRestoreRequest,
    GameSaveRequest,
    GameStartRequest,
    RuntimeSessionSnapshot,
    SaveLootTarget,
    app,
    game_reset,
    game_restore,
    game_save,
    game_start,
)
from server.agent.gm import AgentTurnResult, AgentTurnStreamUpdate
from server.runtime.session_store import LootTarget, SessionStore
from server.schemas.core import FanficMetaData, WorldConfig, WorldGlossary


def build_world_config() -> WorldConfig:
    return WorldConfig(
        world_id="world_api",
        theme="urban_fantasy",
        fanfic_meta=FanficMetaData(
            base_ip="JJK",
            universe_type="同人",
            tone_and_style="紧张、凌厉、沉浸",
        ),
        world_book={
            "campaign_context": {
                "era_and_timeline": "东京现代咒术时代",
                "macro_world_state": "咒灵在城市阴影中活跃，术师组织维持着脆弱秩序。",
                "looming_crisis": "涩谷方向的异常波动正在抬升，任何拖延都可能酿成更大伤亡。",
                "opening_scene": "你在废弃站台边缘喘息，远处的咒灵正沿着铁轨逼近。",
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


def _parse_sse_body(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        event_name = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))

        if event_name and data_lines:
            events.append((event_name, json.loads("".join(data_lines))))

    return events


def test_game_start_reuses_prebuilt_prologue(monkeypatch) -> None:
    store = SessionStore()

    class UnexpectedGM:
        async def generate_opening(self, *, record, user_input: str) -> str:
            del record, user_input
            raise AssertionError("generate_opening should not run when prologue_text is provided")

    monkeypatch.setattr("server.api.app.get_session_store", lambda: store)
    monkeypatch.setattr("server.api.app.get_gm_engine", lambda: UnexpectedGM())

    prologue_text = "剧痛像潮水一样漫上来，你在陌生的和室里睁开眼。"
    response = asyncio.run(
        game_start(
            GameStartRequest(
                world_config=build_world_config(),
                world_prompt="咒术回战同人",
                prologue_text=prologue_text,
            )
        )
    )

    assert response.narration == prologue_text
    assert response.telemetry is not None
    assert response.telemetry.stages[1].stage_id == "opening_prologue"
    assert response.telemetry.stages[1].duration_ms == 0

    saved_record = store.get(response.session_id)
    assert saved_record is not None
    assert saved_record.recent_visible_text == prologue_text


def test_game_start_falls_back_to_gm_opening_when_prologue_missing(monkeypatch) -> None:
    store = SessionStore()

    class FakeGM:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_opening(self, *, record, user_input: str) -> str:
            del record, user_input
            self.calls += 1
            return "黑雾沿着铁轨翻涌而来，你已经没有退路。"

    gm = FakeGM()
    monkeypatch.setattr("server.api.app.get_session_store", lambda: store)
    monkeypatch.setattr("server.api.app.get_gm_engine", lambda: gm)

    response = asyncio.run(
        game_start(
            GameStartRequest(
                world_config=build_world_config(),
                world_prompt="咒术回战同人",
                prologue_text="   ",
            )
        )
    )

    assert response.narration == "黑雾沿着铁轨翻涌而来，你已经没有退路。"
    assert gm.calls == 1
    assert response.telemetry is not None
    assert response.telemetry.stages[1].stage_id == "opening_scene"


def test_game_action_stream_emits_sse_events_and_commits_state(monkeypatch) -> None:
    store = SessionStore()
    record = store.create_session(build_world_config(), world_prompt="咒术回战同人")

    class FakeStreamingGM:
        async def stream_turn(self, *, record, user_input: str):
            del user_input
            working_record = record.model_copy(deep=True) if hasattr(record, "model_copy") else record
            if working_record is record:
                from copy import deepcopy

                working_record = deepcopy(record)
            working_record.game_state.player.stats["stat_mp"] = 9
            narration = "风压沿着铁轨压来，你稳住脚下，咒力在指节间一点点凝结成形。"

            yield AgentTurnStreamUpdate(
                kind="status",
                phase="resolving_tools",
                message="Resolving tools and validating world-state changes.",
            )
            yield AgentTurnStreamUpdate(kind="narration_start")
            yield AgentTurnStreamUpdate(kind="narration_delta", delta="风压沿着铁轨压来，")
            yield AgentTurnStreamUpdate(kind="narration_delta", delta="你稳住脚下，咒力在指节间一点点凝结成形。")
            yield AgentTurnStreamUpdate(kind="narration_end", narration=narration)
            yield AgentTurnStreamUpdate(
                kind="result",
                result=AgentTurnResult(
                    narration=narration,
                    executed_events=[],
                    mutation_logs=[],
                ),
                source_record=working_record,
            )

    monkeypatch.setattr("server.api.app.get_session_store", lambda: store)
    monkeypatch.setattr("server.api.app.get_gm_engine", lambda: FakeStreamingGM())

    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/game/action/stream",
        json={
            "session_id": record.session_id,
            "user_input": "我稳住呼吸，展开防御。",
            "client_turn_id": "client_turn_01",
        },
    ) as response:
        body = b"".join(response.iter_raw()).decode("utf-8")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_body(body)
    event_names = [name for name, _payload in events]
    assert event_names == [
        "turn.accepted",
        "turn.status",
        "turn.status",
        "narration.start",
        "narration.delta",
        "narration.delta",
        "narration.end",
        "turn.status",
        "turn.completed",
    ]

    completed_payload = events[-1][1]
    assert completed_payload["type"] == "turn.completed"
    assert completed_payload["client_turn_id"] == "client_turn_01"
    assert completed_payload["narration"].startswith("风压沿着铁轨压来")

    saved_record = store.get(record.session_id)
    assert saved_record is not None
    assert saved_record.game_state.player.stats["stat_mp"] == 9
    assert saved_record.recent_visible_text == completed_payload["narration"]


def test_game_save_exports_runtime_snapshot(monkeypatch) -> None:
    store = SessionStore()
    record = store.create_session(build_world_config(), world_prompt="咒术回战同人")
    record.recent_visible_text = "你握紧了刀柄，呼吸短促而滚烫。"
    record.temp_item_counter = 2
    record.dynamic_location_counter = 1
    record.lootable_targets["corpse_enemy_01"] = LootTarget(
        target_id="corpse_enemy_01",
        display_name="巡逻咒灵的尸体",
        entity_type="corpse",
        summary="一具刚刚倒下的咒灵尸体。",
        source_enemy_id="enemy_01",
    )
    store.save(record)

    monkeypatch.setattr("server.api.app.get_session_store", lambda: store)

    response = game_save(GameSaveRequest(session_id=record.session_id))

    assert response.runtime_snapshot.recent_visible_text == record.recent_visible_text
    assert response.runtime_snapshot.temp_item_counter == 2
    assert response.runtime_snapshot.dynamic_location_counter == 1
    assert "corpse_enemy_01" in response.runtime_snapshot.lootable_targets


def test_game_restore_rehydrates_session(monkeypatch) -> None:
    store = SessionStore()
    original_record = store.create_session(build_world_config(), world_prompt="咒术回战同人")
    original_record.recent_visible_text = "楼梯下方的黑暗开始蠕动。"
    original_record.temp_item_counter = 3
    original_record.dynamic_location_counter = 2
    original_record.lootable_targets["corpse_enemy_01"] = LootTarget(
        target_id="corpse_enemy_01",
        display_name="巡逻咒灵的尸体",
        entity_type="corpse",
        summary="一具刚刚倒下的咒灵尸体。",
        source_enemy_id="enemy_01",
    )

    monkeypatch.setattr("server.api.app.get_session_store", lambda: store)

    response = game_restore(
        GameRestoreRequest(
            world_prompt="咒术回战同人",
            game_state=original_record.game_state,
            runtime_snapshot=RuntimeSessionSnapshot(
                recent_visible_text=original_record.recent_visible_text,
                nearby_npcs=original_record.nearby_npcs,
                encounter_names=original_record.encounter_names,
                lootable_targets={
                    "corpse_enemy_01": SaveLootTarget(
                        target_id="corpse_enemy_01",
                        display_name="巡逻咒灵的尸体",
                        entity_type="corpse",
                        summary="一具刚刚倒下的咒灵尸体。",
                        source_enemy_id="enemy_01",
                    )
                },
                temp_item_counter=3,
                dynamic_location_counter=2,
            ),
        )
    )

    assert response.session_id != original_record.session_id
    assert response.current_state.session_id == response.session_id

    restored_record = store.get(response.session_id)
    assert restored_record is not None
    assert restored_record.recent_visible_text == "楼梯下方的黑暗开始蠕动。"
    assert restored_record.temp_item_counter == 3
    assert restored_record.dynamic_location_counter == 2
    assert "corpse_enemy_01" in restored_record.lootable_targets


def test_game_reset_removes_session(monkeypatch) -> None:
    store = SessionStore()
    record = store.create_session(build_world_config())

    monkeypatch.setattr("server.api.app.get_session_store", lambda: store)

    response = game_reset(GameResetRequest(session_id=record.session_id))

    assert response.ok is True
    assert store.get(record.session_id) is None
