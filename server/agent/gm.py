"""Agentic GM loop built on top of OpenAI-compatible tool calling."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Protocol

logger = logging.getLogger("uvicorn.error")

from server.agent.runtime_tools import (
    clone_session_record,
    commit_session_record,
    execute_runtime_tool,
    get_runtime_tool_schemas,
)
from server.llm.config import LLMSettings
from server.llm.json_payload import normalize_json_payload
from server.llm.openai_compatible import LLMGatewayError, OpenAICompatibleToolClient
from server.runtime.session_store import SessionRecord
from server.schemas.core import ExecutedEvent, MutationLog


@dataclass
class AgentTurnResult:
    """Final result returned by the GM agent for one turn."""

    narration: str
    executed_events: list[ExecutedEvent]
    mutation_logs: list[MutationLog]


@dataclass
class AgentTurnStreamUpdate:
    """Incremental update emitted while preparing a streamed turn response."""

    kind: str
    phase: str | None = None
    message: str | None = None
    delta: str | None = None
    narration: str | None = None
    result: AgentTurnResult | None = None
    source_record: SessionRecord | None = None


class ToolCallingNarrativeClient(Protocol):
    """Async chat client that can return either tool calls or final text."""

    async def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """Return one assistant turn with optional tool calls."""

    async def stream_text(
        self,
        *,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Yield text chunks for the final narration phase."""


