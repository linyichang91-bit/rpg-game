"""Central Brain implementation for intent parsing and routing."""

from __future__ import annotations

import re
from textwrap import dedent
from typing import Any, Protocol

from pydantic import ValidationError

from server.llm.config import LLMSettings
from server.llm.json_payload import normalize_json_payload
from server.llm.openai_compatible import LLMGatewayError, OpenAICompatibleJSONClient
from server.schemas.core import GameState
from server.schemas.orchestrator import (
    ContextEntity,
    DecisionContext,
    OrchestratorDecision,
    PipelineSpec,
    PromptBundle,
    RoutingOutcome,
)


DEFAULT_PIPELINE_SPECS: tuple[PipelineSpec, ...] = (
    PipelineSpec(
        pipeline_type="combat",
        description="Hostile actions such as attacking, shooting, striking, or using improvised violence.",
        required_parameters=["action_type", "target_id"],
        optional_parameters=["weapon_key", "weapon_id", "skill_key", "raw_target_text", "raw_item_text"],
    ),
    PipelineSpec(
        pipeline_type="exploration",
        description="Movement, navigation, scouting, and inspecting where to go next.",
        required_parameters=["action_type"],
        optional_parameters=["destination_id", "target_id", "raw_target_text"],
    ),
    PipelineSpec(
        pipeline_type="loot",
        description="Searching, looting, opening, taking, using, or manipulating environmental objects.",
        required_parameters=["action_type"],
        optional_parameters=["target_id", "item_id", "raw_target_text", "raw_item_text"],
    ),
    PipelineSpec(
        pipeline_type="dialogue",
        description="Speaking to an entity, negotiating, threatening verbally, or asking questions in character.",
        required_parameters=["target_id", "dialogue_intent"],
        optional_parameters=["utterance", "raw_target_text"],
    ),
    PipelineSpec(
        pipeline_type="skill_check",
        description="Attempting a risky or uncertain action that should map to an abstract skill or attribute check.",
        required_parameters=["action_type"],
        optional_parameters=["skill_key", "target_id", "approach", "raw_target_text"],
    ),
    PipelineSpec(
        pipeline_type="lore_query",
        description="Asking about world facts, rules, history, or known lore instead of taking an action.",
        required_parameters=["query_topic"],
        optional_parameters=["target_id"],
    ),
    PipelineSpec(
        pipeline_type="utility",
        description="Outcomes that inspect state such as inventory, stats, objectives, or current status.",
        required_parameters=["query_type"],
        optional_parameters=["target_id"],
    ),
    PipelineSpec(
        pipeline_type="ooc",
        description="Out-of-character commands such as help, save, load, restart, or rules explanations.",
        required_parameters=["command"],
        optional_parameters=[],
    ),
)


DEFAULT_CLARIFICATION_MESSAGE = "请再具体说明一下你现在想做什么。"
HEURISTIC_TARGET_MESSAGE = "请说清你要对哪个目标动手。"
HEURISTIC_DESTINATION_MESSAGE = "请再说清你想去哪里。"


class CentralBrainError(Exception):
    """Base exception for Central Brain failures."""


class DecisionValidationError(CentralBrainError):
    """Raised when the model response cannot be validated into the decision schema."""


class StructuredJSONClient(Protocol):
    """Provider-agnostic interface for LLMs that can return JSON text."""

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> str:
        """Return a JSON string matching the requested response schema."""


def build_decision_context(
    game_state: GameState,
    *,
    location_summary: str | None = None,
    active_quest_ids: list[str] | None = None,
    nearby_entities: list[ContextEntity] | None = None,
) -> DecisionContext:
    """Create a prompt-facing context snapshot from the current game state."""

    return DecisionContext(
        session_id=game_state.session_id,
        world_id=game_state.world_config.world_id,
        world_theme=game_state.world_config.theme,
        current_location_id=game_state.current_location_id,
        location_summary=location_summary,
        active_encounter=game_state.active_encounter,
        active_quest_ids=active_quest_ids or [],
        nearby_entities=nearby_entities or [],
    )


