from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import uuid

from fastapi.responses import StreamingResponse

from app.api.routes import admin_exports


class _ScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[object]:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _QueueDB:
    def __init__(self, result_sets: list[list[object]]) -> None:
        self._result_sets = [list(items) for items in result_sets]

    def execute(self, _stmt: object) -> _ScalarResult:
        assert self._result_sets, "Unexpected execute() call"
        return _ScalarResult(self._result_sets.pop(0))


async def _read_body(response: StreamingResponse) -> str:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        elif isinstance(chunk, str):
            chunks.append(chunk.encode("utf-8"))
        else:
            chunks.append(bytes(chunk))
    return b"".join(chunks).decode("utf-8")


def _jsonl_records(response: StreamingResponse) -> list[dict[str, object]]:
    body = asyncio.run(_read_body(response))
    return [json.loads(line) for line in body.splitlines() if line]


def test_normalize_recurses_over_dict_lists_and_scalar_types() -> None:
    now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    nested_id = uuid.uuid4()
    payload = {
        "items": [
            {
                "id": nested_id,
                "at": now,
            },
            "keep-me",
        ]
    }

    normalized = admin_exports._normalize(payload)

    assert normalized == {
        "items": [
            {
                "id": str(nested_id),
                "at": now.isoformat(),
            },
            "keep-me",
        ]
    }


def test_jsonl_response_sets_download_headers_and_preserves_unicode() -> None:
    response = admin_exports._jsonl_response(
        [{"greeting": "\u3053\u3093\u306b\u3061\u306f"}],
        filename="sample.jsonl",
    )

    body = asyncio.run(_read_body(response))

    assert response.media_type == "application/x-ndjson"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="sample.jsonl"'
    )
    assert "\\u3053" not in body
    assert "\u3053\u3093\u306b\u3061\u306f" in body


def test_export_tasks_serializes_schema_version_and_nested_metadata() -> None:
    created_at = datetime(2026, 2, 18, 10, 15, tzinfo=timezone.utc)
    task_id = uuid.uuid4()
    task_set_id = uuid.uuid4()
    metadata_ref = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        task_set_id=task_set_id,
        source_lang="ja",
        target_lang="zh",
        source_text="\u30c6\u30b9\u30c8",
        metadata_json={"source_ref": metadata_ref, "seen_at": created_at},
        created_at=created_at,
    )
    db = _QueueDB([[task]])

    response = admin_exports.export_tasks(db=db)  # type: ignore[arg-type]
    records = _jsonl_records(response)

    assert (
        response.headers["content-disposition"] == 'attachment; filename="tasks.jsonl"'
    )
    assert records == [
        {
            "schema_version": admin_exports.SCHEMA_VERSION,
            "record_type": "task",
            "id": str(task_id),
            "task_set_id": str(task_set_id),
            "source_lang": "ja",
            "target_lang": "zh",
            "source_text": "\u30c6\u30b9\u30c8",
            "metadata": {
                "source_ref": str(metadata_ref),
                "seen_at": created_at.isoformat(),
            },
            "created_at": created_at.isoformat(),
        }
    ]


def test_export_runs_serializes_ids_stats_and_null_errors() -> None:
    created_at = datetime(2026, 2, 18, 10, 30, tzinfo=timezone.utc)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=uuid.uuid4(),
        side="A",
        model_id=uuid.uuid4(),
        request_json={"model": "model-a"},
        prompt_rendered="prompt",
        output_text="translated",
        stats={"input_tokens": 12, "output_tokens": 18},
        error_text=None,
        created_at=created_at,
    )
    db = _QueueDB([[run]])

    response = admin_exports.export_runs(db=db)  # type: ignore[arg-type]
    records = _jsonl_records(response)

    assert (
        response.headers["content-disposition"] == 'attachment; filename="runs.jsonl"'
    )
    assert records == [
        {
            "schema_version": admin_exports.SCHEMA_VERSION,
            "record_type": "run",
            "id": str(run.id),
            "battle_id": str(run.battle_id),
            "side": "A",
            "model_id": str(run.model_id),
            "request_json": {"model": "model-a"},
            "prompt_rendered": "prompt",
            "output_text": "translated",
            "output_text_raw": None,
            "stats": {"input_tokens": 12, "output_tokens": 18},
            "error_text": None,
            "created_at": created_at.isoformat(),
        }
    ]