class GameMasterAgent:
    """Narrative GM that resolves turns through runtime tools."""

    def __init__(
        self,
        llm_client: ToolCallingNarrativeClient,
        *,
        max_tool_rounds: int = 8,
    ) -> None:
        self._llm_client = llm_client
        self._max_tool_rounds = max_tool_rounds

    async def generate_opening(
        self,
        *,
        record: SessionRecord,
        user_input: str,
    ) -> str:
        """Render the opening scene directly from the campaign context."""

        messages = [
            {
                "role": "system",
                "content": _build_system_prompt(record, opening_mode=True),
            },
            {
                "role": "user",
                "content": _build_opening_user_prompt(record, user_input),
            },
        ]

        try:
            response = await self._llm_client.complete_chat(
                messages=messages,
                tools=None,
                temperature=0.6,
            )
        except LLMGatewayError:
            return _build_opening_fallback(record)

        content = str(response.get("content") or "").strip()
        if content:
            return content
        return _build_opening_fallback(record)

    async def run_turn(
        self,
        *,
        record: SessionRecord,
        user_input: str,
    ) -> AgentTurnResult:
        """Resolve one player turn by looping over tool calls and final narration."""

        working_record = clone_session_record(record)
        all_events: list[ExecutedEvent] = []
        all_logs: list[MutationLog] = []

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _build_system_prompt(working_record, opening_mode=False),
            },
            {
                "role": "user",
                "content": _build_turn_user_prompt(working_record, user_input),
            },
        ]
        tools = get_runtime_tool_schemas()

        for round_idx in range(self._max_tool_rounds):
            response = await self._llm_client.complete_chat(
                messages=messages,
                tools=tools,
                temperature=0.35,
            )
            tool_calls = list(response.get("tool_calls") or [])
            assistant_message = _build_assistant_message(response)

            if tool_calls:
                messages.append(assistant_message)
                for tool_call in tool_calls:
                    execution = execute_runtime_tool(
                        working_record,
                        tool_call["name"],
                        _parse_tool_arguments(tool_call.get("arguments")),
                    )
                    all_events.extend(execution.executed_events)
                    all_logs.extend(execution.mutation_logs)
                    logger.info(
                        "[GM run_turn] round=%d tool=%s events=%d",
                        round_idx, tool_call["name"], len(execution.executed_events),
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": json.dumps(
                                execution.observation,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )
                continue

            narration = str(response.get("content") or "").strip()
            if narration:
                logger.info(
                    "[GM run_turn] round=%d narration received, len=%d chars=%d",
                    round_idx, len(narration), _count_visible_characters(narration),
                )
                follow_up_instruction = _build_missing_resolution_instruction(
                    user_input=user_input,
                    executed_events=all_events,
                )
                if follow_up_instruction is not None:
                    logger.info("[GM run_turn] round=%d -> missing resolution, looping", round_idx)
                    messages.append(assistant_message)
                    messages.append(
                        {
                            "role": "user",
                            "content": follow_up_instruction,
                        }
                    )
                    continue

                rewrite_instruction = _build_narrative_rewrite_instruction(narration)
                if rewrite_instruction is not None:
                    logger.info("[GM run_turn] round=%d -> rigid menu rewrite, looping", round_idx)
                    messages.append(assistant_message)
                    messages.append(
                        {
                            "role": "user",
                            "content": rewrite_instruction,
                        }
                    )
                    continue

                length_instruction = _build_narrative_length_instruction(
                    narration=narration,
                    opening_mode=False,
                    user_input=user_input,
                )
                if length_instruction is not None:
                    logger.info(
                        "[GM run_turn] round=%d -> too short (%d/%d), looping",
                        round_idx, _count_visible_characters(narration),
                        _minimum_narrative_characters(opening_mode=False, user_input=user_input),
                    )
                    messages.append(assistant_message)
                    messages.append(
                        {
                            "role": "user",
                            "content": length_instruction,
                        }
                    )
                    continue

                logger.info("[GM run_turn] round=%d -> narration ACCEPTED", round_idx)
                commit_session_record(working_record, record)
                return AgentTurnResult(
                    narration=_scrub_narration(narration),
                    executed_events=all_events,
                    mutation_logs=all_logs,
                )

            logger.info("[GM run_turn] round=%d -> empty narration, prompting again", round_idx)
            messages.append(assistant_message)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You must now do one of two things: either keep calling tools until the turn "
                        "is fully resolved, or provide the final player-facing narration in Simplified Chinese."
                    ),
                }
            )

        logger.warning(
            "[GM run_turn] tool loop exhausted after %d rounds, events=%d, using fallback",
            self._max_tool_rounds, len(all_events),
        )
        commit_session_record(working_record, record)
        fallback_narration = await _generate_fallback_narration(
            self._llm_client, messages, user_input, all_events,
        )
        return AgentTurnResult(
            narration=_scrub_narration(fallback_narration),
            executed_events=all_events,
            mutation_logs=all_logs,
        )

    async def stream_turn(
        self,
        *,
        record: SessionRecord,
        user_input: str,
    ) -> AsyncIterator[AgentTurnStreamUpdate]:
        """Resolve a turn and emit only the finalized narration as stream updates."""

        working_record = clone_session_record(record)
        all_events: list[ExecutedEvent] = []
        all_logs: list[MutationLog] = []

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _build_system_prompt(working_record, opening_mode=False),
            },
            {
                "role": "user",
                "content": _build_turn_user_prompt(working_record, user_input),
            },
        ]
        tools = get_runtime_tool_schemas()

        yield AgentTurnStreamUpdate(
            kind="status",
            phase="resolving_tools",
            message="Resolving tools and validating world-state changes.",
        )

        for round_idx in range(self._max_tool_rounds):
            response = await self._llm_client.complete_chat(
                messages=messages,
                tools=tools,
                temperature=0.35,
            )
            tool_calls = list(response.get("tool_calls") or [])
            assistant_message = _build_assistant_message(response)

            if tool_calls:
                messages.append(assistant_message)
                for tool_call in tool_calls:
                    execution = execute_runtime_tool(
                        working_record,
                        tool_call["name"],
                        _parse_tool_arguments(tool_call.get("arguments")),
                    )
                    all_events.extend(execution.executed_events)
                    all_logs.extend(execution.mutation_logs)
                    logger.info(
                        "[GM stream_turn] round=%d tool=%s events=%d",
                        round_idx, tool_call["name"], len(execution.executed_events),
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": json.dumps(
                                execution.observation,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )
                continue

            narration = str(response.get("content") or "").strip()
            if narration:
                logger.info(
                    "[GM stream_turn] round=%d narration received, len=%d chars=%d",
                    round_idx, len(narration), _count_visible_characters(narration),
                )
                follow_up_instruction = _build_missing_resolution_instruction(
                    user_input=user_input,
                    executed_events=all_events,
                )
                if follow_up_instruction is not None:
                    logger.info("[GM stream_turn] round=%d -> missing resolution, looping", round_idx)
                    messages.append(assistant_message)
                    messages.append(
                        {
                            "role": "user",
                            "content": follow_up_instruction,
                        }
                    )
                    continue

                rewrite_instruction = _build_narrative_rewrite_instruction(narration)
                if rewrite_instruction is not None:
                    logger.info("[GM stream_turn] round=%d -> rigid menu rewrite, looping", round_idx)
                    messages.append(assistant_message)
                    messages.append(
                        {
                            "role": "user",
                            "content": rewrite_instruction,
                        }
                    )
                    continue

                length_instruction = _build_narrative_length_instruction(
                    narration=narration,
                    opening_mode=False,
                    user_input=user_input,
                )
                if length_instruction is not None:
                    logger.info(
                        "[GM stream_turn] round=%d -> too short (%d/%d), looping",
                        round_idx, _count_visible_characters(narration),
                        _minimum_narrative_characters(opening_mode=False, user_input=user_input),
                    )
                    messages.append(assistant_message)
                    messages.append(
                        {
                            "role": "user",
                            "content": length_instruction,
                        }
                    )
                    continue

                logger.info("[GM stream_turn] round=%d -> narration ACCEPTED", round_idx)
                yield AgentTurnStreamUpdate(
                    kind="status",
                    phase="writing_narration",
                    message="Final narration accepted and ready to stream.",
                )
                scrubbed_narration = _scrub_narration(narration)
                yield AgentTurnStreamUpdate(kind="narration_start")
                for chunk in _iter_narration_chunks(scrubbed_narration):
                    yield AgentTurnStreamUpdate(kind="narration_delta", delta=chunk)
                yield AgentTurnStreamUpdate(kind="narration_end", narration=scrubbed_narration)
                yield AgentTurnStreamUpdate(
                    kind="result",
                    result=AgentTurnResult(
                        narration=scrubbed_narration,
                        executed_events=all_events,
                        mutation_logs=all_logs,
                    ),
                    source_record=working_record,
                )
                return

            logger.info("[GM stream_turn] round=%d -> empty narration, prompting again", round_idx)
            messages.append(assistant_message)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You must now do one of two things: either keep calling tools until the turn "
                        "is fully resolved, or provide the final player-facing narration in Simplified Chinese."
                    ),
                }
            )

        logger.warning(
            "[GM stream_turn] tool loop exhausted after %d rounds, events=%d, using fallback",
            self._max_tool_rounds, len(all_events),
        )
        fallback_narration = await _generate_fallback_narration(
            self._llm_client, messages, user_input, all_events,
        )
        scrubbed_fallback = _scrub_narration(fallback_narration)
        yield AgentTurnStreamUpdate(
            kind="status",
            phase="writing_narration",
            message="Tool loop exhausted; generating narration via LLM fallback.",
        )
        yield AgentTurnStreamUpdate(kind="narration_start")
        for chunk in _iter_narration_chunks(scrubbed_fallback):
            yield AgentTurnStreamUpdate(kind="narration_delta", delta=chunk)
        yield AgentTurnStreamUpdate(kind="narration_end", narration=scrubbed_fallback)
        yield AgentTurnStreamUpdate(
            kind="result",
            result=AgentTurnResult(
                narration=scrubbed_fallback,
                executed_events=all_events,
                mutation_logs=all_logs,
            ),
            source_record=working_record,
        )


