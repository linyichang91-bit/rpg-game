"""FastAPI application wiring the frontend to the runtime engine."""

from __future__ import annotations

from functools import lru_cache
from time import perf_counter
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from server.agent.gm import build_gm_agent_from_env
from server.generators.loot_generator import LootGenerator, build_loot_generator_from_env
from server.initialization.weaver import WorldWeaverError, generate_world_bundle
from server.llm.openai_compatible import LLMGatewayError
from server.pipelines.loot import resolve_loot
from server.runtime.session_store import LootTarget, SessionRecord, SessionStore
from server.schemas.core import (
    ContextEntity,
    ExecutedEvent,
    GameState,
    MutationLog,
    WorldConfig,
)


class WorldGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)


class TimingStage(BaseModel):
    stage_id: str
    label: str
    duration_ms: int


class RequestTelemetry(BaseModel):
    total_ms: int
    stages: list[TimingStage] = Field(default_factory=list)


class WorldGenerateResponse(BaseModel):
    world_config: WorldConfig
    prologue_text: str | None = None
    telemetry: RequestTelemetry | None = None


class GameStartRequest(BaseModel):
    world_config: WorldConfig
    world_prompt: str | None = None
    prologue_text: str | None = None


class GameActionRequest(BaseModel):
    session_id: str
    user_input: str = Field(..., min_length=1)


class SaveLootTarget(BaseModel):
    target_id: str
    display_name: str
    entity_type: str
    summary: str
    source_enemy_id: str | None = None


class RuntimeSessionSnapshot(BaseModel):
    recent_visible_text: str | None = None
    nearby_npcs: list[ContextEntity] = Field(default_factory=list)
    encounter_names: dict[str, str] = Field(default_factory=dict)
    lootable_targets: dict[str, SaveLootTarget] = Field(default_factory=dict)
    temp_item_counter: int = 0
    dynamic_location_counter: int = 0


class GameTurnResponse(BaseModel):
    session_id: str
    current_state: GameState
    narration: str
    executed_events: list[ExecutedEvent] = Field(default_factory=list)
    mutation_logs: list[MutationLog] = Field(default_factory=list)
    telemetry: RequestTelemetry | None = None


class GameSaveRequest(BaseModel):
    session_id: str


class GameSaveResponse(BaseModel):
    runtime_snapshot: RuntimeSessionSnapshot


class GameRestoreRequest(BaseModel):
    world_prompt: str | None = None
    game_state: GameState
    runtime_snapshot: RuntimeSessionSnapshot


class GameRestoreResponse(BaseModel):
    session_id: str
    current_state: GameState


class GameResetRequest(BaseModel):
    session_id: str


class GameResetResponse(BaseModel):
    ok: bool


app = FastAPI(title="Fanfic Sandbox API")


@lru_cache(maxsize=1)
def get_session_store() -> SessionStore:
    return SessionStore()


@lru_cache(maxsize=1)
def get_gm_engine():
    return build_gm_agent_from_env()


@lru_cache(maxsize=1)
def get_loot_generator() -> LootGenerator:
    return build_loot_generator_from_env()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/world/generate", response_model=WorldGenerateResponse)
