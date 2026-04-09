"""Agentic GM loop built on top of OpenAI-compatible tool calling."""

from __future__ import annotations

import json
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

                commit_session_record(working_record, record)
                return AgentTurnResult(
                    narration=narration,
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


def build_gm_agent_from_env(*, env_file: str = ".env") -> GameMasterAgent:
    """Create an env-backed GM agent."""

    settings = LLMSettings.from_env(env_file=env_file)
    llm_client = OpenAICompatibleToolClient.from_settings(settings)
    return GameMasterAgent(llm_client)


def build_gm_engine_from_env(*, env_file: str = ".env") -> GameMasterAgent:
    """Backward-compatible alias for older API wiring."""

    return build_gm_agent_from_env(env_file=env_file)


def _build_system_prompt(record: SessionRecord, *, opening_mode: bool) -> str:
    campaign_context = record.game_state.world_config.world_book.campaign_context
    return dedent(
        f"""
        You are a ruthless, immersive tabletop GM for a fanfiction sandbox.
        You are not a classifier and not a passive narrator. You must interpret the player's declared move,
        call tools whenever resolution is needed, and only then write one continuous scene.

        World anchors you must never violate:
        - Era and timeline: {campaign_context.era_and_timeline}
        - Macro world state: {campaign_context.macro_world_state}
        - Looming crisis: {campaign_context.looming_crisis}

        Mandatory rules:
        1. Any risky action must be resolved with roll_d20_check before you narrate its outcome.
        2. Any HP, MP, inventory, or location change must be committed through the proper tool first.
        3. Compound actions must be split into multiple sub-actions. Do not flatten them into one vague roll.
        4. Do not stop after the first failure if later sub-actions could still happen.
        5. Do not replace the player's declared move with a different move. Resolve what the player actually tried.
        6. Never invent concrete damage, resource loss, movement, item transfer, or deaths without tool support.
        7. Short replies such as A, B, C, left, dodge, or yes must be interpreted against the recent visible text.
        8. Never expose tool names, raw JSON, internal ids, abstract keys, or backend paths.

        Tone rules:
        - All player-facing narration must be written in Simplified Chinese.
        - Keep the prose tense, physical, and immediate.
        - Respect canon-era boundaries strictly.

        {"This is the opening scene. The very first sentence must land inside the configured opening_scene and end on a pressure hook." if opening_mode else "This is a live turn. Resolve as much as you honestly can, use multiple tool calls when needed, then narrate the result."}
        """
    ).strip()


def _build_opening_user_prompt(record: SessionRecord, user_input: str) -> str:
    campaign_context = record.game_state.world_config.world_book.campaign_context
    return dedent(
        f"""
        Player setup prompt:
        {user_input}

        Opening scene that must be obeyed:
        {campaign_context.opening_scene}

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
    connected_locations = _build_connected_location_snapshot(record)

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
            "inventory": record.game_state.player.inventory,
            "temporary_items": record.game_state.player.temporary_items,
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
        "active_quests": record.game_state.world_config.initial_quests,
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
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str) or not raw_arguments.strip():
        return {}
    try:
        parsed = json.loads(normalize_json_payload(raw_arguments))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_opening_fallback(record: SessionRecord) -> str:
    opening_scene = record.game_state.world_config.world_book.campaign_context.opening_scene.strip()
    question = "\u4f60\u73b0\u5728\u8981\u600e\u4e48\u505a\uff1f"
    if opening_scene.endswith(("\u3002", "\uff01", "\uff1f", "!", "?")):
        return f"{opening_scene}\n\n{question}"
    return f"{opening_scene}\u3002\n\n{question}"


def _build_turn_fallback(user_input: str, events: list[ExecutedEvent]) -> str:
    if not events:
        return (
            f"\u4f60\u77ed\u6682\u5730\u5c4f\u4f4f\u547c\u5438\uff0c"
            f"\u91cd\u65b0\u786e\u8ba4\u4e86\u4e00\u904d\u5c40\u52bf\uff0c"
            f"\u7136\u540e\u51c6\u5907\u6267\u884c\u201c{user_input}\u201d\u3002"
        )

    fragments: list[str] = []
    for event in events:
        if event.event_type == "skill_check":
            if event.is_success:
                fragments.append(
                    f"\u4f60\u7684\u201c{event.abstract_action}\u201d\u5224\u5b9a\u6210\u529f\u4e86\u3002"
                )
            else:
                fragments.append(
                    f"\u4f60\u7684\u201c{event.abstract_action}\u201d\u5224\u5b9a\u5931\u8d25\u4e86\u3002"
                )
        elif event.event_type == "state_change":
            fragments.append("\u5c40\u52bf\u56e0\u6b64\u53d1\u751f\u4e86\u5b9e\u8d28\u53d8\u5316\u3002")
        elif event.event_type == "inventory":
            fragments.append(
                "\u4f60\u7684\u968f\u8eab\u7269\u54c1\u4e5f\u8ddf\u7740\u51fa\u73b0\u4e86\u53d8\u5316\u3002"
            )
        elif event.event_type == "tool_error":
            fragments.append(
                "\u4f46\u4f60\u7684\u52a8\u4f5c\u91cc\u6709\u4e00\u90e8\u5206\u6ca1\u80fd\u771f\u6b63\u843d\u5b9e\u3002"
            )

    if not fragments:
        return (
            f"\u4f60\u5c1d\u8bd5\u6267\u884c\u201c{user_input}\u201d\uff0c"
            "\u5c40\u52bf\u968f\u4e4b\u8d77\u4e86\u53d8\u5316\u3002"
        )
    return "".join(fragments)


def _build_missing_resolution_instruction(
    *,
    user_input: str,
    executed_events: list[ExecutedEvent],
) -> str | None:
    requirements = _infer_resolution_requirements(user_input)
    skill_check_count = sum(1 for event in executed_events if event.event_type == "skill_check")
    has_mp_change = any(
        event.event_type == "state_change" and "mp_changed" in event.result_tags
        for event in executed_events
    )

    missing_items: list[str] = []
    if skill_check_count < requirements["min_skill_checks"]:
        missing_items.append(
            f"the player declared multiple risky sub-actions, and you still owe {requirements['min_skill_checks'] - skill_check_count} more roll_d20_check call(s)"
        )
    if requirements["needs_mp_change"] and not has_mp_change:
        missing_items.append(
            "the player attempted a protective or magical action, so you must apply its MP or resource cost with modify_game_state"
        )

    if not missing_items:
        return None

    details = "\n".join(f"- {item}" for item in missing_items)
    return (
        "You have not fully resolved the player's declared action yet.\n"
        "Do not write the final narration yet. Finish the missing tool calls first:\n"
        f"{details}"
    )


def _infer_resolution_requirements(user_input: str) -> dict[str, int | bool]:
    normalized = user_input.strip()
    if not normalized:
        return {"min_skill_checks": 0, "needs_mp_change": False}

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
    return {
        "min_skill_checks": min_skill_checks,
        "needs_mp_change": needs_mp_change,
    }