def build_gm_agent_from_env(*, env_file: str = ".env") -> GameMasterAgent:
    """Create an env-backed GM agent."""

    settings = LLMSettings.from_env(env_file=env_file)
    llm_client = OpenAICompatibleToolClient.from_settings(settings)
    return GameMasterAgent(llm_client)


def build_gm_engine_from_env(*, env_file: str = ".env") -> GameMasterAgent:
    """Backward-compatible alias for older API wiring."""

    return build_gm_agent_from_env(env_file=env_file)


def _build_system_prompt(record: SessionRecord, *, opening_mode: bool) -> str:
    world_book = record.game_state.world_config.world_book
    campaign_context = world_book.campaign_context
    power_scaling = world_book.power_scaling
    return dedent(
        f"""
        You are the lead writer and scene director of an interactive novel, not a menu-based game host.
        Resolve actions with tools, then write publish-grade prose in Simplified Chinese.

        Canon anchors you must respect:
        - Era and timeline: {campaign_context.era_and_timeline}
        - Macro world state: {campaign_context.macro_world_state}
        - Looming crisis: {campaign_context.looming_crisis}
        - Main quest: {campaign_context.main_quest.title} -> {campaign_context.main_quest.final_goal}
        - Current chapter: {campaign_context.current_chapter.title} -> {campaign_context.current_chapter.objective}
        - Chapter tension: {campaign_context.current_chapter.tension_level}/5
        - Power scaling label: {power_scaling.scale_label}
        - Impossible gap threshold: {power_scaling.impossible_gap_threshold}
        - Power tier ladder: {", ".join(f"{t.min_power}={t.label}" for t in power_scaling.power_tiers) if power_scaling.power_tiers else "not defined"}

        Tool and state discipline:
        1. EVERY risky action MUST be resolved via tools BEFORE narration. No exceptions.
        2. Use specialized tools when applicable: resolve_combat_action, resolve_exploration_action, resolve_loot_action.
        3. Any HP/MP/inventory/location/quest/encounter change must be committed through tools first.
        4. Compound player actions must be resolved as multiple sub-actions, never flattened.
        5. Do not replace the player's declared move with a different move.
        6. Never expose tool names, raw JSON, internal ids, or backend paths.
        7. Use update_quest_state for meaningful objective shifts. Avoid creating extra quests for small updates.
        8. Use update_encounter_state when pacing should transition away from pure combat.
        9. Use trigger_growth when a milestone, epiphany, or mastery break should visibly change the player.
        10. CRITICAL: You MUST call roll_d20_check for ANY risky, uncertain, or skill-dependent action the player attempts — including exploring unknown powers, attempting new techniques, sensing supernatural forces, or any action whose outcome is not guaranteed. Do NOT narrate the outcome of such actions without a tool roll first.
        11. CRITICAL: When a player describes discovering, awakening, or experimenting with new abilities, you MUST use trigger_growth to register the mechanical change. Do NOT narrate power awakenings or breakthroughs without committing the state change through trigger_growth first.

        Time-skip and montage handling:
        23. When the player requests a time skip or montage (e.g. "修炼到18岁", "训练三个月", "在此生活了十年"), this is NOT a risky action — do NOT call roll_d20_check for the passage of time itself.
        24. For time skips: call trigger_growth once with growth_type="stat_boost" to reflect accumulated growth, then write the montage narration directly. Do NOT keep looping tools trying to resolve every individual event within the time skip.
        25. Time-skip narration should be a vivid montage: compress years into sensory fragments, key moments, and turning points. 300-600 Chinese characters is ideal.
        26. Only call additional tools (modify_game_state, update_quest_state) if the time skip clearly produces a concrete state change you need to register. One trigger_growth call is usually sufficient.
        27. For trials, formations, traps, gauntlets, and sect entrance tests (e.g. "闯剑阵", "破阵", "过试炼"): treat them as risky environmental challenges. Start with roll_d20_check, then use modify_game_state or update_quest_state if the trial inflicts cost or clearly advances the scene.

        Director mindset:
        12. Combat is not a mandatory HP race. Dramatic actions may interrupt combat and trigger dialogue or standoff.
        13. Respect world_book.power_scaling. If the player's effective power is far below the opposition, narrate the struggle honestly even on a strong roll.
        14. NPCs must react with human motives (doubt, fear, curiosity, anger, restraint).
        15. Unless this is a literal split-second crisis, never end with rigid A/B/C menus.
        16. Before final narration, silently evaluate:
            <scene_eval>Should this scene stay in combat, or transition to dialogue/plot tension now?</scene_eval>
            Never output this tag.

        Interactive novel writing rules:
        17. Each live-turn response should read like a complete micro-chapter, ideally 500-800 Chinese characters.
        18. Include: visceral action detail, environment interaction, and NPC reaction/dialogue with psychology.
        19. Never expose numeric stat updates in prose; express consequences physically and emotionally.
        20. End with a natural suspense hook that invites freeform player input. NEVER end with binary choices like "是继续...还是..." or A/B options.
        21. ABSOLUTELY NO meta-commentary, inner monologue about your writing process, or behind-the-scenes reasoning. Never write things like "现在，我需要撰写叙述性文字" or "接下来，我将描述". Your output must be pure in-universe fiction.
        22. Never expose mechanical outcomes like "判定成功", "判定失败", "skill check passed/failed", "DC", or dice results. Translate all success/failure into concrete narrative consequences the character would physically experience.

        {"This is the opening scene. Start inside the configured opening_scene immediately, include sensory immersion, and end on a pressure hook." if opening_mode else "This is a live turn. Resolve as much as honestly possible via tools, then narrate one continuous immersive chapter segment."}
        """
    ).strip()