def test_export_battles_and_votes_capture_expected_fields() -> None:
    created_at = datetime(2026, 2, 18, 11, 0, tzinfo=timezone.utc)
    battle = SimpleNamespace(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        mode="random",
        status="completed",
        metadata_json={"task_snapshot": {"source_lang": "ja"}},
        created_at=created_at,
    )
    vote = SimpleNamespace(
        id=uuid.uuid4(),
        battle_id=battle.id,
        winner="A",
        rubric=["accuracy"],
        comment="clear winner",
        voter_user_id=None,
        voter_anon_id="anon-123",
        ip_hash="ip-hash",
        user_agent_hash="ua-hash",
        created_at=created_at,
    )

    battle_response = admin_exports.export_battles(db=_QueueDB([[battle]]))  # type: ignore[arg-type]
    vote_response = admin_exports.export_votes(db=_QueueDB([[vote]]))  # type: ignore[arg-type]

    battle_record = _jsonl_records(battle_response)[0]
    vote_record = _jsonl_records(vote_response)[0]

    assert battle_record["record_type"] == "battle"
    assert battle_record["id"] == str(battle.id)
    assert battle_record["task_id"] == str(battle.task_id)
    assert battle_record["metadata"] == {"task_snapshot": {"source_lang": "ja"}}
    assert battle_record["created_at"] == created_at.isoformat()

    assert vote_record["record_type"] == "vote"
    assert vote_record["battle_id"] == str(battle.id)
    assert vote_record["winner"] == "A"
    assert vote_record["voter_user_id"] is None
    assert vote_record["voter_anon_id"] == "anon-123"
    assert vote_record["created_at"] == created_at.isoformat()


def test_export_ratings_emits_model_ratings() -> None:
    updated_at = datetime(2026, 2, 18, 11, 15, tzinfo=timezone.utc)

    model_rating = SimpleNamespace(
        model_id=uuid.uuid4(),
        rating=1032.5,
        games_played=24,
        updated_at=updated_at,
    )
    db = _QueueDB([[model_rating]])

    response = admin_exports.export_ratings(db=db)  # type: ignore[arg-type]
    records = _jsonl_records(response)

    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="ratings.jsonl"'
    )
    assert [record["record_type"] for record in records] == [
        "model_rating",
    ]
    assert records[0] == {
        "schema_version": admin_exports.SCHEMA_VERSION,
        "record_type": "model_rating",
        "rating_method": "elo",
        "model_id": str(model_rating.model_id),
        "rating": 1032.5,
        "games_played": 24,
        "updated_at": updated_at.isoformat(),
    }


def test_stream_export_safe_after_session_close() -> None:
    """Rows are materialized before session teardown so streaming
    succeeds even when the session is closed before body iteration."""
    created_at = datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)
    task = SimpleNamespace(
        id=uuid.uuid4(),
        task_set_id=None,
        source_lang="ja",
        target_lang="zh",
        source_text="text",
        metadata_json=None,
        created_at=created_at,
    )

    class _ClosingDB:
        """DB stub that closes after execute — simulating get_db teardown."""

        def __init__(self, rows: list[object]) -> None:
            self._rows = rows
            self.closed = False

        def execute(self, _stmt: object) -> _ScalarResult:
            return _ScalarResult(self._rows)

        def close(self) -> None:
            self.closed = True

    db = _ClosingDB([task])
    response = admin_exports.export_tasks(db=db)  # type: ignore[arg-type]

    db.close()
    assert db.closed

    records = _jsonl_records(response)
    assert len(records) == 1
    assert records[0]["id"] == str(task.id)
