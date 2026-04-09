from __future__ import annotations

from app.core.config import Settings


def test_settings_accepts_csv_cors_origins() -> None:
    settings = Settings(
        cors_allow_origins="http://localhost:3000, https://arena.example"
    )

    assert settings.cors_allow_origins == [
        "http://localhost:3000",
        "https://arena.example",
    ]


def test_settings_accepts_json_array_cors_origins() -> None:
    settings = Settings(
        cors_allow_origins='["http://localhost:3000", "https://arena.example"]'
    )

    assert settings.cors_allow_origins == [
        "http://localhost:3000",
        "https://arena.example",
    ]