def _build_opening_user_prompt(record: SessionRecord, user_input: str) -> str:
    world_book = record.game_state.world_config.world_book
    campaign_context = world_book.campaign_context
    return dedent(
        f"""
        Player setup prompt:
        {user_input}

        Opening scene that must be obeyed:
        {campaign_context.opening_scene}

        Main quest:
        {campaign_context.main_quest.title} -> {campaign_context.main_quest.final_goal}

        Current chapter:
        {campaign_context.current_chapter.title} -> {campaign_context.current_chapter.objective}

        Current location id:
        {record.game_state.current_location_id}

        Nearby entities:
        {_format_nearby_entities(record)}

        Write the first scene now.
        Requirements:
        1. Start inside the configured opening scene immediately.
        2. Include concrete location detail, active motion, and at least two sensory details.
        3. End with a question, threat, or hook that forces the player to act.
        """
    ).strip()


def _build_turn_user_prompt(record: SessionRecord, user_input: str) -> str:
    current_node = record.current_location_node
    current_location_title = current_node.title if current_node is not None else record.game_state.current_location_id
    current_location_desc = current_node.base_desc if current_node is not None else record.location_summary
    world_book = record.game_state.world_config.world_book
    campaign_context = world_book.campaign_context
    connected_locations = _build_connected_location_snapshot(record)
    active_encounter = _build_active_encounter_snapshot(record)

    prompt_payload = {
        "player_input": user_input,
        "recent_visible_text": record.recent_visible_text,
        "current_location": {
            "location_id": record.game_state.current_location_id,
            "title": current_location_title,
            "base_desc": current_location_desc,
        },
        "player_state": {
            "stats": record.game_state.player.stats,
            "attributes": record.game_state.player.attributes,
            "skills": record.game_state.player.skills,
            "skill_labels": record.game_state.player.skill_labels,
            "power_level": record.game_state.player.power_level,
            "rank_label": record.game_state.player.rank_label,
            "growth": {
                "xp": record.game_state.player.growth.xp,
                "level": record.game_state.player.growth.level,
                "proficiency_bonus": record.game_state.player.growth.proficiency_bonus,
                "unspent_stat_points": record.game_state.player.growth.unspent_stat_points,
                "last_growth_reason": record.game_state.player.growth.last_growth_reason,
            },
            "inventory": record.game_state.player.inventory,
            "temporary_items": record.game_state.player.temporary_items,
        },
        "glossary": {
            "stats": record.game_state.world_config.glossary.stats,
            "attributes": record.game_state.world_config.glossary.attributes,
            "damage_types": record.game_state.world_config.glossary.damage_types,
            "item_categories": record.game_state.world_config.glossary.item_categories,
        },
        "storyline": {
            "main_quest": {
                "quest_id": campaign_context.main_quest.quest_id,
                "title": campaign_context.main_quest.title,
                "final_goal": campaign_context.main_quest.final_goal,
                "summary": campaign_context.main_quest.summary,
                "linked_quest_id": campaign_context.main_quest.linked_quest_id,
            },
            "current_chapter": {
                "chapter_id": campaign_context.current_chapter.chapter_id,
                "title": campaign_context.current_chapter.title,
                "objective": campaign_context.current_chapter.objective,
                "tension_level": campaign_context.current_chapter.tension_level,
                "progress_percent": campaign_context.current_chapter.progress_percent,
                "linked_quest_id": campaign_context.current_chapter.linked_quest_id,
            },
            "milestones": [
                {
                    "milestone_id": milestone.milestone_id,
                    "title": milestone.title,
                    "summary": milestone.summary,
                    "is_completed": milestone.is_completed,
                }
                for milestone in campaign_context.milestones
            ],
            "power_scaling": {
                "scale_label": world_book.power_scaling.scale_label,
                "danger_gap_threshold": world_book.power_scaling.danger_gap_threshold,
                "impossible_gap_threshold": world_book.power_scaling.impossible_gap_threshold,
                "power_tiers": [
                    {
                        "min_power": tier.min_power,
                        "label": tier.label,
                    }
                    for tier in world_book.power_scaling.power_tiers
                ],
                "benchmark_examples": [
                    {
                        "subject": benchmark.subject,
                        "offense_rating": benchmark.offense_rating,
                        "defense_rating": benchmark.defense_rating,
                        "notes": benchmark.notes,
                    }
                    for benchmark in world_book.power_scaling.benchmark_examples
                ],
            },
        },
        "nearby_entities": [
            {
                "entity_id": entity.entity_id,
                "display_name": entity.display_name,
                "entity_type": entity.entity_type,
                "summary": entity.summary,
            }
            for entity in record.build_nearby_entities()
        ],
        "connected_locations": connected_locations,
        "active_encounter": active_encounter,
        "quest_log": [
            {
                "quest_id": quest.quest_id,
                "title": quest.title,
                "status": quest.status,
                "summary": quest.summary,
                "progress": quest.progress,
            }
            for quest in record.game_state.quest_log.values()
        ],
    }
    return dedent(
        f"""
        Resolve the player's turn strictly from the snapshot below. Do not rewrite the scene into a different setup.
        If the player described multiple distinct phases, resolve them as multiple sub-actions instead of flattening them.

        Scene snapshot:
        {json.dumps(prompt_payload, ensure_ascii=False, indent=2)}
        """
    ).strip()


