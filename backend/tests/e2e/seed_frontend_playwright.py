from __future__ import annotations

import os

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.models.model_registry import Model
from app.models.task import Task


def seed_frontend_playwright_fixture() -> None:
    mock_llm_base_url = os.getenv(
        "PLAYWRIGHT_MOCK_LLM_BASE_URL", "http://127.0.0.1:18080"
    )

    session = get_sessionmaker()()
    try:
        session.execute(
            text(
                """
                TRUNCATE TABLE
                    rating_events,
                    votes,
                    runs,
                    battles,
                    model_ratings,
                    tasks,
                    task_sets,
                    models
                RESTART IDENTITY CASCADE
                """
            )
        )

        task = Task(
            source_lang="ja",
            target_lang="zh",
            source_text="E2E live contract source text.",
            metadata_json={"source": "playwright-live"},
        )

        model_a = Model(
            display_name="Playwright Live Model A",
            provider_type="openai_compat",
            model_name="playwright-live-model-a",
            base_url=mock_llm_base_url,
            enabled=True,
            visibility="public",
            tags={"suite": "frontend-e2e"},
        )
        model_b = Model(
            display_name="Playwright Live Model B",
            provider_type="openai_compat",
            model_name="playwright-live-model-b",
            base_url=mock_llm_base_url,
            enabled=True,
            visibility="public",
            tags={"suite": "frontend-e2e"},
        )

        session.add_all([task, model_a, model_b])
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    seed_frontend_playwright_fixture()
