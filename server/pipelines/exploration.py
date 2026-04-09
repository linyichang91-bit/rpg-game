"""Dynamic exploration and just-in-time topology stitching."""

from __future__ import annotations

from typing import Any

from server.generators.map_generator import DynamicMapGenerator
from server.schemas.core import ExecutedEvent, GameState, MutationLog


DEFAULT_ACTION = "travel"


def resolve_exploration(
    current_state: GameState,
    parameters: dict[str, Any],
    *,
    map_generator: DynamicMapGenerator,
    target_node_id: str,
    target_name: str,
) -> tuple[list[MutationLog], ExecutedEvent]:
    """Resolve movement, generating and stitching new nodes when needed."""

    current_node_id = current_state.current_location_id
    topology = current_state.world_config.topology
    action_type = str(parameters.get("action_type", DEFAULT_ACTION))

    if target_node_id in topology.nodes:
        if target_node_id in topology.edges.get(current_node_id, []):
            logs = [
                MutationLog(
                    action="set",
                    target_path="current_location_id",
                    value=target_node_id,
                    reason="exploration_travel",
                )
            ]
            return logs, ExecutedEvent(
                event_type="exploration",
                is_success=True,
                actor="player",
                target=topology.nodes[target_node_id].title,
                abstract_action=action_type,
                result_tags=["travel_success", target_node_id],
            )

        return [], ExecutedEvent(
            event_type="exploration",
            is_success=False,
            actor="player",
            target=topology.nodes[target_node_id].title,
            abstract_action=action_type,
            result_tags=["path_not_connected", target_node_id],
        )

    new_node = map_generator.generate_node(
        current_state,
        current_node_id=current_node_id,
        target_node_id=target_node_id,
        target_name=target_name,
    )
    logs = [
        MutationLog(
            action="set",
            target_path=f"world_config.topology.nodes.{target_node_id}",
            value=new_node,
            reason="exploration_register_node",
        ),
        MutationLog(
            action="append",
            target_path=f"world_config.topology.edges.{current_node_id}",
            value=target_node_id,
            reason="exploration_stitch_forward",
        ),
        MutationLog(
            action="append",
            target_path=f"world_config.topology.edges.{target_node_id}",
            value=current_node_id,
            reason="exploration_stitch_backward",
        ),
        MutationLog(
            action="set",
            target_path="current_location_id",
            value=target_node_id,
            reason="exploration_move_player",
        ),
    ]
    return logs, ExecutedEvent(
        event_type="exploration",
        is_success=True,
        actor="player",
        target=new_node.title,
        abstract_action="discovery",
        result_tags=["new_location_discovered", target_node_id],
    )