def _build_connected_location_snapshot(record: SessionRecord) -> list[dict[str, str]]:
    topology = record.game_state.world_config.topology
    connected_ids = topology.edges.get(record.game_state.current_location_id, [])
    results: list[dict[str, str]] = []
    for node_id in connected_ids:
        node = topology.nodes.get(node_id)
        if node is None:
            continue
        results.append(
            {
                "location_id": node_id,
                "title": node.title,
            }
        )
    return results


def _build_active_encounter_snapshot(record: SessionRecord) -> dict[str, Any] | None:
    active_encounter_id = record.game_state.active_encounter
    if not active_encounter_id:
        return None

    encounter = record.game_state.encounter_log.get(active_encounter_id)
    if encounter is None:
        return {
            "encounter_id": active_encounter_id,
            "status": "active",
            "enemy_ids": sorted(record.game_state.encounter_entities.keys()),
        }

    return {
        "encounter_id": encounter.encounter_id,
        "label": encounter.label,
        "status": encounter.status,
        "summary": encounter.summary,
        "enemy_ids": encounter.enemy_ids,
        "location_id": encounter.location_id,
    }


def _format_nearby_entities(record: SessionRecord) -> str:
    entities = record.build_nearby_entities()
    if not entities:
        return "- none"
    return "\n".join(
        f"- {entity.display_name} ({entity.entity_id}, {entity.entity_type})"
        for entity in entities
    )


def _build_assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": str(response.get("content") or ""),
    }
    tool_calls = list(response.get("tool_calls") or [])
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_call["id"],
                "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": str(tool_call.get("arguments") or "{}"),
                },
            }
            for tool_call in tool_calls
        ]
    return message


def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    """Parse LLM tool call arguments into a dict.

    When the payload is missing, empty, or fails JSON parsing, returns a dict
    with an explicit ``__parse_error`` key instead of a silent ``{}``.  This
    allows ``execute_runtime_tool`` to surface a meaningful error to the GM
    rather than silently running with default values.
    """
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str) or not raw_arguments.strip():
        return {"__parse_error": "empty_arguments", "__raw": str(raw_arguments)}
    try:
        parsed = json.loads(normalize_json_payload(raw_arguments))
    except json.JSONDecodeError as exc:
        return {
            "__parse_error": "json_decode_failed",
            "__raw": raw_arguments[:200],
            "__detail": str(exc),
        }
    if not isinstance(parsed, dict):
        return {
            "__parse_error": "non_dict_payload",
            "__raw": raw_arguments[:200],
            "__actual_type": type(parsed).__name__,
        }
    return parsed


async def _generate_fallback_narration(
    llm_client: ToolCallingNarrativeClient,
    messages: list[dict[str, Any]],
    user_input: str,
    events: list[ExecutedEvent],
) -> str:
    """Generate a proper narrative via LLM when the tool loop exhausts its rounds.

    Instead of returning a hardcoded template, asks the LLM to write narration
    based on the conversation so far. Falls back to the template only if the
    LLM call itself fails.
    """
    # Build a summary of resolved events for context
    event_summary_parts: list[str] = []
    for event in events:
        outcome = "成功" if event.is_success else "失败"
        event_summary_parts.append(
            f"- {event.abstract_action} ({event.event_type}): {outcome}"
        )
    event_summary = "\n".join(event_summary_parts) if event_summary_parts else "无"

    fallback_prompt = dedent(
        f"""
        The tool resolution loop reached its maximum rounds. You must now write the final narration immediately.

        Player action: {user_input}

        Resolved tool events so far:
        {event_summary}

        Write a complete, immersive narration in Simplified Chinese that:
        1. Covers the player's declared action with vivid, in-universe prose.
        2. Incorporates all resolved tool outcomes naturally without exposing mechanics.
        3. Is at least 300 visible Chinese characters.
        4. Ends with a natural suspense hook — NOT a binary choice or menu.
        5. Contains NO meta-commentary, NO mechanical terms like "判定成功/失败", NO dice results.

        Write the narration now:
        """
    ).strip()

    fallback_messages = messages + [
        {
            "role": "user",
            "content": fallback_prompt,
        }
    ]

    try:
        response = await llm_client.complete_chat(
            messages=fallback_messages,
            tools=None,
            temperature=0.6,
        )
        narration = str(response.get("content") or "").strip()
        if narration and _count_visible_characters(narration) >= 120:
            return narration
    except LLMGatewayError:
        pass

    # Last resort: use the template fallback
    return _build_turn_fallback(user_input, events)


def _build_opening_fallback(record: SessionRecord) -> str:
    opening_scene = record.game_state.world_config.world_book.campaign_context.opening_scene.strip()
    question = "\u4f60\u73b0\u5728\u8981\u600e\u4e48\u505a\uff1f"
    if opening_scene.endswith(("\u3002", "\uff01", "\uff1f", "!", "?")):
        return f"{opening_scene}\n\n{question}"
    return f"{opening_scene}\u3002\n\n{question}"