def build_prompt_bundle(
    *,
    player_input: str,
    context: DecisionContext | dict[str, Any],
    pipeline_specs: list[PipelineSpec] | tuple[PipelineSpec, ...] = DEFAULT_PIPELINE_SPECS,
) -> PromptBundle:
    """Assemble the strict system and user prompts for the Central Brain."""

    validated_context = DecisionContext.model_validate(context)

    system_prompt = dedent(
        """
        You are an intent parser and routing brain for a narrative game engine.
        Your job is to classify the player's natural-language input and extract
        structured parameters for backend execution.

        Constraints:
        1. Parse only. Never decide whether an action succeeds, never describe consequences, and never mutate state.
        2. Output one JSON object only, and it must match the OrchestratorDecision schema.
        3. Match referenced targets to known entity_id or location_id values from Context whenever possible.
        4. If an action requires a target and Context does not support a unique match, set clarification_needed.
        5. Initial version: handle only the first or primary intent. Ignore later chained actions.
        6. Be tolerant of unusual actions. For example, "hit him with a banana" should still route to combat or loot.
        7. Prefer abstract ids in parameters. If an id is unknown, include raw_target_text or raw_item_text as fallback.
        8. If the player is asking for lore, rules, help, or other non-embodied queries, route to lore_query, utility, or ooc.
        9. If clarification_needed is not null, write it in concise Simplified Chinese for the player.

        Few-shot examples:
        Example 1 Input: "I shoot the boar."
        Example 1 Output:
        {"pipeline_type":"combat","confidence":0.94,"parameters":{"action_type":"attack","target_id":"enemy_boar_01","weapon_id":"item_ranged_01"},"clarification_needed":null}

        Example 2 Input: "Show me my inventory."
        Example 2 Output:
        {"pipeline_type":"utility","confidence":0.97,"parameters":{"query_type":"inventory"},"clarification_needed":null}

        Example 3 Input: "I attack him."
        Example 3 Output:
        {"pipeline_type":"combat","confidence":0.41,"parameters":{"action_type":"attack","raw_target_text":"him"},"clarification_needed":"请说清你要攻击哪个目标。"}
        """
    ).strip()

    user_prompt = dedent(
        f"""
        Player Input:
        {player_input}

        Context:
        {validated_context.model_dump_json(indent=2)}

        Available Pipelines & Parameter Specs:
        {_format_pipeline_specs(pipeline_specs)}
        """
    ).strip()

    return PromptBundle(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=OrchestratorDecision.model_json_schema(),
    )


def should_pause_for_clarification(
    decision: OrchestratorDecision,
    *,
    confidence_threshold: float,
) -> bool:
    """Return whether pipeline execution should pause for clarification."""

    return decision.confidence < confidence_threshold or decision.clarification_needed is not None


class CentralBrain:
    """Provider-agnostic intent parser that converts player text into routing decisions."""

    def __init__(
        self,
        llm_client: StructuredJSONClient,
        *,
        confidence_threshold: float = 0.6,
        max_validation_retries: int = 1,
        pipeline_specs: list[PipelineSpec] | tuple[PipelineSpec, ...] = DEFAULT_PIPELINE_SPECS,
    ) -> None:
        self._llm_client = llm_client
        self._confidence_threshold = confidence_threshold
        self._max_validation_retries = max_validation_retries
        self._pipeline_specs = pipeline_specs

    def decide(
        self,
        *,
        player_input: str,
        game_state: GameState,
        location_summary: str | None = None,
        active_quest_ids: list[str] | None = None,
        nearby_entities: list[ContextEntity] | None = None,
    ) -> RoutingOutcome:
        """Parse a player's input into a validated orchestration decision."""

        context = build_decision_context(
            game_state,
            location_summary=location_summary,
            active_quest_ids=active_quest_ids,
            nearby_entities=nearby_entities,
        )
        prompt_bundle = build_prompt_bundle(
            player_input=player_input,
            context=context,
            pipeline_specs=self._pipeline_specs,
        )
        heuristic_outcome = _build_heuristic_outcome(
            player_input=player_input,
            game_state=game_state,
            context=context,
        )

        try:
            decision = self._request_validated_decision(prompt_bundle)
        except (DecisionValidationError, LLMGatewayError):
            if heuristic_outcome is not None:
                return heuristic_outcome
            raise

        if should_pause_for_clarification(
            decision,
            confidence_threshold=self._confidence_threshold,
        ):
            if heuristic_outcome is not None and heuristic_outcome.should_execute:
                return heuristic_outcome
            failure_reason = (
                "clarification_needed"
                if decision.clarification_needed is not None
                else "low_confidence"
            )
            return RoutingOutcome(
                decision=decision,
                should_execute=False,
                clarification_message=_normalize_clarification_message(
                    decision.clarification_needed
                ),
                failure_reason=failure_reason,
            )

        return RoutingOutcome(
            decision=decision,
            should_execute=True,
            clarification_message=None,
            failure_reason=None,
        )

    def _request_validated_decision(self, prompt_bundle: PromptBundle) -> OrchestratorDecision:
        last_error: ValidationError | None = None

        for _ in range(self._max_validation_retries + 1):
            raw_response = self._llm_client.generate_json(
                system_prompt=prompt_bundle.system_prompt,
                user_prompt=prompt_bundle.user_prompt,
                response_schema=prompt_bundle.response_schema,
            )
            try:
                return OrchestratorDecision.model_validate_json(
                    normalize_json_payload(raw_response)
                )
            except ValidationError as exc:
                last_error = exc

        raise DecisionValidationError("路由中枢未能生成合法的结构化决策数据。") from last_error


