from __future__ import annotations

from app.utils.usage import normalize_usage


def test_normalize_usage_from_openai_fields() -> None:
    usage = {"prompt_tokens": 12, "completion_tokens": 34}
    normalized = normalize_usage(usage)

    assert normalized is not None
    assert normalized["input_tokens"] == 12
    assert normalized["output_tokens"] == 34
    assert normalized["total_tokens"] == 46


def test_normalize_usage_keeps_existing_total() -> None:
    usage = {
        "input_tokens": 5,
        "output_tokens": 9,
        "total_tokens": 99,
    }
    normalized = normalize_usage(usage)

    assert normalized is not None
    assert normalized["input_tokens"] == 5
    assert normalized["output_tokens"] == 9
    assert normalized["total_tokens"] == 99


def test_normalize_usage_handles_empty() -> None:
    assert normalize_usage(None) is None
    assert normalize_usage({}) == {}