def _build_turn_fallback(user_input: str, events: list[ExecutedEvent]) -> str:
    """Generate an immersive fallback narration when the tool loop exhausts its rounds.

    Rather than mechanical fragments, weaves resolved facts into a short narrative
    paragraph that feels like a natural continuation of the story.
    """
    action_text = user_input.strip() or "出手"

    if not events:
        if _looks_like_trial_or_hazard(action_text):
            return (
                f"你踏入{action_text}的瞬间，四周气机便同时绷紧，像无数看不见的锋刃贴着皮肤游走。"
                "你只能强压住呼吸，在急促的心跳里重新分辨生门与死角。"
                "阵势还没有真正停下，下一重变化已经在更深处悄悄抬头。"
            )
        return (
            "你屏住呼吸，重新审视了一遍局势。"
            "眼前的变化还没有彻底落定，空气里的压迫感依旧悬着，逼得你不得不重新判断下一步该把力道落向哪里。"
        )

    successes = [event for event in events if event.is_success]
    failures = [event for event in events if not event.is_success]
    success_tags = [tag for event in successes for tag in event.result_tags]
    failure_tags = [tag for event in failures for tag in event.result_tags]
    event_types = {event.event_type for event in events}

    lines = [_build_fallback_opening(action_text, event_types)]
    if successes:
        lines.append(_build_fallback_outcome(action_text, success_tags, event_types, positive=True))
    if failures:
        lines.append(_build_fallback_outcome(action_text, failure_tags, event_types, positive=False))
    lines.append(_build_fallback_hook(action_text, event_types, success_tags, failure_tags))
    return "".join(part for part in lines if part)


def _build_fallback_opening(action_text: str, event_types: set[str]) -> str:
    if _looks_like_trial_or_hazard(action_text):
        options = [
            f"你刚踏进{action_text}的范围，原本沉寂的气机便一下子活了过来，像有无数锋线从四面八方朝你压下。",
            f"{action_text}的那一步才刚落稳，四周灵气便猛地收束，连呼吸都像被逼成了一条细线。",
            f"你一头撞进{action_text}，耳边顿时尽是锐利的颤鸣，像整片空间都在试图把你的步伐切碎。",
        ]
        return options[_stable_text_index(action_text, len(options))]

    if "combat" in event_types:
        return f"你这一记{action_text}落下时，场中的气氛几乎在瞬间绷到了极点。"
    if "exploration" in event_types:
        return f"你顺着{action_text}的念头往前探去，眼前的局势立刻显出了新的棱角。"
    return f"你刚一动手去做{action_text}，周围的气息就随之悄然偏转。"


def _build_fallback_outcome(
    action_text: str,
    tags: list[str],
    event_types: set[str],
    *,
    positive: bool,
) -> str:
    if positive:
        if "target_killed" in tags:
            return "最前方那股逼人的杀意被你硬生生撕开了一道口子，局势终于露出了可以喘息的缝隙。"
        if "critical_hit" in tags:
            return "其中一次碰撞几乎是贴着生死线劈开的，连周围积压的气机都被你震得微微一散。"
        if _looks_like_trial_or_hazard(action_text):
            return "你终究还是抓住了阵势转换时最关键的那一拍，让自己没有被第一波杀机直接吞进去。"
        if "exploration" in event_types:
            return "你在混乱里摸到了一点正确的方向，至少没有彻底失去前进的抓手。"
        return "局面并没有完全失控，你至少逼出了一线对自己有利的空隙。"

    if "player_downed" in tags:
        return "可反震与压迫也顺着破绽一起撞了进来，逼得你胸口发闷，连脚步都险些当场散开。"
    if "missed" in tags or "dodged_by_player" in tags or "power_gap_blocked" in tags:
        return "可并不是每一步都落在你预想的位置，稍慢半拍的代价立刻被眼前的危险放大。"
    if _looks_like_trial_or_hazard(action_text):
        return "可阵势深处的变化比你预想得更阴狠，几道藏在暗处的锋意还是逼得你不得不临时改换身形。"
    if "loot" in event_types:
        return "但你想要的东西并没有就这样轻易落进手里，细节里的阻滞仍旧在拖慢你的节奏。"
    return "但事态也没有完全照着你的心意发展，暗处的变化仍在不断挤压你能够腾挪的余地。"


def _build_fallback_hook(
    action_text: str,
    event_types: set[str],
    success_tags: list[str],
    failure_tags: list[str],
) -> str:
    if _looks_like_trial_or_hazard(action_text):
        hooks = [
            "你还来不及把气息彻底理顺，阵纹更深处已经有新的剑鸣抬了起来。",
            "可真正的考验显然还没结束，下一道变化已经顺着你脚下的落点逼近。",
            "这还只是第一轮碰撞，藏在后面的杀机正顺着灵气回流一点点抬头。",
        ]
        return hooks[_stable_text_index(action_text + "".join(success_tags) + "".join(failure_tags), len(hooks))]

    if "combat" in event_types:
        return "对面的气息并没有因此彻底沉下去，下一次真正见血的碰撞随时可能压上来。"
    if "exploration" in event_types:
        return "前方的路并未完全敞开，新的线索和新的风险都还在更深处等着你伸手去碰。"
    return "局势还在继续向前滚动，你已经没有太多迟疑的空档了。"


def _stable_text_index(text: str, size: int) -> int:
    if size <= 0:
        return 0
    return sum(ord(char) for char in text) % size