def build_central_brain_from_env(
    *,
    env_file: str = ".env",
    confidence_threshold: float = 0.6,
    max_validation_retries: int = 1,
    pipeline_specs: list[PipelineSpec] | tuple[PipelineSpec, ...] = DEFAULT_PIPELINE_SPECS,
) -> CentralBrain:
    """Create a Central Brain instance from environment-backed LLM settings."""

    settings = LLMSettings.from_env(env_file=env_file)
    llm_client = OpenAICompatibleJSONClient.from_settings(settings)
    return CentralBrain(
        llm_client,
        confidence_threshold=confidence_threshold,
        max_validation_retries=max_validation_retries,
        pipeline_specs=pipeline_specs,
    )


def _format_pipeline_specs(
    pipeline_specs: list[PipelineSpec] | tuple[PipelineSpec, ...],
) -> str:
    return "\n".join(
        (
            f"- {spec.pipeline_type}: {spec.description} "
            f"(required={list(spec.required_parameters)}, optional={list(spec.optional_parameters)})"
        )
        for spec in pipeline_specs
    )


def _normalize_clarification_message(message: str | None) -> str:
    if isinstance(message, str) and _contains_cjk(message):
        return message
    return DEFAULT_CLARIFICATION_MESSAGE


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _build_heuristic_outcome(
    *,
    player_input: str,
    game_state: GameState,
    context: DecisionContext,
) -> RoutingOutcome | None:
    normalized_text = _normalize_player_text(player_input)
    if not normalized_text:
        return None

    utility_decision = _build_heuristic_utility_decision(normalized_text)
    if utility_decision is not None:
        return RoutingOutcome(
            decision=utility_decision,
            should_execute=True,
            clarification_message=None,
            failure_reason=None,
        )

    exploration_outcome = _build_heuristic_exploration_outcome(
        player_input=player_input,
        normalized_text=normalized_text,
        game_state=game_state,
    )
    if exploration_outcome is not None:
        return exploration_outcome

    loot_outcome = _build_heuristic_loot_outcome(
        player_input=player_input,
        normalized_text=normalized_text,
        game_state=game_state,
        context=context,
    )
    if loot_outcome is not None:
        return loot_outcome

    ooc_decision = _build_heuristic_ooc_decision(normalized_text)
    if ooc_decision is not None:
        return RoutingOutcome(
            decision=ooc_decision,
            should_execute=True,
            clarification_message=None,
            failure_reason=None,
        )

    return _build_heuristic_combat_outcome(
        normalized_text=normalized_text,
        game_state=game_state,
        context=context,
    )


def _build_heuristic_utility_decision(normalized_text: str) -> OrchestratorDecision | None:
    if any(keyword in normalized_text for keyword in ("背包", "物品", "装备", "道具", "库存")):
        return OrchestratorDecision(
            pipeline_type="utility",
            confidence=0.93,
            parameters={"query_type": "inventory"},
            clarification_needed=None,
        )

    if any(
        keyword in normalized_text
        for keyword in ("状态", "属性", "面板", "血量", "生命", "魔力", "体力", "数值", "信息")
    ):
        return OrchestratorDecision(
            pipeline_type="utility",
            confidence=0.9,
            parameters={"query_type": "status"},
            clarification_needed=None,
        )

    return None


