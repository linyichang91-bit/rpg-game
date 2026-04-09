"""Helpers for extracting valid JSON payloads from imperfect LLM responses."""

from __future__ import annotations

import re


_CODE_FENCE_PREFIX = re.compile(r"^```[a-zA-Z0-9_-]*\s*")
_CODE_FENCE_SUFFIX = re.compile(r"\s*```$")
_OPEN_TO_CLOSE = {"{": "}", "[": "]"}


def normalize_json_payload(payload: str) -> str:
    """Strip markdown fences and isolate the first JSON object or array."""

    normalized = payload.strip()
    if not normalized:
        return normalized

    if normalized.startswith("```"):
        normalized = _CODE_FENCE_PREFIX.sub("", normalized, count=1)
        normalized = _CODE_FENCE_SUFFIX.sub("", normalized, count=1).strip()

    extracted = _extract_first_json_container(normalized)
    return extracted.strip()


def _extract_first_json_container(payload: str) -> str:
    start_index = _find_first_json_start(payload)
    if start_index is None:
        return payload

    stack: list[str] = []
    in_string = False
    is_escaping = False

    for index in range(start_index, len(payload)):
        char = payload[index]

        if in_string:
            if is_escaping:
                is_escaping = False
            elif char == "\\":
                is_escaping = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[char])
            continue

        if char in ("}", "]"):
            if not stack or char != stack[-1]:
                return payload

            stack.pop()
            if not stack:
                return payload[start_index : index + 1]

    return payload


def _find_first_json_start(payload: str) -> int | None:
    candidates = [
        index
        for index in (payload.find("{"), payload.find("["))
        if index >= 0
    ]
    if not candidates:
        return None
    return min(candidates)
