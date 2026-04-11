"""app.services.prompting

Prompt rendering and normalization.

Notes:
- Prompt templates should be versioned (DB) and rendered deterministically.
- The rendered prompt/messages must be stored on each Run for reproducibility.
"""

from __future__ import annotations

import re
from typing import Any


_TOKEN_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_prompt_template(template_text: str, inputs: dict[str, Any]) -> str:
    """Render a prompt template into plain text.

    Supported placeholder syntax is `{{ variable_name }}`.
    This renderer intentionally supports only direct variable substitution.
    """

    required_keys = set(_TOKEN_PATTERN.findall(template_text))
    missing_keys = sorted(key for key in required_keys if key not in inputs)
    if missing_keys:
        raise ValueError(f"Missing prompt inputs: {', '.join(missing_keys)}")

    # Validate template syntax before substitution so user content can safely
    # include literal ``{{...}}`` without being treated as malformed template
    # syntax.
    template_without_tokens = _TOKEN_PATTERN.sub("", template_text)
    if "{{" in template_without_tokens:
        raise ValueError("Invalid prompt template syntax")

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = inputs[key]
        if value is None:
            raise ValueError(f"Prompt input '{key}' is None")
        return str(value)

    rendered = _TOKEN_PATTERN.sub(replace, template_text)

    return rendered


def build_chat_messages(
    *, system_prompt: str | None, user_prompt: str
) -> list[dict[str, str]]:
    """Build OpenAI-compatible chat messages payload."""

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return messages