def _build_missing_resolution_instruction(
    *,
    user_input: str,
    executed_events: list[ExecutedEvent],
) -> str | None:
    requirements = _infer_resolution_requirements(user_input)
    skill_check_count = sum(1 for event in executed_events if event.event_type == "skill_check")
    specialized_resolution_count = sum(
        1
        for event in executed_events
        if event.event_type in {"combat", "exploration", "loot"}
    )
    resolved_risk_count = skill_check_count + specialized_resolution_count
    has_mp_change = any(
        event.event_type == "state_change" and "mp_changed" in event.result_tags
        for event in executed_events
    )
    has_growth_trigger = any(
        event.event_type == "growth"
        for event in executed_events
    )

    missing_items: list[str] = []
    if resolved_risk_count < requirements["min_skill_checks"]:
        missing_items.append(
            f"the player declared multiple risky sub-actions, and you still owe {requirements['min_skill_checks'] - resolved_risk_count} more risky resolution step(s)"
        )
    if requirements["needs_mp_change"] and not has_mp_change:
        missing_items.append(
            "the player attempted a protective or magical action, so you must apply its MP or resource cost with modify_game_state"
        )
    if requirements["needs_growth_trigger"] and not has_growth_trigger:
        missing_items.append(
            "the player described discovering, awakening, or experimenting with new abilities/powers — you MUST call trigger_growth to register this mechanical change before narrating"
        )

    if not missing_items:
        return None

    details = "\n".join(f"- {item}" for item in missing_items)
    return (
        "You have not fully resolved the player's declared action yet.\n"
        "Do not write the final narration yet. Finish the missing tool calls first:\n"
        f"{details}"
    )


def _build_narrative_rewrite_instruction(narration: str) -> str | None:
    if _looks_like_rigid_menu_ending(narration):
        return (
            "Rewrite your last narration in Simplified Chinese while preserving all resolved facts and consequences. "
            "Do not append rigid action menus or numbered options. End on a natural dramatic hook that invites "
            "freeform player input."
        )
    return None


def _looks_like_time_skip(user_input: str) -> bool:
    """Detect montage / time-skip actions that should have relaxed length requirements."""
    if not user_input:
        return False
    normalized = user_input.strip()
    time_skip_markers = (
        "修炼到",
        "修炼了",
        "训练到",
        "训练了",
        "生活了",
        "在此时",
        "过了一个",
        "度过",
        "时光飞逝",
        "几年后",
        "数年后",
        "个月后",
        "十年",
        "数年",
        "长大到",
        "成长到",
        "一直到",
        "直到",
        "在此期间",
        "日复一日",
        "年复一年",
        "岁月",
    )
    return any(marker in normalized for marker in time_skip_markers)


def _looks_like_trial_or_hazard(user_input: str) -> bool:
    """Detect formation / trap / sect-trial actions that behave like risky scenes."""
    if not user_input:
        return False
    normalized = user_input.strip()
    challenge_markers = (
        "闯剑阵",
        "剑阵",
        "破阵",
        "阵法",
        "禁制",
        "试炼",
        "考核",
        "闯关",
        "过关",
        "机关",
        "幻阵",
        "杀阵",
        "试剑",
        "剑关",
    )
    return any(marker in normalized for marker in challenge_markers)


def _minimum_narrative_characters(*, opening_mode: bool, user_input: str = "") -> int:
    if opening_mode:
        return 380
    if _looks_like_time_skip(user_input):
        return 300
    if _looks_like_trial_or_hazard(user_input):
        return 380
    return 500


def _build_narrative_length_instruction(
    *,
    narration: str,
    opening_mode: bool,
    user_input: str = "",
) -> str | None:
    minimum_characters = _minimum_narrative_characters(
        opening_mode=opening_mode,
        user_input=user_input,
    )
    visible_characters = _count_visible_characters(narration)
    if visible_characters >= minimum_characters:
        return None

    return (
        "Expand your previous narration in Simplified Chinese without changing factual outcomes from tool observations. "
        "Keep continuity, deepen movement detail, environment interaction, and NPC emotional reaction. "
        f"Return at least {minimum_characters} visible Chinese characters and end with a natural suspense hook."
    )


def _looks_like_rigid_menu_ending(narration: str) -> bool:
    compact = narration.strip().lower()
    if not compact:
        return False

    menu_markers = (
        "请选择",
        "选择你的行动",
        "选择你的应对",
        "可选行动",
        "选项：",
        "option:",
        "choose your action",
    )
    if any(marker in compact for marker in menu_markers):
        return True

    ending_window = compact[-140:]
    if re.search(r"(?:^|\n)\s*[a-dＡ-Ｄ][\.:、\)\s]", ending_window):
        return True
    if re.search(r"\[[^\]]{0,40}(?:请选择|选项|action)[^\]]{0,40}\]$", ending_window):
        return True
    if re.search(r"【[^】]{0,50}(?:请选择|选项|行动)[^】]{0,50}】$", ending_window):
        return True
    return False


