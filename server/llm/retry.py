"""Shared retry helpers for structured JSON generation pipelines."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_none


T = TypeVar("T")


def run_retryable_json_operation(
    operation: Callable[[], T],
    *,
    max_attempts: int,
    retryable_exceptions: type[Exception] | tuple[type[Exception], ...],
) -> T:
    """Run a JSON generation operation with retry semantics and no backoff delay."""

    for attempt in Retrying(
        stop=stop_after_attempt(max(1, max_attempts)),
        wait=wait_none(),
        retry=retry_if_exception_type(retryable_exceptions),
        reraise=True,
    ):
        with attempt:
            return operation()

    raise RuntimeError("JSON retry loop exited without returning a result.")
