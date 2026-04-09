"""Pure state mutation helpers for the narrative world engine."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from server.schemas.core import GameState, MutationLog


class MutationError(Exception):
    """Base exception for mutation failures."""


class PathResolutionError(MutationError):
    """Raised when a mutation path cannot be safely resolved."""


class MutationTypeError(MutationError):
    """Raised when a mutation cannot be applied due to type mismatch."""


def apply_mutations(current_state: GameState, logs: list[MutationLog]) -> GameState:
    """Apply ordered mutation logs to a deep-copied state and return a new snapshot."""

    next_state = current_state.model_copy(deep=True)

    for log in logs:
        _apply_single_mutation(next_state, log)

    # Re-validate the full snapshot before returning it so mutator writes
    # can never leak an invalid state shape downstream.
    return GameState.model_validate(next_state.model_dump())


def _apply_single_mutation(state: GameState, log: MutationLog) -> None:
    parent, final_segment = _resolve_parent_container(state, log.target_path)

    if log.action == "add":
        _apply_add(parent, final_segment, log.value, log.target_path)
        return

    if log.action == "subtract":
        _apply_subtract(parent, final_segment, log.value, log.target_path)
        return

    if log.action == "set":
        _apply_set(parent, final_segment, log.value)
        return

    if log.action == "delete":
        _apply_delete(parent, final_segment, log.target_path)
        return

    if log.action == "append":
        _apply_append(parent, final_segment, log.value, log.target_path)
        return

    raise MutationTypeError(f"Unsupported mutation action: {log.action}")


def _resolve_parent_container(root: GameState, target_path: str) -> tuple[Any, str]:
    segments = target_path.split(".")
    current: Any = root

    for segment in segments[:-1]:
        current = _get_child(current, segment, target_path)

    return current, segments[-1]


def _get_child(container: Any, segment: str, target_path: str) -> Any:
    if isinstance(container, BaseModel):
        model_fields = container.__class__.model_fields
        if segment not in model_fields:
            raise PathResolutionError(
                f"Segment '{segment}' does not exist while resolving '{target_path}'."
            )
        return getattr(container, segment)

    if isinstance(container, dict):
        if segment not in container:
            raise PathResolutionError(
                f"Key '{segment}' does not exist while resolving '{target_path}'."
            )
        return container[segment]

    raise PathResolutionError(
        f"Cannot traverse through value of type '{type(container).__name__}' "
        f"while resolving '{target_path}'."
    )


def _read_value(container: Any, key: str, target_path: str) -> Any:
    if isinstance(container, BaseModel):
        model_fields = container.__class__.model_fields
        if key not in model_fields:
            raise PathResolutionError(f"Field '{key}' does not exist for '{target_path}'.")
        return getattr(container, key)

    if isinstance(container, dict):
        if key not in container:
            raise PathResolutionError(f"Key '{key}' does not exist for '{target_path}'.")
        return container[key]

    raise PathResolutionError(
        f"Cannot read target '{target_path}' from '{type(container).__name__}'."
    )


def _write_value(container: Any, key: str, value: Any, *, allow_new_dict_key: bool) -> None:
    if isinstance(container, BaseModel):
        model_fields = container.__class__.model_fields
        if key not in model_fields:
            raise PathResolutionError(f"Field '{key}' does not exist on model '{type(container).__name__}'.")
        setattr(container, key, value)
        return

    if isinstance(container, dict):
        if not allow_new_dict_key and key not in container:
            raise PathResolutionError(f"Key '{key}' does not exist on target dictionary.")
        container[key] = value
        return

    raise PathResolutionError(
        f"Cannot write key '{key}' on '{type(container).__name__}'."
    )


def _apply_add(container: Any, key: str, value: Any, target_path: str) -> None:
    if isinstance(container, dict) and key not in container:
        if not isinstance(value, (int, float)):
            raise MutationTypeError(
                f"Cannot initialize '{target_path}' with non-numeric add value '{value!r}'."
            )
        container[key] = value
        return

    current_value = _read_value(container, key, target_path)
    if not isinstance(current_value, (int, float)) or not isinstance(value, (int, float)):
        raise MutationTypeError(f"Add requires numeric values at '{target_path}'.")
    _write_value(container, key, current_value + value, allow_new_dict_key=False)


def _apply_subtract(container: Any, key: str, value: Any, target_path: str) -> None:
    current_value = _read_value(container, key, target_path)
    if not isinstance(current_value, (int, float)) or not isinstance(value, (int, float)):
        raise MutationTypeError(f"Subtract requires numeric values at '{target_path}'.")
    _write_value(container, key, current_value - value, allow_new_dict_key=False)


def _apply_set(container: Any, key: str, value: Any) -> None:
    allow_new_dict_key = isinstance(container, dict)
    _write_value(container, key, value, allow_new_dict_key=allow_new_dict_key)


def _apply_delete(container: Any, key: str, target_path: str) -> None:
    if not isinstance(container, dict):
        raise PathResolutionError(
            f"Delete requires a dictionary target for '{target_path}'."
        )
    if key not in container:
        raise PathResolutionError(f"Key '{key}' does not exist for '{target_path}'.")
    del container[key]


def _apply_append(container: Any, key: str, value: Any, target_path: str) -> None:
    if isinstance(container, dict) and key not in container:
        container[key] = [value]
        return

    current_value = _read_value(container, key, target_path)
    if not isinstance(current_value, list):
        raise MutationTypeError(f"Append requires a list target at '{target_path}'.")
    current_value.append(value)
