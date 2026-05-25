from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import random
import sys
from typing import Any


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "ingest_adam_arena_split.py"
)
SPEC = importlib.util.spec_from_file_location("ingest_adam_arena_split", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
ingest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingest
SPEC.loader.exec_module(ingest)


class _SequenceSampler:
    def __init__(self, targets: list[int]) -> None:
        self._targets = targets
        self._index = 0

    def next_target(self) -> int:
        target = self._targets[min(self._index, len(self._targets) - 1)]
        self._index += 1
        return target


def _line(
    text: str,
    token_count: int,
    *,
    row_uuid: str = "row-1",
    line_index: int = 0,
) -> Any:
    return ingest.SourceLine(
        row_uuid=row_uuid,
        lang="ja",
        source_row_token_count=123,
        line_index=line_index,
        text=text,
        token_count=token_count,
    )


def _write_workspace(
    workspace: Path,
    record: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    shards_dir = workspace / "shards"
    shards_dir.mkdir(parents=True)
    shard_path = shards_dir / "tasks-00000.jsonl"
    shard_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    (workspace / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )


def _manifest_for_record(config: Any) -> dict[str, Any]:
    return {
        "schema_version": ingest.MANIFEST_SCHEMA_VERSION,
        "dataset": {
            "name": config.dataset,
            "split": config.split,
            "columns": list(ingest.HF_COLUMNS),
        },
        "tokenizer": {"name": config.tokenizer},
        "source_lang": "ja",
        "target_lang": "zh",
        "packing": {
            "max_tokens": config.max_tokens,
            "center_tokens": config.center_tokens,
            "relative_sigma": config.relative_sigma,
            "seed": config.seed,
        },
        "materialization": {"tasks_written": 1},
        "shards": [{"path": "shards/tasks-00000.jsonl", "rows": 1}],
        "validation": {"status": "not_validated"},
    }


def _sample_record(config: Any) -> dict[str, Any]:
    packed = ingest.PackedTask(
        sequence=0,
        text="line one\nline two\n",
        token_count=9,
        target_tokens=10,
        lines=(
            _line("line one\n", 4, line_index=0),
            _line("line two\n", 5, line_index=1),
        ),
    )
    return ingest.build_task_record(
        packed,
        config=config,
        source_lang="ja",
        target_lang="zh",
    )


def test_pack_source_lines_preserves_whole_lines_and_spans() -> None:
    config = ingest.PackingConfig(max_tokens=20, center_tokens=7, relative_sigma=0)

    result = ingest.pack_source_lines(
        [
            _line("alpha\n", 4, line_index=0),
            _line("beta\n", 3, line_index=1),
            _line("gamma", 5, line_index=2),
        ],
        config=config,
        target_sampler=_SequenceSampler([7, 10, 10]),
    )

    assert [task.text for task in result.tasks] == ["alpha\nbeta\n", "gamma"]
    assert [task.token_count for task in result.tasks] == [7, 5]
    metadata = ingest.build_task_metadata(result.tasks[0], config=config)
    assert metadata["provenance"]["line_spans"] == [
        {
            "row_uuid": "row-1",
            "lang": "ja",
            "source_row_token_count": 123,
            "start_line": 0,
            "end_line_exclusive": 2,
            "line_count": 2,
        }
    ]


def test_pack_source_lines_enforces_max_token_cap_before_target() -> None:
    config = ingest.PackingConfig(max_tokens=10, center_tokens=100, relative_sigma=0)

    result = ingest.pack_source_lines(
        [
            _line("a\n", 6, line_index=0),
            _line("b\n", 4, line_index=1),
            _line("c\n", 3, line_index=2),
        ],
        config=config,
        target_sampler=_SequenceSampler([100, 100, 100]),
    )

    assert [task.token_count for task in result.tasks] == [10, 3]
    assert all(task.token_count <= config.max_tokens for task in result.tasks)


def test_pack_source_lines_skips_and_reports_oversized_single_line() -> None:
    config = ingest.PackingConfig(max_tokens=10, center_tokens=10, relative_sigma=0)

    result = ingest.pack_source_lines(
        [
            _line("before\n", 4, line_index=0),
            _line("too-large\n", 11, line_index=1),
            _line("after\n", 3, line_index=2),
        ],
        config=config,
        target_sampler=_SequenceSampler([10, 10, 10]),
    )

    assert [task.text for task in result.tasks] == ["before\n", "after\n"]
    assert result.skipped_oversized_lines == 1
    assert result.skipped_oversized_line_examples[0].to_manifest_example() == {
        "row_uuid": "row-1",
        "lang": "ja",
        "source_row_token_count": 123,
        "line_index": 1,
        "token_count": 11,
        "char_count": len("too-large\n"),
    }


def test_pack_source_lines_drops_whitespace_only_packs() -> None:
    config = ingest.PackingConfig(max_tokens=20, center_tokens=2, relative_sigma=0)

    result = ingest.pack_source_lines(
        [
            _line("\n", 1, line_index=0),
            _line("  \n", 1, line_index=1),
            _line("content\n", 3, line_index=2),
        ],
        config=config,
        target_sampler=_SequenceSampler([2, 10]),
    )

    assert [task.text for task in result.tasks] == ["content\n"]


def test_process_row_batch_skips_nul_containing_lines() -> None:
    class Counter:
        def count_tokens(self, texts: list[str]) -> list[int]:
            return [len(text) for text in texts]

    config = ingest.PackingConfig(max_tokens=20, center_tokens=20, relative_sigma=0)
    packer = ingest.LinePacker(
        config=config,
        target_sampler=_SequenceSampler([20]),
    )
    stats = ingest.MaterializationStats(max_tokens=config.max_tokens)
    row = ingest.SourceRow(
        row_uuid="row-1",
        text="safe\nbad\x00line\nafter\n",
        lang="ja",
        token_count=20,
    )

    ingest._process_row_batch(
        [row],
        token_counter=Counter(),
        packer=packer,
        writer=None,
        stats=stats,
        config=config,
        source_lang="ja",
        target_lang="zh",
    )
    final_task = packer.flush()
    if final_task is not None:
        ingest._emit_task(
            final_task,
            writer=None,
            stats=stats,
            config=config,
            source_lang="ja",
            target_lang="zh",
        )

    assert stats.tasks_written == 1
    assert stats.skipped_invalid_lines == 1
    assert stats.skipped_invalid_line_examples[0].to_manifest_example() == {
        "reason": "nul_byte",
        "row_uuid": "row-1",
        "lang": "ja",
        "source_row_token_count": 20,
        "line_index": 1,
        "char_count": len("bad\x00line\n"),
    }
    assert "\x00" not in final_task.text


def test_target_length_generation_is_seeded_and_clamped() -> None:
    first_rng = random.Random(42)
    second_rng = random.Random(42)

    first = [
        ingest.sample_target_tokens(
            rng=first_rng,
            center_tokens=1_000,
            relative_sigma=0.5,
            max_tokens=2_000,
        )
        for _ in range(10)
    ]
    second = [
        ingest.sample_target_tokens(
            rng=second_rng,
            center_tokens=1_000,
            relative_sigma=0.5,
            max_tokens=2_000,
        )
        for _ in range(10)
    ]

    assert first == second
    assert all(1 <= target <= 2_000 for target in first)
    assert ingest.sample_target_tokens(
        rng=random.Random(1),
        center_tokens=1_000,
        relative_sigma=10,
        max_tokens=2_000,
    ) <= 2_000


def test_validate_staged_workspace_summarizes_manifest_and_shards(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "adam_arena"
    config = ingest.PackingConfig(max_tokens=20, center_tokens=10, relative_sigma=0)
    record = _sample_record(config)
    manifest = _manifest_for_record(config)
    _write_workspace(workspace, record, manifest)

    summary = ingest.validate_staged_workspace(workspace, write_manifest=False)

    assert summary.status == "valid"
    assert summary.checked_shards == 1
    assert summary.checked_tasks == 1
    assert summary.errors == ()
    assert summary.token_distribution["count"] == 1
    assert summary.token_distribution["max"] == 9


def test_validate_task_record_rejects_manifest_mismatches_and_task_limits() -> None:
    config = ingest.PackingConfig(max_tokens=20, center_tokens=10, relative_sigma=0)
    manifest = _manifest_for_record(config)
    record = _sample_record(config)
    record["source_lang"] = "en"
    record["source_text"] = "x" * (ingest.MAX_SOURCE_TEXT_LENGTH + 1)
    record["metadata"]["packing"]["seed"] = config.seed + 1
    record["metadata"]["oversized"] = "x" * ingest.MAX_METADATA_JSON_BYTES

    errors = ingest.validate_task_record(
        record,
        manifest=manifest,
        line_label="tasks-00000.jsonl:1",
    )

    assert "tasks-00000.jsonl:1 source_lang does not match manifest" in errors
    assert any("source_text exceeds maximum length" in error for error in errors)
    assert "tasks-00000.jsonl:1 packing.seed does not match manifest" in errors
    assert any("metadata JSON exceeds maximum size" in error for error in errors)


def test_validate_task_record_rejects_nul_source_text() -> None:
    config = ingest.PackingConfig(max_tokens=20, center_tokens=10, relative_sigma=0)
    manifest = _manifest_for_record(config)
    record = _sample_record(config)
    record["source_text"] = "bad\x00text"

    errors = ingest.validate_task_record(
        record,
        manifest=manifest,
        line_label="tasks-00000.jsonl:1",
    )

    assert "tasks-00000.jsonl:1 source_text contains NUL byte" in errors


def test_insert_db_revalidates_stale_valid_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "adam_arena"
    config = ingest.PackingConfig(max_tokens=20, center_tokens=10, relative_sigma=0)
    record = _sample_record(config)
    record["target_lang"] = "en"
    manifest = _manifest_for_record(config)
    manifest["validation"] = {"status": "valid"}
    _write_workspace(workspace, record, manifest)

    args = ingest.build_parser().parse_args(
        ["insert-db", "--workspace", str(workspace), "--dry-run"]
    )

    try:
        ingest.run_insert_db(args)
    except RuntimeError as exc:
        assert "Staged workspace is not valid" in str(exc)
        assert "target_lang does not match manifest" in str(exc)
    else:
        raise AssertionError("insert-db accepted stale valid manifest")


def test_main_reports_hf_iteration_errors_without_traceback(monkeypatch: Any, capsys: Any) -> None:
    class BrokenIterable:
        def __iter__(self) -> Any:
            return self

        def __next__(self) -> Any:
            raise RuntimeError("dataset unavailable")

    monkeypatch.setattr(ingest, "_iter_hf_dataset", lambda *_args: BrokenIterable())

    exit_code = ingest.main(["dry-run", "--limit", "1"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "error: Failed to read Hugging Face dataset" in captured.err
    assert "Traceback" not in captured.err
