"""FastAPI application wiring the frontend to the runtime engine."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from server.brain.central import CentralBrainError, build_central_brain_from_env
from server.generators.loot_generator import LootGenerator, build_loot_generator_from_env
from server.generators.map_generator import DynamicMapGenerator, build_map_generator_from_env
from server.initialization.weaver import WorldWeaverError, generate_world_config
from server.llm.openai_compatible import LLMGatewayError
from server.narrative.narrator import build_narrator_from_env
from server.pipelines.combat import resolve_combat
from server.pipelines.exploration import resolve_exploration
from server.pipelines.loot import resolve_loot
from server.runtime.session_store import DEFAULT_WEAPON_KEY, SessionRecord, SessionStore
from server.schemas.core import ExecutedEvent, GameState, MutationLog, WorldConfig
from server.state.mutator import apply_mutations


class WorldGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)


class WorldGenerateResponse(BaseModel):
    world_config: WorldConfig


class GameStartRequest(BaseModel):
    world_config: WorldConfig
    world_prompt: str | None = None


class GameActionRequest(BaseModel):
    session_id: str
    user_input: str = Field(..., min_length=1)


class GameTurnResponse(BaseModel):
    session_id: str
    current_state: GameState
    narration: str
    executed_events: list[ExecutedEvent] = Field(default_factory=list)
    mutation_logs: list[MutationLog] = Field(default_factory=list)


app = FastAPI(title="同人互动沙盒引擎 API")


@lru_cache(maxsize=1)
def get_session_store() -> SessionStore:
    return SessionStore()


@lru_cache(maxsize=1)
def get_central_brain():
    return build_central_brain_from_env()


@lru_cache(maxsize=1)
def get_narrator():
    return build_narrator_from_env()


@lru_cache(maxsize=1)
def get_loot_generator() -> LootGenerator:
    return build_loot_generator_from_env()


@lru_cache(maxsize=1)
def get_map_generator() -> DynamicMapGenerator:
    return build_map_generator_from_env()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/world/generate", response_model=WorldGenerateResponse)
def world_generate(request: WorldGenerateRequest) -> WorldGenerateResponse:
    try:
        world_config = generate_world_config(request.prompt)
    except (WorldWeaverError, LLMGatewayError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return WorldGenerateResponse(world_config=world_config)


@app.post("/api/game/start", response_model=GameTurnResponse)
async def game_start(request: GameStartRequest) -> GameTurnResponse:
    session_store = get_session_store()
    narrator = get_narrator()

    record = session_store.create_session(
        request.world_config,
        world_prompt=request.world_prompt,
    )
    opening_event = ExecutedEvent(
        event_type="utility",
        is_success=True,
        actor="system",
        target="player",
        abstract_action="world_entry",
        result_tags=["session_started", "location_ready"],
    )
    try:
        narration = await narrator.generate_narration(
            current_state=record.game_state,
            events=[opening_event],
            user_input=request.world_prompt or "开始冒险",
        )
    except LLMGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return GameTurnResponse(
        session_id=record.session_id,
        current_state=record.game_state,
        narration=narration,
        executed_events=[opening_event],
        mutation_logs=[],
    )


@app.post("/api/game/action", response_model=GameTurnResponse)
async def game_action(request: GameActionRequest) -> GameTurnResponse:
    session_store = get_session_store()
    central_brain = get_central_brain()
    narrator = get_narrator()

    record = session_store.get(request.session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="会话不存在或已失效。")

    try:
        decision_outcome = central_brain.decide(
            player_input=request.user_input,
            game_state=record.game_state,
            location_summary=record.location_summary,
            active_quest_ids=record.game_state.world_config.initial_quests,
            nearby_entities=record.build_nearby_entities(),
        )
    except (CentralBrainError, LLMGatewayError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not decision_outcome.should_execute:
        return GameTurnResponse(
            session_id=record.session_id,
            current_state=record.game_state,
            narration=decision_outcome.clarification_message or "请再具体说明一下你现在想做什么。",
            executed_events=[],
            mutation_logs=[],
        )

    decision = decision_outcome.decision
    if decision.pipeline_type == "combat":
        mutation_logs, events = _resolve_combat_turn(record, dict(decision.parameters))
        _register_lootables_from_combat(record, events)
        next_state = apply_mutations(record.game_state, mutation_logs)
        record.game_state = next_state
        record.sync_after_state_update()
        session_store.save(record)

        try:
            narration = await narrator.generate_narration(
                current_state=record.game_state,
                events=events,
                user_input=request.user_input,
            )
        except LLMGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return GameTurnResponse(
            session_id=record.session_id,
            current_state=record.game_state,
            narration=narration,
            executed_events=events,
            mutation_logs=mutation_logs,
        )

    if decision.pipeline_type == "loot":
        mutation_logs, event, consumed_target_id = _resolve_loot_turn(
            record,
            dict(decision.parameters),
            user_input=request.user_input,
        )
        next_state = apply_mutations(record.game_state, mutation_logs)
        record.game_state = next_state
        if consumed_target_id is not None:
            record.consume_loot_target(consumed_target_id)
        session_store.save(record)

        try:
            narration = await narrator.generate_narration(
                current_state=record.game_state,
                events=[event],
                user_input=request.user_input,
            )
        except LLMGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return GameTurnResponse(
            session_id=record.session_id,
            current_state=record.game_state,
            narration=narration,
            executed_events=[event],
            mutation_logs=mutation_logs,
        )

    if decision.pipeline_type == "exploration":
        mutation_logs, event = _resolve_exploration_turn(
            record,
            dict(decision.parameters),
        )
        next_state = apply_mutations(record.game_state, mutation_logs)
        record.game_state = next_state
        record.sync_after_state_update()
        session_store.save(record)

        try:
            narration = await narrator.generate_narration(
                current_state=record.game_state,
                events=[event],
                user_input=request.user_input,
            )
        except LLMGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return GameTurnResponse(
            session_id=record.session_id,
            current_state=record.game_state,
            narration=narration,
            executed_events=[event],
            mutation_logs=mutation_logs,
        )

    if decision.pipeline_type == "utility":
        event = ExecutedEvent(
            event_type="utility",
            is_success=True,
            actor="player",
            target="player",
            abstract_action="state_query",
            result_tags=["state_query"],
        )
        try:
            narration = await narrator.generate_narration(
                current_state=record.game_state,
                events=[event],
                user_input=request.user_input,
            )
        except LLMGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return GameTurnResponse(
            session_id=record.session_id,
            current_state=record.game_state,
            narration=narration,
            executed_events=[event],
            mutation_logs=[],
        )

    event = ExecutedEvent(
        event_type=decision.pipeline_type,
        is_success=False,
        actor="player",
        target="world",
        abstract_action="deferred",
        result_tags=["pipeline_not_implemented"],
    )
    return GameTurnResponse(
        session_id=record.session_id,
        current_state=record.game_state,
        narration=f"已识别到“{decision.pipeline_type}”类型的意图，但这个管线在当前版本里还没有接入。",
        executed_events=[event],
        mutation_logs=[],
    )


def _resolve_combat_turn(
    record: SessionRecord,
    parameters: dict[str, Any],
) -> tuple[list[MutationLog], list[ExecutedEvent]]:
    parameters.setdefault("attacker_id", "player")
    if "target_id" not in parameters and len(record.game_state.encounter_entities) == 1:
        parameters["target_id"] = next(iter(record.game_state.encounter_entities.keys()))

    parameters["weapon_key"] = _select_weapon_key(record.game_state, parameters)
    return resolve_combat(record.game_state, parameters)


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
        if any(token in normalized_target_text for token in ("尸体", "残骸", "遗体", "尸首")):
            return None, normalized_target_text, None, False
        return None, normalized_target_text, None, True

    return None, record.game_state.current_location_id, None, True


def _resolve_exploration_turn(
    record: SessionRecord,
    parameters: dict[str, Any],
) -> tuple[list[MutationLog], ExecutedEvent]:
    destination_id, destination_name = _resolve_destination_node(record, parameters)
    return resolve_exploration(
        record.game_state,
        parameters,
        map_generator=get_map_generator(),
        target_node_id=destination_id,
        target_name=destination_name,
    )


def _resolve_destination_node(
    record: SessionRecord,
    parameters: dict[str, Any],
) -> tuple[str, str]:
    explicit_destination_id = parameters.get("destination_id")
    topology = record.game_state.world_config.topology
    if isinstance(explicit_destination_id, str) and explicit_destination_id.strip():
        normalized_destination_id = explicit_destination_id.strip()
        if normalized_destination_id in topology.nodes:
            return normalized_destination_id, topology.nodes[normalized_destination_id].title

    raw_target_text = parameters.get("raw_target_text")
    if isinstance(raw_target_text, str) and raw_target_text.strip():
        normalized_target_name = raw_target_text.strip()
        matched_node_id = _match_destination_id_by_title(record.game_state, normalized_target_name)
        if matched_node_id is not None:
            return matched_node_id, topology.nodes[matched_node_id].title
        return record.next_dynamic_location_id(), normalized_target_name

    current_node = topology.nodes.get(record.game_state.current_location_id)
    fallback_name = current_node.title if current_node is not None else record.game_state.current_location_id
    return record.game_state.current_location_id, fallback_name


def _match_destination_id_by_title(
    current_state: GameState,
    target_name: str,
) -> str | None:
    normalized_target = _normalize_name(target_name)
    for node_id, node in current_state.world_config.topology.nodes.items():
        if _normalize_name(node.title) == normalized_target:
            return node_id
    return None


def _normalize_name(value: str) -> str:
    return "".join(value.strip().lower().split())


def _register_lootables_from_combat(
    record: SessionRecord,
    events: list[ExecutedEvent],
) -> None:
    for event in events:
        if event.event_type != "combat" or event.actor != "player":
            continue
        if "target_killed" not in event.result_tags:
            continue
        if event.target in record.game_state.encounter_entities:
            record.register_defeated_enemy_loot_target(event.target)


def _select_weapon_key(
    current_state: GameState,
    parameters: dict[str, Any],
) -> str:
    explicit = parameters.get("weapon_key") or parameters.get("weapon_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    inventory_keys = [
        key for key, value in current_state.player.inventory.items() if value > 0
    ]
    if len(inventory_keys) == 1:
        return inventory_keys[0]
    if DEFAULT_WEAPON_KEY in current_state.player.inventory:
        return DEFAULT_WEAPON_KEY
    return inventory_keys[0] if inventory_keys else DEFAULT_WEAPON_KEY
