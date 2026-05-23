from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.bot import BotBattleCreateAndWaitRequest
from app.schemas.service_accounts import (
    ServiceAccountPublic,
    ServiceAccountTokenCreate,
    ServiceAccountTokenCreateResponse,
    ServiceAccountTokenRedacted,
)
from app.schemas.votes import VoteCreate
from app.utils.json_limits import validate_bounded_json_object


def _oversize_payload() -> dict[str, str]:
    return {f"key_{index}": "x" * 300 for index in range(64)}


def _depth_five_payload() -> dict[str, object]:
    return {"a": {"b": {"c": {"d": {}}}}}


def test_bounded_json_accepts_empty_and_arbitrary_object_keys() -> None:
    sample = {"external_run_id": "run-001", "score": 0.87}

    assert validate_bounded_json_object({}) == {}
    assert validate_bounded_json_object(sample) == sample
    assert VoteCreate(winner="A", bot_metadata=sample).bot_metadata == sample


def test_vote_create_bot_metadata_is_optional() -> None:
    payload = VoteCreate(winner="tie")

    assert payload.bot_metadata is None


def test_vote_create_bot_metadata_validation_error_is_field_scoped() -> None:
    with pytest.raises(ValidationError) as exc_info:
        VoteCreate.model_validate({"winner": "A", "bot_metadata": []})

    errors = exc_info.value.errors()
    assert errors[0]["loc"] == ("bot_metadata",)
    assert errors[0]["type"] == "value_error"


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        "not an object",
        _oversize_payload(),
        _depth_five_payload(),
        {f"key_{index}": index for index in range(65)},
        {"x" * 129: True},
        {"text": "x" * 4097},
    ],
)
def test_vote_create_rejects_invalid_bot_metadata_as_validation_error(
    metadata: object,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        VoteCreate.model_validate({"winner": "A", "bot_metadata": metadata})

    assert exc_info.value.errors()[0]["loc"] == ("bot_metadata",)


def test_bounded_json_rejects_root_array() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        validate_bounded_json_object([])


def test_bounded_json_rejects_root_string() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        validate_bounded_json_object("not an object")


def test_bounded_json_rejects_serialized_payload_over_16_kib() -> None:
    payload = _oversize_payload()

    with pytest.raises(ValueError, match="16384 bytes"):
        validate_bounded_json_object(payload)


def test_bounded_json_rejects_depth_five() -> None:
    payload = _depth_five_payload()

    with pytest.raises(ValueError, match="depth"):
        validate_bounded_json_object(payload)


def test_bounded_json_rejects_65_keys() -> None:
    payload = {f"key_{index}": index for index in range(65)}

    with pytest.raises(ValueError, match="64 keys"):
        validate_bounded_json_object(payload)


def test_bounded_json_rejects_129_character_key() -> None:
    payload = {"x" * 129: True}

    with pytest.raises(ValueError, match="128 characters"):
        validate_bounded_json_object(payload)


def test_bounded_json_rejects_4097_character_string() -> None:
    payload = {"text": "x" * 4097}

    with pytest.raises(ValueError, match="4096 characters"):
        validate_bounded_json_object(payload)


def test_service_account_token_plaintext_only_exists_on_create_response() -> None:
    service_account = ServiceAccountPublic(
        id="service-account-1",
        name="Judge Bot",
        enabled=True,
        scopes=["battle:create", "vote:create"],
        created_at="2026-05-23T00:00:00Z",
        updated_at="2026-05-23T00:00:00Z",
    )
    redacted_token = ServiceAccountTokenRedacted(
        id="token-1",
        service_account_id="service-account-1",
        token_prefix="osa_bot_1234",
        scopes=["vote:create"],
        created_at="2026-05-23T00:00:00Z",
    )
    response = ServiceAccountTokenCreateResponse(
        service_account=service_account,
        token=redacted_token,
        plaintext_token="osa_bot_secret",
    )

    assert "plaintext_token" not in ServiceAccountTokenRedacted.model_fields
    assert "plaintext_token" in ServiceAccountTokenCreateResponse.model_fields
    assert response.plaintext_token == "osa_bot_secret"


def test_service_account_token_create_normalizes_and_rejects_scopes() -> None:
    payload = ServiceAccountTokenCreate(
        scopes=["battle:create", "battle:create", "vote:create"]
    )

    assert payload.scopes == ["battle:create", "vote:create"]

    with pytest.raises(ValidationError):
        ServiceAccountTokenCreate(scopes=["admin"])


def test_bot_battle_create_and_wait_request_timeout_contract() -> None:
    payload = BotBattleCreateAndWaitRequest()

    assert payload.timeout_seconds == 60
    assert "bot_metadata" not in BotBattleCreateAndWaitRequest.model_fields

    with pytest.raises(ValidationError):
        BotBattleCreateAndWaitRequest(timeout_seconds=121)


def test_bot_battle_create_and_wait_request_rejects_metadata_extra() -> None:
    with pytest.raises(ValidationError) as exc_info:
        BotBattleCreateAndWaitRequest.model_validate(
            {"timeout_seconds": 60, "bot_metadata": {"external_run_id": "run-001"}}
        )

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("bot_metadata",)
    assert error["type"] == "extra_forbidden"