def _build_heuristic_exploration_outcome(
    *,
    player_input: str,
    normalized_text: str,
    game_state: GameState,
) -> RoutingOutcome | None:
    movement_keywords = ("去", "前往", "进入", "走进", "走向", "赶往", "移动到", "前去", "回到", "去往")
    if not any(keyword in normalized_text for keyword in movement_keywords):
        return None

    target_name = _extract_travel_target(player_input)
    if target_name is None:
        decision = OrchestratorDecision(
            pipeline_type="exploration",
            confidence=0.42,
            parameters={"action_type": "travel"},
            clarification_needed=HEURISTIC_DESTINATION_MESSAGE,
        )
        return RoutingOutcome(
            decision=decision,
            should_execute=False,
            clarification_message=HEURISTIC_DESTINATION_MESSAGE,
            failure_reason="clarification_needed",
        )

    destination_id = _match_location_id(target_name, game_state)
    parameters: dict[str, Any] = {
        "action_type": "travel",
        "raw_target_text": target_name,
    }
    if destination_id is not None:
        parameters["destination_id"] = destination_id

    return RoutingOutcome(
        decision=OrchestratorDecision(
            pipeline_type="exploration",
            confidence=0.87,
            parameters=parameters,
            clarification_needed=None,
        ),
        should_execute=True,
        clarification_message=None,
        failure_reason=None,
    )


def _build_heuristic_loot_outcome(
    *,
    player_input: str,
    normalized_text: str,
    game_state: GameState,
    context: DecisionContext,
) -> RoutingOutcome | None:
    del game_state
    loot_keywords = (
        "搜",
        "搜查",
        "搜刮",
        "翻找",
        "摸尸",
        "搜身",
        "掏",
        "拾取",
        "捡",
        "捡起",
        "打开",
        "开箱",
        "检查",
    )
    if not any(keyword in normalized_text for keyword in loot_keywords):
        return None

    loot_entities = [
        entity
        for entity in context.nearby_entities
        if entity.entity_type.lower() in {"corpse", "container", "lootable", "environment"}
    ]
    target_id = _match_entity_id(normalized_text, loot_entities)
    generic_loot_terms = ("尸体", "残骸", "宝箱", "箱子", "桌子", "抽屉", "柜子", "敌人尸体")

    parameters: dict[str, Any] = {
        "action_type": "loot",
    }

    if target_id is None and len(loot_entities) == 1:
        if any(term in normalized_text for term in generic_loot_terms) or "搜" in normalized_text:
            target_id = loot_entities[0].entity_id

    if target_id is not None:
        parameters["target_id"] = target_id
    else:
        parameters["raw_target_text"] = _extract_loot_target(player_input) or player_input.strip()

    if target_id is None and len(loot_entities) > 1 and not any(
        term in normalized_text for term in generic_loot_terms
    ):
        decision = OrchestratorDecision(
            pipeline_type="loot",
            confidence=0.44,
            parameters=parameters,
            clarification_needed=HEURISTIC_TARGET_MESSAGE,
        )
        return RoutingOutcome(
            decision=decision,
            should_execute=False,
            clarification_message=HEURISTIC_TARGET_MESSAGE,
            failure_reason="clarification_needed",
        )

    decision = OrchestratorDecision(
        pipeline_type="loot",
        confidence=0.86,
        parameters=parameters,
        clarification_needed=None,
    )
    return RoutingOutcome(
        decision=decision,
        should_execute=True,
        clarification_message=None,
        failure_reason=None,
    )


def _build_heuristic_ooc_decision(normalized_text: str) -> OrchestratorDecision | None:
    if any(keyword in normalized_text for keyword in ("帮助", "help", "说明", "规则")):
        return OrchestratorDecision(
            pipeline_type="ooc",
            confidence=0.88,
            parameters={"command": "help"},
            clarification_needed=None,
        )

    if any(keyword in normalized_text for keyword in ("重开", "重新开始", "restart")):
        return OrchestratorDecision(
            pipeline_type="ooc",
            confidence=0.86,
            parameters={"command": "restart"},
            clarification_needed=None,
        )

    return None