def world_generate(request: WorldGenerateRequest) -> WorldGenerateResponse:
    started_at = perf_counter()
    try:
        weave_result = generate_world_bundle(request.prompt)
    except (WorldWeaverError, LLMGatewayError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    duration_ms = round((perf_counter() - started_at) * 1000)
    return WorldGenerateResponse(
        world_config=weave_result.world_config,
        prologue_text=weave_result.prologue_text,
        telemetry=RequestTelemetry(
            total_ms=duration_ms,
            stages=[
                TimingStage(
                    stage_id="world_weaver",
                    label="World Weaver",
                    duration_ms=duration_ms,
                )
            ],
        ),
    )


@app.post("/api/game/start", response_model=GameTurnResponse)
async def game_start(request: GameStartRequest) -> GameTurnResponse:
    started_at = perf_counter()
    session_store = get_session_store()

    session_prepare_started_at = perf_counter()
    record = session_store.create_session(
        request.world_config,
        world_prompt=request.world_prompt,
    )
    session_prepare_ms = round((perf_counter() - session_prepare_started_at) * 1000)
    opening_event = ExecutedEvent(
        event_type="utility",
        is_success=True,
        actor="system",
        target="player",
        abstract_action="world_entry",
        result_tags=["session_started", "location_ready"],
    )

    prepared_prologue = request.prologue_text.strip() if request.prologue_text else ""
    if prepared_prologue:
        narration = prepared_prologue
        narration_ms = 0
        narration_stage_id = "opening_prologue"
        narration_stage_label = "Opening Prologue"
    else:
        gm_engine = get_gm_engine()
        try:
            narration_started_at = perf_counter()
            narration = await gm_engine.generate_opening(
                record=record,
                user_input=request.world_prompt or "开始冒险",
            )
            narration_ms = round((perf_counter() - narration_started_at) * 1000)
        except LLMGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        narration_stage_id = "opening_scene"
        narration_stage_label = "GM Opening Scene"

    _remember_visible_text(record, narration)
    session_store.save(record)
    return GameTurnResponse(
        session_id=record.session_id,
        current_state=record.game_state,
        narration=narration,
        executed_events=[opening_event],
        mutation_logs=[],
        telemetry=RequestTelemetry(
            total_ms=round((perf_counter() - started_at) * 1000),
            stages=[
                TimingStage(
                    stage_id="session_bootstrap",
                    label="Session Bootstrap",
                    duration_ms=session_prepare_ms,
                ),
                TimingStage(
                    stage_id=narration_stage_id,
                    label=narration_stage_label,
                    duration_ms=narration_ms,
                ),
            ],
        ),
    )


@app.post("/api/game/action", response_model=GameTurnResponse)
async def game_action(request: GameActionRequest) -> GameTurnResponse:
    session_store = get_session_store()
    gm_engine = get_gm_engine()

    record = session_store.get(request.session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    try:
        turn_result = await gm_engine.run_turn(
            record=record,
            user_input=request.user_input,
        )
    except LLMGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _remember_visible_text(record, turn_result.narration)
    session_store.save(record)
    return GameTurnResponse(
        session_id=record.session_id,
        current_state=record.game_state,
        narration=turn_result.narration,
        executed_events=turn_result.executed_events,
        mutation_logs=turn_result.mutation_logs,
    )


@app.post("/api/game/save", response_model=GameSaveResponse)
def game_save(request: GameSaveRequest) -> GameSaveResponse:
    session_store = get_session_store()
    record = session_store.get(request.session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    return GameSaveResponse(runtime_snapshot=_build_runtime_snapshot(record))


@app.post("/api/game/restore", response_model=GameRestoreResponse)
def game_restore(request: GameRestoreRequest) -> GameRestoreResponse:
    session_store = get_session_store()
    record = session_store.restore_session(
        request.game_state,
        world_prompt=request.world_prompt,
        recent_visible_text=request.runtime_snapshot.recent_visible_text,
        nearby_npcs=request.runtime_snapshot.nearby_npcs,
        encounter_names=request.runtime_snapshot.encounter_names,
        lootable_targets={
            target_id: LootTarget(**loot_target.model_dump())
            for target_id, loot_target in request.runtime_snapshot.lootable_targets.items()
        },
        temp_item_counter=request.runtime_snapshot.temp_item_counter,
        dynamic_location_counter=request.runtime_snapshot.dynamic_location_counter,
    )
    return GameRestoreResponse(
        session_id=record.session_id,
        current_state=record.game_state,
    )


@app.post("/api/game/reset", response_model=GameResetResponse)
def game_reset(request: GameResetRequest) -> GameResetResponse:
    session_store = get_session_store()
    return GameResetResponse(ok=session_store.delete(request.session_id))


def _remember_visible_text(record: SessionRecord, text: str | None) -> None:
    if isinstance(text, str):
        normalized_text = text.strip()
        record.recent_visible_text = normalized_text or None
    else:
        record.recent_visible_text = None


def _build_runtime_snapshot(record: SessionRecord) -> RuntimeSessionSnapshot:
    return RuntimeSessionSnapshot(
        recent_visible_text=record.recent_visible_text,
        nearby_npcs=record.nearby_npcs,
        encounter_names=record.encounter_names,
        lootable_targets={
            target_id: SaveLootTarget(
                target_id=loot_target.target_id,
                display_name=loot_target.display_name,
                entity_type=loot_target.entity_type,
                summary=loot_target.summary,
                source_enemy_id=loot_target.source_enemy_id,
            )
            for target_id, loot_target in record.lootable_targets.items()
        },
        temp_item_counter=record.temp_item_counter,
        dynamic_location_counter=record.dynamic_location_counter,
    )


def _resolve_loot_turn(
    record: SessionRecord,
    parameters: dict[str, Any],
    *,
    user_input: str,
) -> tuple[list[MutationLog], ExecutedEvent, str | None]:
    target_id, target_label, consumed_target_id, is_valid_target = _resolve_loot_target(
        record,
        parameters,
    )
    if not is_valid_target:
        return [], ExecutedEvent(
            event_type="loot",
            is_success=False,
            actor="player",
            target=target_label,
            abstract_action=str(parameters.get("action_type", "loot")),
            result_tags=["invalid_loot_target"],
        ), None

    loot_pool = get_loot_generator().generate_pool(
        world_config=record.game_state.world_config,
        target_name=target_label,
        user_input=user_input,
        temp_key_factory=record.next_temp_item_key,
    )
    logs, event = resolve_loot(
        record.game_state,
        parameters,
        loot_pool=loot_pool,
        target_label=target_label,
    )
    return logs, event, consumed_target_id


def _resolve_loot_target(
    record: SessionRecord,
    parameters: dict[str, Any],
) -> tuple[str | None, str, str | None, bool]:
    raw_target_id = parameters.get("target_id")
    if isinstance(raw_target_id, str) and raw_target_id.strip():
        normalized_target_id = raw_target_id.strip()
        loot_target = record.get_loot_target(normalized_target_id)
        if loot_target is not None:
            return loot_target.target_id, loot_target.display_name, loot_target.target_id, True
        return None, normalized_target_id, None, False

    if len(record.lootable_targets) == 1:
        only_target = next(iter(record.lootable_targets.values()))
        return only_target.target_id, only_target.display_name, only_target.target_id, True

    raw_target_text = parameters.get("raw_target_text")
    if isinstance(raw_target_text, str) and raw_target_text.strip():
        normalized_target_text = raw_target_text.strip()
        if any(token in normalized_target_text for token in ("尸体", "残骸", "遗体", "首级")):
            return None, normalized_target_text, None, False
        return None, normalized_target_text, None, True

    return None, record.game_state.current_location_id, None, True
