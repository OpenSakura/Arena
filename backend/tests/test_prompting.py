from __future__ import annotations

import pytest

from app.services.prompting import build_chat_messages, render_prompt_template


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


def test_render_prompt_template_rejects_unmatched_closing_braces() -> None:
    with pytest.raises(ValueError, match="Invalid prompt template syntax"):
        render_prompt_template(
            "{{ source_text }} }}",
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