def _build_heuristic_combat_outcome(
    *,
    normalized_text: str,
    game_state: GameState,
    context: DecisionContext,
) -> RoutingOutcome | None:
    combat_keywords = (
        "攻击",
        "打",
        "砍",
        "刺",
        "斩",
        "杀",
        "射",
        "开枪",
        "挥",
        "猛击",
        "痛击",
        "扑向",
        "踢",
        "揍",
    )
    if not any(keyword in normalized_text for keyword in combat_keywords):
        return None

    enemy_entities = [
        entity for entity in context.nearby_entities if entity.entity_type.lower() == "enemy"
    ]
    target_id = _match_entity_id(normalized_text, enemy_entities)
    if target_id is None and len(game_state.encounter_entities) == 1:
        target_id = next(iter(game_state.encounter_entities.keys()))

    weapon_key = _select_weapon_key(normalized_text, game_state.player.inventory)
    parameters: dict[str, Any] = {
        "action_type": "attack",
        "attacker_id": "player",
    }
    if weapon_key is not None:
        parameters["weapon_key"] = weapon_key
    if target_id is not None:
        parameters["target_id"] = target_id

    if target_id is None and len(enemy_entities) > 1:
        decision = OrchestratorDecision(
            pipeline_type="combat",
            confidence=0.48,
            parameters=parameters,
            clarification_needed=HEURISTIC_TARGET_MESSAGE,
        )
        return RoutingOutcome(
            decision=decision,
            should_execute=False,
            clarification_message=HEURISTIC_TARGET_MESSAGE,
            failure_reason="clarification_needed",
        )

    decision = OrchestratorDecision(
        pipeline_type="combat",
        confidence=0.87 if target_id is not None else 0.76,
        parameters=parameters,
        clarification_needed=None,
    )
    return RoutingOutcome(
        decision=decision,
        should_execute=True,
        clarification_message=None,
        failure_reason=None,
    )


def _match_entity_id(
    normalized_text: str,
    entities: list[ContextEntity],
) -> str | None:
    for entity in entities:
        entity_id = entity.entity_id.strip()
        display_name = entity.display_name.strip()
        if entity_id and entity_id.lower() in normalized_text:
            return entity_id
        if display_name and _normalize_player_text(display_name) in normalized_text:
            return entity_id

    generic_hostile_terms = ("敌人", "怪物", "对手", "目标", "叛忍", "巡逻兵", "野猪")
    if entities and any(term in normalized_text for term in generic_hostile_terms):
        if len(entities) == 1:
            return entities[0].entity_id

    return None


def _match_location_id(target_name: str, game_state: GameState) -> str | None:
    normalized_target = _normalize_player_text(target_name)
    for node_id, node in game_state.world_config.topology.nodes.items():
        if node_id == normalized_target:
            return node_id
        if _normalize_player_text(node.title) == normalized_target:
            return node_id
    return None


def _extract_travel_target(player_input: str) -> str | None:
    stripped = player_input.strip().rstrip("。！？!?")
    patterns = (
        r"^(?:我想)?(?:去|前往|进入|走进|走向|赶往|移动到|前去|去往|回到)(.+)$",
        r"^(?:朝着|朝)(.+?)(?:走去|前进|过去)$",
    )
    for pattern in patterns:
        match = re.match(pattern, stripped)
        if match is not None:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
    return None


def _extract_loot_target(player_input: str) -> str | None:
    stripped = player_input.strip().rstrip("。！？!?")
    patterns = (
        r"^(?:我)?(?:仔细)?(?:搜查|搜刮|翻找|检查|摸尸|搜身)(.+)$",
        r"^(?:打开|开箱)(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, stripped)
        if match is not None:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
    return None


def _select_weapon_key(normalized_text: str, inventory: dict[str, int]) -> str | None:
    available_items = [key for key, amount in inventory.items() if amount > 0]
    if not available_items:
        return None

    for item_key in available_items:
        if item_key.lower() in normalized_text:
            return item_key

    if len(available_items) == 1:
        return available_items[0]

    weapon_like_items = [
        item_key
        for item_key in available_items
        if any(marker in item_key.lower() for marker in ("weapon", "gun", "blade", "sword", "bow"))
    ]
    if len(weapon_like_items) == 1:
        return weapon_like_items[0]

    return available_items[0]


def _normalize_player_text(text: str) -> str:
    lowered = text.strip().lower()
    return re.sub(r"\s+", "", lowered)