def _count_visible_characters(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _scrub_narration(narration: str) -> str:
    """Remove meta-commentary and mechanical leakage from LLM narration output.

    This is a last-resort safety net.  The system prompt already forbids these
    patterns, but LLMs occasionally violate their instructions — especially
    smaller or less-aligned models.
    """
    cleaned = narration

    # Strip common meta-reasoning prefixes the LLM might prepend before the
    # actual story, e.g. "现在，我需要撰写叙述性文字。接下来，我将描述……"
    meta_prefix_patterns = [
        r"(?:现在|接下来|首先|然后|最后)[，,]\s*"
        r"(?:我|我们|作者|GM|dm)\s*"
        r"(?:需要|要|将|会|开始|继续)\s*"
        r"(?:撰写|写|描述|叙述|说明|解释|分析|回顾|总结|列出|展现|呈现)\s*",
        r"(?:---+)\s*\n",  # horizontal rule before narration
    ]
    for pattern in meta_prefix_patterns:
        cleaned = re.sub(pattern, "", cleaned, count=0, flags=re.IGNORECASE)

    # Strip lines that are pure meta-commentary (e.g. planning thoughts)
    meta_line_patterns = [
        r"^(?:首先|然后|接下来|最后|现在|同时|此外|另外|为了|基于|根据|考虑到)",
        r"^(?:I\s+need\s+to|I\s+will\s+now|I\s+should|Let\s+me|Now\s+I\s+will)",
        r"^(?:我需要|我要|我将|我来|我该|让我|我现在)",
    ]
    lines = cleaned.split("\n")
    filtered_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            filtered_lines.append(line)
            continue
        skip = False
        for pattern in meta_line_patterns:
            if re.search(pattern, stripped, re.IGNORECASE):
                # Heuristic: skip if the line looks like an instruction rather
                # than fiction (short, no dialogue markers, no narrative verbs)
                if len(stripped) < 60 and "「" not in stripped and "」" not in stripped:
                    skip = True
                    break
        if not skip:
            filtered_lines.append(line)
    cleaned = "\n".join(filtered_lines)

    # Remove explicit mechanical result phrases (判定成功/失败, DC values, etc.)
    mechanical_patterns = [
        r'[你他她它].{0,6}["\u201c]([^\u201d"]+)[\u201d"].{0,4}判定(?:成功|失败)了?[。，]?',
        r"判定(?:成功|失败)",
        r"[Dd][Cc][:：]?\s*\d+",
        r"roll[:：]?\s*\d+",
        r"skill\s*check\s*(?:passed|failed|succeeded)",
    ]
    for pattern in mechanical_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Collapse excessive blank lines that may result from the above stripping
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


def _iter_narration_chunks(narration: str, *, chunk_size: int = 24) -> list[str]:
    normalized = narration.strip()
    if not normalized:
        return []

    chunks: list[str] = []
    current = ""
    punctuation = {"。", "！", "？", "；", "，", "\n", ".", "!", "?", ";", ","}
    for char in normalized:
        current += char
        if len(current) >= chunk_size and char in punctuation:
            chunks.append(current)
            current = ""

    if current:
        chunks.append(current)

    return chunks

def _infer_resolution_requirements(user_input: str) -> dict[str, int | bool]:
    normalized = user_input.strip()
    if not normalized:
        return {"min_skill_checks": 0, "needs_mp_change": False, "needs_growth_trigger": False}

    categories = 0
    if any(
        token in normalized
        for token in (
            "\u5047\u88c5",
            "\u4f6f\u88c5",
            "\u8bc8\u964d",
            "\u6b3a\u9a97",
            "\u6295\u964d",
        )
    ):
        categories += 1
    if any(
        token in normalized
        for token in (
            "\u7838",
            "\u5c04",
            "\u6253",
            "\u523a",
            "\u780d",
            "\u653b\u51fb",
            "\u8e22",
        )
    ):
        categories += 1
    if any(
        token in normalized
        for token in (
            "\u7ffb\u6eda",
            "\u95ea\u907f",
            "\u4fa7\u6251",
            "\u8eb2\u5f00",
            "\u51b2",
            "\u9003",
            "\u8dd1",
        )
    ):
        categories += 1

    has_compound_marker = any(
        token in normalized
        for token in (
            "\u7136\u540e",
            "\u63a5\u7740",
            "\u540c\u65f6",
            "\u518d",
            "\u968f\u540e",
            "\u8d81\u673a",
        )
    )
    min_skill_checks = 1 if categories >= 1 else 0
    if categories >= 2 and has_compound_marker:
        min_skill_checks = 2

    # Ability exploration / awakening must also trigger a skill check
    ability_exploration_markers = (
        "\u63a2\u7d22",
        "\u89c9\u9192",
        "\u5c1d\u8bd5",
        "\u53d1\u52a8",
        "\u4f7f\u7528",
        "\u53ec\u5524",
        "\u53ec\u96c6",
        "\u91ca\u653e",
        "\u6fc0\u53d1",
        "\u80fd\u529b",
        "\u679c\u5b9e",
        "\u9b54\u6cd5",
        "\u529b\u91cf",
        "\u6280\u80fd",
        "\u5fc5\u6740",
        "\u62db\u5f0f",
    )
    if any(token in normalized for token in ability_exploration_markers):
        if min_skill_checks < 1:
            min_skill_checks = 1

    if _looks_like_trial_or_hazard(normalized):
        min_skill_checks = max(min_skill_checks, 1)

    needs_mp_change = any(
        token in normalized
        for token in (
            "\u62a4\u76fe",
            "\u7ed3\u754c",
            "\u5c4f\u969c",
            "\u9632\u62a4",
            "\u62a4\u6301",
        )
    )

    # Detect ability awakening / discovery — must trigger_growth
    ability_awakening_markers = (
        "\u89c9\u9192",
        "\u6fc0\u53d1",
        "\u7a81\u7834",
        "\u9886\u609f",
        "\u638c\u63e1",
        "\u89e3\u9501",
        "\u5f00\u542f",
        "\u8fdb\u5316",
        "\u5347\u7ea7",
        "\u6210\u957f",
        "\u53d1\u73b0\u80fd\u529b",
        "\u80fd\u529b\u89c9\u9192",
        "\u65b0\u80fd\u529b",
        "\u529b\u91cf\u89c9\u9192",
    )
    needs_growth_trigger = any(token in normalized for token in ability_awakening_markers)

    return {
        "min_skill_checks": min_skill_checks,
        "needs_mp_change": needs_mp_change,
        "needs_growth_trigger": needs_growth_trigger,
    }



