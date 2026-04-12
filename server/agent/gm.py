"""Agentic GM loop built on top of OpenAI-compatible tool calling."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Protocol

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
        max_tool_rounds: int = 6,
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

        for _ in range(self._max_tool_rounds):
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
                follow_up_instruction = _build_missing_resolution_instruction(
                    user_input=user_input,
                    executed_events=all_events,
                )
                if follow_up_instruction is not None:
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
                )
                if length_instruction is not None:
                    messages.append(assistant_message)
                    messages.append(
                        {
                            "role": "user",
                            "content": length_instruction,
                        }
                    )
                    continue

                commit_session_record(working_record, record)
                return AgentTurnResult(
                    narration=_scrub_narration(narration),
                    executed_events=all_events,
                    mutation_logs=all_logs,
                )

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

        commit_session_record(working_record, record)
        return AgentTurnResult(
            narration=_build_turn_fallback(user_input, all_events),
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

        for _ in range(self._max_tool_rounds):
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
                follow_up_instruction = _build_missing_resolution_instruction(
                    user_input=user_input,
                    executed_events=all_events,
                )
                if follow_up_instruction is not None:
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
                )
                if length_instruction is not None:
                    messages.append(assistant_message)
                    messages.append(
                        {
                            "role": "user",
                            "content": length_instruction,
                        }
                    )
                    continue

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

        fallback_narration = _build_turn_fallback(user_input, all_events)
        yield AgentTurnStreamUpdate(
            kind="status",
            phase="writing_narration",
            message="Using fallback narration because the tool loop exhausted its rounds.",
        )
        yield AgentTurnStreamUpdate(kind="narration_start")
        for chunk in _iter_narration_chunks(fallback_narration):
            yield AgentTurnStreamUpdate(kind="narration_delta", delta=chunk)
        yield AgentTurnStreamUpdate(kind="narration_end", narration=fallback_narration)
        yield AgentTurnStreamUpdate(
            kind="result",
            result=AgentTurnResult(
                narration=fallback_narration,
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
    if not events:
        return "你屏住呼吸，重新审视了一遍局势，等待着下一个出手的时机。"

    # Separate events by outcome
    successes = [e for e in events if e.is_success]
    failures = [e for e in events if not e.is_success]

    # Gather outcome descriptors from tags
    def _tag_summary(tags: list[str]) -> str:
        if "target_killed" in tags:
            return "一道致命打击落下。"
        if "critical_hit" in tags:
            return "暴击划破了防线。"
        if "missed" in tags or "dodged_by_player" in tags:
            return "攻击落了空。"
        if "player_downed" in tags:
            return "你感到身体猛地一沉，脚步踉跄。"
        if "found_nothing" in tags:
            return "翻遍了四周，一无所获。"
        if any(t.startswith("found_") for t in tags):
            return "手中多了些什么。"
        if "state_change" in tags:
            return "某种力量悄然改变了局势。"
        if "inventory" in tags:
            return "随身物品发生了变化。"
        return ""

    # Count unique event types
    combat_count = sum(1 for e in events if e.event_type == "combat")
    loot_count = sum(1 for e in events if e.event_type == "loot")
    exploration_count = sum(1 for e in events if e.event_type == "exploration")

    lines: list[str] = []

    # Opening: a grounding observation about the player's declared action
    action_snippet = user_input.strip()[:12]
    openings = [
        f"你{action_snippet}——",
        f"就在你{action_snippet}的瞬间，",
        f"一切发生在心跳之间。",
        f"空气中弥漫着紧张的气息，你{action_snippet}——",
    ]
    lines.append(openings[hash(user_input) % len(openings)])

    # Body: weave in resolved outcomes
    if successes and failures:
        lines.append(
            "有些事情如你所愿，有些却偏离了轨道。"
        )
    elif successes:
        lines.append("事情正在朝着有利的方向发展。")
        if successes:
            tag_desc = _tag_summary(sum((e.result_tags for e in successes), []))
            if tag_desc:
                lines.append(tag_desc)
    elif failures:
        lines.append("但事态并未如你所愿。")
        failed_tags = sum((e.result_tags for e in failures), [])
        tag_desc = _tag_summary(failed_tags)
        if tag_desc:
            lines.append(tag_desc)

    # Closing: a suspense hook
    hooks = [
        "新的时机稍纵即逝。",
        "下一个间隙随时可能出现。",
        "你需要重新评估眼前的局面。",
        "故事还在继续。",
        "——你的下一步是什么？",
    ]
    lines.append(hooks[hash(user_input) % len(hooks)])

    return "".join(lines)


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


def _build_narrative_length_instruction(
    *,
    narration: str,
    opening_mode: bool,
) -> str | None:
    minimum_characters = 380 if opening_mode else 500
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



