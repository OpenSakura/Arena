#!/usr/bin/env python3
"""Seed the model registry with the OpenSakura LLM gateway catalog.

Run from ``backend/`` so that ``.env`` (ARENA_MASTER_KEY, DATABASE_URL) is loaded::

    .venv/bin/python scripts/seed_model_registry.py

Models that already exist (matched by ``model_name``) are skipped.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.db.session import get_sessionmaker
from app.models.model_registry import Model


BASE_URL = "https://llm.opensakura.com/v1"
API_KEY = "sk-OqaNzvRdsMMeVkAxlVH4r04RmqTXMTDOBMmDzBpZl7rmU0HI"
TEMPERATURE = 0.6


# Main OpenAI reasoning models — the gateway encodes reasoning effort via a
# ``-<effort>`` suffix on the model name.  Mini/nano variants are kept as a
# single entry per the seeding spec.
GPT_REASONING_EFFORTS: dict[str, list[str]] = {
    "gpt-5": ["minimal", "low", "medium", "high"],
    "gpt-5.1": ["minimal", "low", "medium", "high"],
    "gpt-5.2": ["minimal", "low", "medium", "high", "xhigh"],
    "gpt-5.4": ["minimal", "low", "medium", "high", "xhigh"],
    "gpt-5.5": ["minimal", "low", "medium", "high", "xhigh"],
}


MODEL_IDENTIFIERS: list[str] = [
    "anthropic/claude-3.5-haiku",
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-opus-4",
    "anthropic/claude-opus-4.1",
    "anthropic/claude-opus-4.5",
    "anthropic/claude-opus-4.6",
    "anthropic/claude-opus-4.7",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-sonnet-4.6",
    "baidu/ernie-5.0-thinking-preview",
    "baidu/ernie-5.1",
    "bytedance/doubao-seed-1.8",
    "bytedance/doubao-seed-2.0-lite",
    "bytedance/doubao-seed-2.0-mini",
    "bytedance/doubao-seed-2.0-pro",
    "deepseek/deepseek-chat-v3.1",
    "deepseek/deepseek-r1-0528",
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-v3.2-exp",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-pro",
    "google/gemini-3-flash-preview",
    "google/gemini-3.1-flash-lite",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.5-flash",
    "google/gemma-3-12b-it",
    "inclusionai/ring-1t",
    "inclusionai/ring-2.6-1t",
    "meta/llama-3.3-70b-instruct",
    "meta/llama-4-scout-17b-16e-instruct",
    "minimax/minimax-m2",
    "minimax/minimax-m2.1",
    "minimax/minimax-m2.5",
    "minimax/minimax-m2.7",
    "mistralai/mistral-large-2512",
    "moonshotai/kimi-k2-0711",
    "moonshotai/kimi-k2-0905",
    "moonshotai/kimi-k2-thinking",
    "moonshotai/kimi-k2.5",
    "moonshotai/kimi-k2.6",
    "openai/gpt-4.1",
    "openai/gpt-4.1-mini",
    "openai/gpt-4.1-nano",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-5",
    "openai/gpt-5-mini",
    "openai/gpt-5-nano",
    "openai/gpt-5.1",
    "openai/gpt-5.2",
    "openai/gpt-5.4",
    "openai/gpt-5.4-mini",
    "openai/gpt-5.4-nano",
    "openai/gpt-5.5",
    "qwen/qwen3-14b",
    "qwen/qwen3-235b-a22b-2507",
    "qwen/qwen3-235b-a22b-thinking-2507",
    "qwen/qwen3.5-flash",
    "qwen/qwen3.5-plus",
    "qwen/qwen3.6-flash",
    "qwen/qwen3.6-plus",
    "qwen/qwen3.7-max",
    "stepfun/step-3",
    "stepfun/step-3.5-flash",
    "tencent/hunyuan-2.0-thinking",
    "tencent/hy3-preview",
    "x-ai/grok-4.2-fast",
    "x-ai/grok-4.2-fast-non-reasoning",
    "x-ai/grok-4.3",
    "xiaomi/mimo-v2-flash",
    "xiaomi/mimo-v2-pro",
    "xiaomi/mimo-v2.5",
    "xiaomi/mimo-v2.5-pro",
    "z-ai/glm-4.5",
    "z-ai/glm-4.5-air",
    "z-ai/glm-4.6",
    "z-ai/glm-4.7",
    "z-ai/glm-5",
    "z-ai/glm-5-turbo",
    "z-ai/glm-5.1",
]


def expand(
    identifier: str,
) -> list[tuple[str, str, dict[str, dict[str, str]] | None]]:
    """Return ``(display_name, model_name, params)`` tuples for an identifier.

    For main OpenAI reasoning models, expand into one entry per reasoning
    effort level and set the gateway's ``reasoning: {effort: ...}`` object
    in ``params`` (OpenRouter-style; the top-level ``reasoning_effort``
    field is deprecated).  Everything else is a single entry with no extra
    params, and the provider stripped from ``model_name`` but retained in
    ``display_name``.
    """

    provider, sep, suffix = identifier.partition("/")
    if not sep:
        return [(identifier, identifier, None)]

    model_name = suffix
    efforts = GPT_REASONING_EFFORTS.get(model_name)
    if efforts is None:
        return [(identifier, model_name, None)]

    return [
        (
            f"{provider}/{model_name}-{effort}",
            f"{model_name}-{effort}",
            {"reasoning": {"effort": effort}},
        )
        for effort in efforts
    ]


def main() -> None:
    encrypted_key = encrypt_secret(API_KEY)
    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    inserted = 0
    updated = 0
    skipped = 0
    try:
        existing_models = {
            m.model_name: m
            for m in session.execute(select(Model)).scalars().all()
        }
        for identifier in MODEL_IDENTIFIERS:
            for display_name, model_name, params in expand(identifier):
                existing = existing_models.get(model_name)
                if existing is None:
                    session.add(
                        Model(
                            display_name=display_name,
                            model_name=model_name,
                            base_url=BASE_URL,
                            enabled=True,
                            visibility="public",
                            temperature=TEMPERATURE,
                            params=params,
                            encrypted_api_key=encrypted_key,
                        )
                    )
                    inserted += 1
                    continue

                # Only sync ``params`` for reasoning variants — leave any
                # admin-set params on other models untouched.
                if params is not None and existing.params != params:
                    existing.params = params
                    updated += 1
                else:
                    skipped += 1
        session.commit()
    finally:
        session.close()

    print(
        f"Inserted {inserted}, updated {updated}, "
        f"skipped {skipped} (already correct or non-reasoning)"
    )


if __name__ == "__main__":
    main()
