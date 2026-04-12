"""Focused tests for OpenAI-compatible streaming helpers."""

from __future__ import annotations

from types import SimpleNamespace

from server.llm.openai_compatible import _extract_stream_text_delta


def test_extract_stream_text_delta_reads_string_content() -> None:
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content="hello"),
            )
        ]
    )

    assert _extract_stream_text_delta(chunk) == "hello"


def test_extract_stream_text_delta_reads_fragment_list_content() -> None:
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=[
                        SimpleNamespace(text="foo"),
                        {"text": "bar"},
                        "baz",
                    ]
                ),
            )
        ]
    )

    assert _extract_stream_text_delta(chunk) == "foobarbaz"
