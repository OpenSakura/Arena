from __future__ import annotations

import pytest

from app.services.prompting import (
    build_chat_messages,
    normalize_optional_prompt_text,
    render_prompt_template,
)


def test_normalize_optional_prompt_text_returns_none_for_blank_values() -> None:
    assert normalize_optional_prompt_text(None) is None
    assert normalize_optional_prompt_text("") is None
    assert normalize_optional_prompt_text(" \n\t ") is None


def test_normalize_optional_prompt_text_preserves_non_blank_content() -> None:
    assert normalize_optional_prompt_text("  keep spacing  ") == "  keep spacing  "


def test_render_prompt_template_substitutes_named_tokens() -> None:
    rendered = render_prompt_template(
        "Translate {{ source_lang }} -> {{ target_lang }}: {{ text }} ({{ text }})",
        {
            "source_lang": "ja",
            "target_lang": "zh",
            "text": "sample text",
        },
    )

    assert rendered == "Translate ja -> zh: sample text (sample text)"


def test_render_prompt_template_raises_on_none_values() -> None:
    with pytest.raises(ValueError, match="Prompt input 'note' is None"):
        render_prompt_template(
            "Name={{ name }}; Note={{ note }}",
            {"name": "arena", "note": None},
        )


def test_render_prompt_template_allows_braces_in_substituted_values() -> None:
    rendered = render_prompt_template(
        "Text={{ source_text }}",
        {"source_text": "Keep literal {{brace}} markers"},
    )

    assert rendered == "Text=Keep literal {{brace}} markers"


def test_render_prompt_template_reports_missing_keys_in_sorted_order() -> None:
    with pytest.raises(
        ValueError,
        match=r"Missing prompt inputs: source_text, target_lang",
    ):
        render_prompt_template(
            "{{ source_text }} -> {{ target_lang }}",
            {},
        )


def test_render_prompt_template_rejects_unclosed_tokens() -> None:
    with pytest.raises(ValueError, match="Invalid prompt template syntax"):
        render_prompt_template(
            "{{ source_text }} {{ dangling",
            {"source_text": "x"},
        )


def test_render_prompt_template_accepts_standalone_closing_braces() -> None:
    rendered = render_prompt_template(
        "{{ source_text }} }}",
        {"source_text": "x"},
    )
    assert rendered == "x }}"


def test_render_prompt_template_allows_literal_closing_braces_in_template() -> None:
    rendered = render_prompt_template(
        'Format: {"key": "value"}} after {{ source_text }}',
        {"source_text": "hello"},
    )
    assert rendered == 'Format: {"key": "value"}} after hello'


def test_render_prompt_template_still_rejects_malformed_opening_braces() -> None:
    with pytest.raises(ValueError, match="Invalid prompt template syntax"):
        render_prompt_template(
            "{{ source_text }} {{ dangling",
            {"source_text": "x"},
        )


def test_build_chat_messages_includes_system_prompt_when_present() -> None:
    messages = build_chat_messages(
        system_prompt="Be concise", user_prompt="Translate this"
    )

    assert messages == [
        {"role": "system", "content": "Be concise"},
        {"role": "user", "content": "Translate this"},
    ]


def test_build_chat_messages_omits_system_prompt_when_empty() -> None:
    messages = build_chat_messages(system_prompt="", user_prompt="Translate this")

    assert messages == [{"role": "user", "content": "Translate this"}]
