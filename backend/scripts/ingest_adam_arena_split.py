#!/usr/bin/env python3
"""Stage the Hugging Face Adam arena split for OpenSakura tasks.

Examples, run from ``backend/``:

  uv run python scripts/ingest_adam_arena_split.py dry-run --limit 100
  uv run python scripts/ingest_adam_arena_split.py materialize --limit 1000
  uv run python scripts/ingest_adam_arena_split.py validate
  uv run python scripts/ingest_adam_arena_split.py insert-db

``dry-run`` and ``materialize`` stream the Hugging Face split and tokenize bounded
batches. Only ``insert-db`` writes to the local database.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import sys
from typing import Any, Protocol


DEFAULT_DATASET = "OpenSakura/OpenSakura-DS-260220-LN-ja-zh-PT-Adam"
DEFAULT_SPLIT = "arena"
DEFAULT_TOKENIZER = "Qwen/Qwen3.5-397B-A17B"
DEFAULT_WORKSPACE = Path("../var/arena_import/adam_arena")
DEFAULT_SOURCE_LANG = "ja"
DEFAULT_TARGET_LANG = "zh"
DEFAULT_MAX_TOKENS = 2_000
DEFAULT_CENTER_TOKENS = 1_000
DEFAULT_RELATIVE_SIGMA = 0.5
DEFAULT_SEED = 260_220
DEFAULT_SHARD_ROWS = 10_000
DEFAULT_ROW_BATCH_SIZE = 32
DEFAULT_TOKEN_BATCH_SIZE = 256
DEFAULT_INSERT_BATCH_SIZE = 1_000
DEFAULT_DRY_RUN_LIMIT = 100
DEFAULT_TASK_SET_NAME = "adam-arena-ja-to-zh-qwen3.5-397b-a17b"
MAX_SOURCE_TEXT_LENGTH = 131_072
MAX_METADATA_JSON_BYTES = 65_536
HF_COLUMNS = ("uuid", "text", "lang", "token_count")
MANIFEST_FILENAME = "manifest.json"
SHARDS_DIRNAME = "shards"
MANIFEST_SCHEMA_VERSION = 1
MAX_SKIP_EXAMPLES = 20
MAX_VALIDATION_ERRORS = 100


JsonObject = dict[str, Any]


class TargetSampler(Protocol):
    def next_target(self) -> int:
        ...


class BatchTokenCounter(Protocol):
    def count_tokens(self, texts: Sequence[str]) -> list[int]:
        ...


@dataclass(frozen=True)
class PackingConfig:
    dataset: str = DEFAULT_DATASET
    split: str = DEFAULT_SPLIT
    tokenizer: str = DEFAULT_TOKENIZER
    max_tokens: int = DEFAULT_MAX_TOKENS
    center_tokens: int = DEFAULT_CENTER_TOKENS
    relative_sigma: float = DEFAULT_RELATIVE_SIGMA
    seed: int = DEFAULT_SEED


@dataclass(frozen=True)
class SourceRow:
    row_uuid: str
    text: str
    lang: str
    token_count: int | None


@dataclass(frozen=True)
class SourceLine:
    row_uuid: str
    lang: str
    source_row_token_count: int | None
    line_index: int
    text: str
    token_count: int


@dataclass(frozen=True)
class SkippedOversizedLine:
    row_uuid: str
    lang: str
    source_row_token_count: int | None
    line_index: int
    token_count: int
    char_count: int

    def to_manifest_example(self) -> JsonObject:
        return {
            "row_uuid": self.row_uuid,
            "lang": self.lang,
            "source_row_token_count": self.source_row_token_count,
            "line_index": self.line_index,
            "token_count": self.token_count,
            "char_count": self.char_count,
        }


@dataclass(frozen=True)
class SkippedInvalidLine:
    reason: str
    row_uuid: str
    lang: str
    source_row_token_count: int | None
    line_index: int
    char_count: int

    def to_manifest_example(self) -> JsonObject:
        return {
            "reason": self.reason,
            "row_uuid": self.row_uuid,
            "lang": self.lang,
            "source_row_token_count": self.source_row_token_count,
            "line_index": self.line_index,
            "char_count": self.char_count,
        }


@dataclass(frozen=True)
class PackedTask:
    sequence: int
    text: str
    token_count: int
    target_tokens: int
    lines: tuple[SourceLine, ...]


@dataclass(frozen=True)
class PackingResult:
    tasks: tuple[PackedTask, ...]
    skipped_oversized_lines: int
    skipped_oversized_line_examples: tuple[SkippedOversizedLine, ...]


@dataclass
class TokenDistribution:
    max_tokens: int
    count: int = 0
    total: int = 0
    minimum: int | None = None
    maximum: int | None = None
    _histogram: list[int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        self._histogram = [0] * (self.max_tokens + 1)

    def add(self, token_count: int) -> None:
        if token_count < 0:
            raise ValueError("token_count must not be negative")
        bucket = min(token_count, self.max_tokens)
        self._histogram[bucket] += 1
        self.count += 1
        self.total += token_count
        self.minimum = token_count if self.minimum is None else min(self.minimum, token_count)
        self.maximum = token_count if self.maximum is None else max(self.maximum, token_count)

    def summary(self) -> JsonObject:
        if self.count == 0:
            return {
                "count": 0,
                "min": None,
                "max": None,
                "mean": None,
                "p50": None,
                "p90": None,
                "p95": None,
            }
        return {
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": round(self.total / self.count, 2),
            "p50": self._percentile(0.50),
            "p90": self._percentile(0.90),
            "p95": self._percentile(0.95),
        }

    def _percentile(self, fraction: float) -> int:
        target_rank = max(1, int(round(self.count * fraction)))
        cumulative = 0
        for token_count, bucket_count in enumerate(self._histogram):
            cumulative += bucket_count
            if cumulative >= target_rank:
                return token_count
        return self.max_tokens


@dataclass
class MaterializationStats:
    max_tokens: int
    source_rows_seen: int = 0
    source_rows_accepted: int = 0
    source_rows_filtered: int = 0
    source_lines_seen: int = 0
    skipped_invalid_lines: int = 0
    skipped_invalid_line_examples: list[SkippedInvalidLine] = field(default_factory=list)
    tasks_written: int = 0
    token_distribution: TokenDistribution = field(init=False)

    def __post_init__(self) -> None:
        self.token_distribution = TokenDistribution(max_tokens=self.max_tokens)

    def record_task(self, task: PackedTask) -> None:
        self.tasks_written += 1
        self.token_distribution.add(task.token_count)

    def record_invalid_line(self, skipped: SkippedInvalidLine) -> None:
        self.skipped_invalid_lines += 1
        if len(self.skipped_invalid_line_examples) < MAX_SKIP_EXAMPLES:
            self.skipped_invalid_line_examples.append(skipped)

    def to_manifest_counts(
        self,
        *,
        skipped_oversized_lines: int,
        skipped_examples: Sequence[SkippedOversizedLine],
    ) -> JsonObject:
        return {
            "source_rows_seen": self.source_rows_seen,
            "source_rows_accepted": self.source_rows_accepted,
            "source_rows_filtered": self.source_rows_filtered,
            "source_lines_seen": self.source_lines_seen,
            "skipped_invalid_lines": self.skipped_invalid_lines,
            "skipped_invalid_line_examples": [
                item.to_manifest_example() for item in self.skipped_invalid_line_examples
            ],
            "tasks_written": self.tasks_written,
            "skipped_oversized_lines": skipped_oversized_lines,
            "skipped_oversized_line_examples": [
                item.to_manifest_example() for item in skipped_examples
            ],
            "token_distribution": self.token_distribution.summary(),
        }


@dataclass(frozen=True)
class ValidationSummary:
    status: str
    checked_shards: int
    checked_tasks: int
    errors: tuple[str, ...]
    token_distribution: JsonObject

    def to_manifest_status(self) -> JsonObject:
        return {
            "status": self.status,
            "validated_at": _utc_now(),
            "checked_shards": self.checked_shards,
            "checked_tasks": self.checked_tasks,
            "errors": list(self.errors),
            "token_distribution": self.token_distribution,
        }


class NormalTargetTokenGenerator:
    def __init__(
        self,
        *,
        center_tokens: int,
        relative_sigma: float,
        max_tokens: int,
        seed: int,
    ) -> None:
        self._center_tokens = center_tokens
        self._relative_sigma = relative_sigma
        self._max_tokens = max_tokens
        self._rng = random.Random(seed)

    def next_target(self) -> int:
        return sample_target_tokens(
            rng=self._rng,
            center_tokens=self._center_tokens,
            relative_sigma=self._relative_sigma,
            max_tokens=self._max_tokens,
        )


class LinePacker:
    def __init__(self, *, config: PackingConfig, target_sampler: TargetSampler) -> None:
        _validate_packing_config(config)
        self._config = config
        self._target_sampler = target_sampler
        self._current_lines: list[SourceLine] = []
        self._current_tokens = 0
        self._current_target = self._target_sampler.next_target()
        self._next_sequence = 0
        self._skipped_oversized_lines = 0
        self._skipped_examples: list[SkippedOversizedLine] = []

    @property
    def skipped_oversized_lines(self) -> int:
        return self._skipped_oversized_lines

    @property
    def skipped_oversized_line_examples(self) -> tuple[SkippedOversizedLine, ...]:
        return tuple(self._skipped_examples)

    def add_line(self, line: SourceLine) -> list[PackedTask]:
        if line.token_count < 0:
            raise ValueError("line token_count must not be negative")

        emitted: list[PackedTask] = []
        if line.token_count > self._config.max_tokens:
            flushed = self.flush()
            if flushed is not None:
                emitted.append(flushed)
            self._record_oversized_line(line)
            return emitted

        if self._current_lines and self._would_exceed_current_pack(line):
            flushed = self.flush()
            if flushed is not None:
                emitted.append(flushed)

        self._current_lines.append(line)
        self._current_tokens += line.token_count

        if self._current_tokens >= self._current_target:
            flushed = self.flush()
            if flushed is not None:
                emitted.append(flushed)

        return emitted

    def flush(self) -> PackedTask | None:
        if not self._current_lines:
            return None

        text = "".join(line.text for line in self._current_lines)
        if not text.strip():
            self._current_lines = []
            self._current_tokens = 0
            self._current_target = self._target_sampler.next_target()
            return None

        task = PackedTask(
            sequence=self._next_sequence,
            text=text,
            token_count=self._current_tokens,
            target_tokens=self._current_target,
            lines=tuple(self._current_lines),
        )
        self._next_sequence += 1
        self._current_lines = []
        self._current_tokens = 0
        self._current_target = self._target_sampler.next_target()
        return task

    def _would_exceed_current_pack(self, line: SourceLine) -> bool:
        next_total = self._current_tokens + line.token_count
        return (
            next_total > self._current_target
            or next_total > self._config.max_tokens
        )

    def _record_oversized_line(self, line: SourceLine) -> None:
        self._skipped_oversized_lines += 1
        if len(self._skipped_examples) >= MAX_SKIP_EXAMPLES:
            return
        self._skipped_examples.append(
            SkippedOversizedLine(
                row_uuid=line.row_uuid,
                lang=line.lang,
                source_row_token_count=line.source_row_token_count,
                line_index=line.line_index,
                token_count=line.token_count,
                char_count=len(line.text),
            )
        )


class HuggingFaceBatchTokenCounter:
    def __init__(
        self,
        *,
        tokenizer_name: str,
        batch_size: int,
        workers: int,
        trust_remote_code: bool,
    ) -> None:
        if batch_size < 1:
            raise ValueError("token batch size must be at least 1")
        if workers < 1:
            raise ValueError("workers must be at least 1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Missing optional dependency 'transformers'. Install it in the "
                "backend environment before dry-run or materialize."
            ) from exc

        self._tokenizer = None
        if workers == 1:
            self._tokenizer = AutoTokenizer.from_pretrained(
                tokenizer_name,
                use_fast=True,
                trust_remote_code=trust_remote_code,
            )
        self._tokenizer_name = tokenizer_name
        self._trust_remote_code = trust_remote_code
        self._batch_size = batch_size
        self._workers = workers
        self._executor = (
            ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_process_tokenizer,
                initargs=(tokenizer_name, trust_remote_code),
            )
            if workers > 1
            else None
        )

    def count_tokens(self, texts: Sequence[str]) -> list[int]:
        text_list = list(texts)
        if not text_list:
            return []

        chunks = [
            text_list[index : index + self._batch_size]
            for index in range(0, len(text_list), self._batch_size)
        ]
        if self._workers == 1:
            counts: list[int] = []
            for chunk in chunks:
                counts.extend(self._count_chunk(chunk))
            return counts

        if self._executor is None:
            raise RuntimeError("tokenizer process pool was not initialized")

        counts = []
        for chunk_counts in self._executor.map(_count_process_chunk, chunks):
            counts.extend(chunk_counts)
        return counts

    def _count_chunk(self, texts: Sequence[str]) -> list[int]:
        if self._tokenizer is None:
            raise RuntimeError("tokenizer is only loaded in-process when workers=1")
        encoded = self._tokenizer(
            list(texts),
            add_special_tokens=False,
            return_attention_mask=False,
            return_token_type_ids=False,
        )
        input_ids = encoded["input_ids"]
        return [len(item) for item in input_ids]

    def close(self) -> None:
        self._tokenizer = None
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None


_PROCESS_TOKENIZER: Any | None = None


def _init_process_tokenizer(tokenizer_name: str, trust_remote_code: bool) -> None:
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Missing optional dependency 'transformers'. Install it in the backend "
            "environment before dry-run or materialize."
        ) from exc
    global _PROCESS_TOKENIZER
    _PROCESS_TOKENIZER = AutoTokenizer.from_pretrained(
        tokenizer_name,
        use_fast=True,
        trust_remote_code=trust_remote_code,
    )


def _count_process_chunk(texts: Sequence[str]) -> list[int]:
    if _PROCESS_TOKENIZER is None:
        raise RuntimeError("process tokenizer was not initialized")
    encoded = _PROCESS_TOKENIZER(
        list(texts),
        add_special_tokens=False,
        return_attention_mask=False,
        return_token_type_ids=False,
    )
    input_ids = encoded["input_ids"]
    return [len(item) for item in input_ids]


class JsonlShardWriter:
    def __init__(self, *, workspace: Path, shard_rows: int) -> None:
        if shard_rows < 1:
            raise ValueError("shard_rows must be at least 1")
        self._workspace = workspace
        self._shards_dir = workspace / SHARDS_DIRNAME
        self._shard_rows = shard_rows
        self._shard_index = 0
        self._current_rows = 0
        self._current_file: Any | None = None
        self._current_tmp_path: Path | None = None
        self._current_final_path: Path | None = None
        self._shards: list[JsonObject] = []

    @property
    def shards(self) -> list[JsonObject]:
        return list(self._shards)

    def write(self, record: JsonObject) -> None:
        if self._current_file is None:
            self._open_next_shard()
        assert self._current_file is not None
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        self._current_file.write(line)
        self._current_file.write("\n")
        self._current_rows += 1
        if self._current_rows >= self._shard_rows:
            self.close_current_shard()

    def close_current_shard(self) -> None:
        if self._current_file is None:
            return
        assert self._current_tmp_path is not None
        assert self._current_final_path is not None
        self._current_file.close()
        self._current_file = None
        self._current_tmp_path.replace(self._current_final_path)
        self._shards.append(
            {
                "path": self._current_final_path.relative_to(self._workspace).as_posix(),
                "rows": self._current_rows,
                "bytes": self._current_final_path.stat().st_size,
            }
        )
        self._current_rows = 0
        self._current_tmp_path = None
        self._current_final_path = None

    def _open_next_shard(self) -> None:
        self._shards_dir.mkdir(parents=True, exist_ok=True)
        final_path = self._shards_dir / f"tasks-{self._shard_index:05d}.jsonl"
        tmp_path = final_path.with_name(f".{final_path.name}.{os.getpid()}.tmp")
        self._shard_index += 1
        self._current_tmp_path = tmp_path
        self._current_final_path = final_path
        self._current_file = tmp_path.open("w", encoding="utf-8")


def sample_target_tokens(
    *,
    rng: random.Random,
    center_tokens: int,
    relative_sigma: float,
    max_tokens: int,
) -> int:
    if center_tokens < 1:
        raise ValueError("center_tokens must be at least 1")
    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    if relative_sigma < 0:
        raise ValueError("relative_sigma must not be negative")

    standard_deviation = center_tokens * relative_sigma
    sampled = (
        float(center_tokens)
        if standard_deviation == 0
        else rng.normalvariate(center_tokens, standard_deviation)
    )
    rounded = int(round(sampled))
    return min(max(rounded, 1), max_tokens)


def pack_source_lines(
    lines: Iterable[SourceLine],
    *,
    config: PackingConfig,
    target_sampler: TargetSampler,
) -> PackingResult:
    packer = LinePacker(config=config, target_sampler=target_sampler)
    tasks: list[PackedTask] = []
    for line in lines:
        tasks.extend(packer.add_line(line))
    flushed = packer.flush()
    if flushed is not None:
        tasks.append(flushed)
    return PackingResult(
        tasks=tuple(tasks),
        skipped_oversized_lines=packer.skipped_oversized_lines,
        skipped_oversized_line_examples=packer.skipped_oversized_line_examples,
    )


def build_task_record(
    task: PackedTask,
    *,
    config: PackingConfig,
    source_lang: str,
    target_lang: str,
) -> JsonObject:
    return {
        "source_text": task.text,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "metadata": build_task_metadata(task, config=config),
    }


def build_task_metadata(task: PackedTask, *, config: PackingConfig) -> JsonObject:
    source_rows = _source_rows_for_task(task)
    return {
        "provenance": {
            "hf_dataset": config.dataset,
            "split": config.split,
            "source_row_uuids": [row["uuid"] for row in source_rows],
            "langs": [row["lang"] for row in source_rows],
            "source_row_token_counts": [row["token_count"] for row in source_rows],
            "source_rows": source_rows,
            "line_spans": _line_spans_for_task(task),
        },
        "tokenizer": config.tokenizer,
        "packing": {
            "sequence": task.sequence,
            "pack_token_count": task.token_count,
            "target_tokens": task.target_tokens,
            "line_count": len(task.lines),
            "max_tokens": config.max_tokens,
            "center_tokens": config.center_tokens,
            "relative_sigma": config.relative_sigma,
            "seed": config.seed,
        },
    }


def validate_staged_workspace(workspace: Path, *, write_manifest: bool) -> ValidationSummary:
    workspace = workspace.resolve()
    manifest_path = workspace / MANIFEST_FILENAME
    manifest = _read_json_object(manifest_path)
    errors = _validate_manifest_shape(manifest)
    max_tokens = _manifest_max_tokens(manifest)
    distribution = TokenDistribution(max_tokens=max_tokens)
    checked_shards = 0
    checked_tasks = 0

    shards = manifest.get("shards")
    if isinstance(shards, list):
        for shard_index, shard in enumerate(shards):
            if not isinstance(shard, dict):
                _append_validation_error(errors, f"shards[{shard_index}] must be an object")
                continue
            shard_path = _manifest_shard_path(workspace, shard, errors, shard_index)
            if shard_path is None:
                continue
            checked_shards += 1
            expected_rows = shard.get("rows")
            shard_rows = 0
            for line_number, record in _iter_jsonl_records(shard_path, errors):
                shard_rows += 1
                checked_tasks += 1
                record_errors = validate_task_record(
                    record,
                    manifest=manifest,
                    line_label=f"{shard_path.name}:{line_number}",
                )
                for error in record_errors:
                    _append_validation_error(errors, error)
                token_count = _record_pack_token_count(record)
                if token_count is not None:
                    distribution.add(token_count)
            if isinstance(expected_rows, int) and expected_rows != shard_rows:
                _append_validation_error(
                    errors,
                    f"shards[{shard_index}] expected {expected_rows} rows, found {shard_rows}",
                )

    materialization = manifest.get("materialization")
    if isinstance(materialization, dict):
        expected_tasks = materialization.get("tasks_written")
        if isinstance(expected_tasks, int) and expected_tasks != checked_tasks:
            _append_validation_error(
                errors,
                f"manifest expected {expected_tasks} tasks, found {checked_tasks}",
            )

    summary = ValidationSummary(
        status="invalid" if errors else "valid",
        checked_shards=checked_shards,
        checked_tasks=checked_tasks,
        errors=tuple(errors),
        token_distribution=distribution.summary(),
    )
    if write_manifest:
        manifest["validation"] = summary.to_manifest_status()
        _write_json_atomic(manifest_path, manifest)
    return summary


def validate_task_record(
    record: JsonObject,
    *,
    manifest: JsonObject,
    line_label: str,
) -> list[str]:
    errors: list[str] = []
    source_text = record.get("source_text")
    if not isinstance(source_text, str) or not source_text.strip():
        errors.append(f"{line_label} missing non-empty source_text")
    elif "\x00" in source_text:
        errors.append(f"{line_label} source_text contains NUL byte")
    elif len(source_text) > MAX_SOURCE_TEXT_LENGTH:
        errors.append(
            f"{line_label} source_text exceeds maximum length of {MAX_SOURCE_TEXT_LENGTH}"
        )

    for field_name in ("source_lang", "target_lang"):
        value = record.get(field_name)
        if not isinstance(value, str) or not value:
            errors.append(f"{line_label} missing {field_name}")
        elif len(value) > 16:
            errors.append(f"{line_label} {field_name} exceeds 16 characters")
        else:
            expected = manifest.get(field_name)
            if isinstance(expected, str) and value != expected:
                errors.append(f"{line_label} {field_name} does not match manifest")

    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        errors.append(f"{line_label} metadata must be an object")
        return errors
    metadata_bytes = len(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
    if metadata_bytes > MAX_METADATA_JSON_BYTES:
        errors.append(
            f"{line_label} metadata JSON exceeds maximum size of {MAX_METADATA_JSON_BYTES} bytes"
        )

    provenance = metadata.get("provenance")
    if not isinstance(provenance, dict):
        errors.append(f"{line_label} metadata.provenance must be an object")
    else:
        _validate_provenance(provenance, manifest, line_label, errors)

    packing = metadata.get("packing")
    if not isinstance(packing, dict):
        errors.append(f"{line_label} metadata.packing must be an object")
    else:
        _validate_packing_metadata(packing, manifest, line_label, errors)

    tokenizer = metadata.get("tokenizer")
    manifest_tokenizer = manifest.get("tokenizer")
    expected_tokenizer = (
        manifest_tokenizer.get("name") if isinstance(manifest_tokenizer, dict) else None
    )
    if not isinstance(tokenizer, str) or tokenizer == "":
        errors.append(f"{line_label} metadata.tokenizer must be a non-empty string")
    elif expected_tokenizer is not None and tokenizer != expected_tokenizer:
        errors.append(f"{line_label} tokenizer does not match manifest")

    return errors


def run_dry_run(args: argparse.Namespace) -> JsonObject:
    report = _process_hf_stream(args=args, write_workspace=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def run_materialize(args: argparse.Namespace) -> JsonObject:
    workspace = args.workspace.resolve()
    lock_path = _acquire_workspace_lock(workspace)
    try:
        _prepare_workspace(workspace, overwrite=args.overwrite)
        manifest = _process_hf_stream(args=args, write_workspace=True)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest
    finally:
        _release_workspace_lock(lock_path)


def run_validate(args: argparse.Namespace) -> ValidationSummary:
    summary = validate_staged_workspace(args.workspace, write_manifest=True)
    print(json.dumps(summary.to_manifest_status(), ensure_ascii=False, indent=2))
    return summary


def run_insert_db(args: argparse.Namespace) -> JsonObject:
    workspace = args.workspace.resolve()
    manifest = _read_json_object(workspace / MANIFEST_FILENAME)
    validation = manifest.get("validation")
    is_validated = isinstance(validation, dict) and validation.get("status") == "valid"
    if not is_validated and not args.allow_unvalidated_manifest:
        raise RuntimeError(
            "Manifest is not validated. Run the validate subcommand first or pass "
            "--allow-unvalidated-manifest."
        )
    validation_summary = validate_staged_workspace(workspace, write_manifest=False)
    if validation_summary.status != "valid":
        first_error = validation_summary.errors[0] if validation_summary.errors else "unknown error"
        raise RuntimeError(f"Staged workspace is not valid: {first_error}")
    if len(args.task_set_name) > 128:
        raise ValueError("task set name must be at most 128 characters")
    if args.batch_size < 1:
        raise ValueError("batch size must be at least 1")

    _ensure_backend_on_path()
    from app.db.bootstrap import bootstrap_schema
    from app.db.session import get_sessionmaker
    from app.models.task import Task, TaskSet
    from sqlalchemy import func, select

    bootstrap_schema()
    SessionLocal = get_sessionmaker()
    inserted = 0
    created_task_set = False

    with SessionLocal() as session:
        try:
            task_set = session.execute(
                select(TaskSet).where(TaskSet.name == args.task_set_name)
            ).scalar_one_or_none()
            if task_set is None:
                task_set = TaskSet(
                    name=args.task_set_name,
                    description=args.task_set_description,
                    metadata_json=_task_set_metadata_from_manifest(manifest),
                )
                session.add(task_set)
                session.flush()
                created_task_set = True
            else:
                existing_tasks = session.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.task_set_id == task_set.id)
                ).scalar_one()
                if existing_tasks > 0 and not args.append:
                    raise RuntimeError(
                        f"task set {args.task_set_name!r} already contains "
                        f"{existing_tasks} task(s); pass --append to add more"
                    )

            if args.dry_run:
                planned = sum(1 for _ in iter_staged_task_records(workspace, manifest))
                session.rollback()
                report = {
                    "ok": True,
                    "dry_run": True,
                    "task_set_name": args.task_set_name,
                    "task_set_created": created_task_set,
                    "tasks_planned": planned,
                    "batch_size": args.batch_size,
                }
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return report

            batch_count = 0
            for record in iter_staged_task_records(workspace, manifest):
                session.add(
                    Task(
                        task_set_id=task_set.id,
                        source_lang=record["source_lang"],
                        target_lang=record["target_lang"],
                        source_text=record["source_text"],
                        metadata_json=record.get("metadata"),
                    )
                )
                inserted += 1
                batch_count += 1
                if batch_count >= args.batch_size:
                    session.commit()
                    batch_count = 0
            session.commit()
        except Exception:
            session.rollback()
            raise

    report = {
        "ok": True,
        "task_set_name": args.task_set_name,
        "task_set_created": created_task_set,
        "tasks_inserted": inserted,
        "batch_size": args.batch_size,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def iter_staged_task_records(
    workspace: Path,
    manifest: JsonObject,
) -> Iterator[JsonObject]:
    workspace = workspace.resolve()
    shards = manifest.get("shards")
    if not isinstance(shards, list):
        raise ValueError("manifest shards must be a list")
    for shard_index, shard in enumerate(shards):
        errors: list[str] = []
        if not isinstance(shard, dict):
            raise ValueError(f"shards[{shard_index}] must be an object")
        shard_path = _manifest_shard_path(workspace, shard, errors, shard_index)
        if shard_path is None:
            raise ValueError("; ".join(errors))
        for _, record in _iter_jsonl_records(shard_path, errors):
            if errors:
                raise ValueError("; ".join(errors))
            yield record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stream, stage, validate, and explicitly insert Adam arena tasks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    dry_run = subparsers.add_parser(
        "dry-run",
        help="Stream and pack a bounded sample without writing files or DB rows.",
    )
    _add_streaming_arguments(dry_run, default_limit=DEFAULT_DRY_RUN_LIMIT)
    dry_run.set_defaults(func=run_dry_run)

    materialize = subparsers.add_parser(
        "materialize",
        help="Stream and write JSONL shards plus manifest into the workspace.",
    )
    _add_streaming_arguments(materialize, default_limit=None)
    materialize.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing staged Adam arena files in the workspace.",
    )
    materialize.set_defaults(func=run_materialize)

    validate = subparsers.add_parser(
        "validate",
        help="Validate staged JSONL shards and mark the manifest valid/invalid.",
    )
    _add_workspace_argument(validate)
    validate.set_defaults(func=run_validate)

    insert_db = subparsers.add_parser(
        "insert-db",
        help="Insert a validated staged workspace into the local DB.",
    )
    _add_workspace_argument(insert_db)
    insert_db.add_argument("--task-set-name", default=DEFAULT_TASK_SET_NAME)
    insert_db.add_argument(
        "--task-set-description",
        default="Hugging Face Adam arena split staged by ingest_adam_arena_split.py",
    )
    insert_db.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_INSERT_BATCH_SIZE,
        help="DB insert commit batch size.",
    )
    insert_db.add_argument(
        "--allow-unvalidated-manifest",
        action="store_true",
        help="Explicitly bypass the validated-manifest guard.",
    )
    insert_db.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate DB connectivity and count planned rows without committing inserts.",
    )
    insert_db.add_argument(
        "--append",
        action="store_true",
        help="Allow inserting into an existing non-empty task set.",
    )
    insert_db.set_defaults(func=run_insert_db)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _process_hf_stream(*, args: argparse.Namespace, write_workspace: bool) -> JsonObject:
    config = PackingConfig(
        dataset=args.dataset,
        split=args.split,
        tokenizer=args.tokenizer,
        max_tokens=args.max_tokens,
        center_tokens=args.center_tokens,
        relative_sigma=args.sigma,
        seed=args.seed,
    )
    _validate_packing_config(config)
    token_counter = HuggingFaceBatchTokenCounter(
        tokenizer_name=args.tokenizer,
        batch_size=args.token_batch_size,
        workers=args.workers,
        trust_remote_code=args.trust_remote_code,
    )
    target_sampler = NormalTargetTokenGenerator(
        center_tokens=args.center_tokens,
        relative_sigma=args.sigma,
        max_tokens=args.max_tokens,
        seed=args.seed,
    )
    packer = LinePacker(config=config, target_sampler=target_sampler)
    stats = MaterializationStats(max_tokens=args.max_tokens)
    writer = (
        JsonlShardWriter(workspace=args.workspace.resolve(), shard_rows=args.shard_rows)
        if write_workspace
        else None
    )

    try:
        row_batch: list[SourceRow] = []
        try:
            row_iter = iter(_iter_hf_dataset(args.dataset, args.split))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load Hugging Face dataset {args.dataset!r} "
                f"split {args.split!r}: {exc}"
            ) from exc
        try:
            while True:
                try:
                    raw_row = next(row_iter)
                except StopIteration:
                    break
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to read Hugging Face dataset {args.dataset!r} "
                        f"split {args.split!r}: {exc}"
                    ) from exc

                stats.source_rows_seen += 1
                row = _coerce_source_row(raw_row, row_number=stats.source_rows_seen)
                if args.source_lang_filter is not None and row.lang != args.source_lang_filter:
                    stats.source_rows_filtered += 1
                    continue
                stats.source_rows_accepted += 1
                row_batch.append(row)
                if len(row_batch) >= args.row_batch_size:
                    _process_row_batch(
                        row_batch,
                        token_counter=token_counter,
                        packer=packer,
                        writer=writer,
                        stats=stats,
                        config=config,
                        source_lang=args.source_lang,
                        target_lang=args.target_lang,
                    )
                    row_batch = []
                if args.limit is not None and stats.source_rows_accepted >= args.limit:
                    break
        finally:
            close = getattr(row_iter, "close", None)
            if callable(close):
                close()

        if row_batch:
            _process_row_batch(
                row_batch,
                token_counter=token_counter,
                packer=packer,
                writer=writer,
                stats=stats,
                config=config,
                source_lang=args.source_lang,
                target_lang=args.target_lang,
            )

        final_task = packer.flush()
        if final_task is not None:
            _emit_task(
                final_task,
                writer=writer,
                stats=stats,
                config=config,
                source_lang=args.source_lang,
                target_lang=args.target_lang,
            )
        if writer is not None:
            writer.close_current_shard()

        report = _build_manifest_or_report(
            args=args,
            config=config,
            stats=stats,
            packer=packer,
            shards=writer.shards if writer is not None else [],
            materialized=write_workspace,
        )
        if write_workspace:
            _write_json_atomic(args.workspace.resolve() / MANIFEST_FILENAME, report)
        return report
    finally:
        token_counter.close()


def _process_row_batch(
    rows: Sequence[SourceRow],
    *,
    token_counter: BatchTokenCounter,
    packer: LinePacker,
    writer: JsonlShardWriter | None,
    stats: MaterializationStats,
    config: PackingConfig,
    source_lang: str,
    target_lang: str,
) -> None:
    line_refs: list[tuple[SourceRow, int, str]] = []
    for row in rows:
        for line_index, line_text in enumerate(row.text.splitlines(keepends=True)):
            if "\x00" in line_text:
                stats.record_invalid_line(
                    SkippedInvalidLine(
                        reason="nul_byte",
                        row_uuid=row.row_uuid,
                        lang=row.lang,
                        source_row_token_count=row.token_count,
                        line_index=line_index,
                        char_count=len(line_text),
                    )
                )
                continue
            line_refs.append((row, line_index, line_text))
    if not line_refs:
        return

    token_counts = token_counter.count_tokens([line_text for _, _, line_text in line_refs])
    if len(token_counts) != len(line_refs):
        raise RuntimeError("tokenizer returned a different number of token counts")

    stats.source_lines_seen += len(line_refs)
    for (row, line_index, line_text), token_count in zip(
        line_refs,
        token_counts,
        strict=True,
    ):
        line = SourceLine(
            row_uuid=row.row_uuid,
            lang=row.lang,
            source_row_token_count=row.token_count,
            line_index=line_index,
            text=line_text,
            token_count=token_count,
        )
        for task in packer.add_line(line):
            _emit_task(
                task,
                writer=writer,
                stats=stats,
                config=config,
                source_lang=source_lang,
                target_lang=target_lang,
            )


def _emit_task(
    task: PackedTask,
    *,
    writer: JsonlShardWriter | None,
    stats: MaterializationStats,
    config: PackingConfig,
    source_lang: str,
    target_lang: str,
) -> None:
    record = build_task_record(
        task,
        config=config,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    if writer is not None:
        writer.write(record)
    stats.record_task(task)


def _iter_hf_dataset(dataset_name: str, split: str) -> Iterable[JsonObject]:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError(
            "Missing optional dependency 'datasets'. Install it in the backend "
            "environment before dry-run or materialize."
        ) from exc
    return load_dataset(
        dataset_name,
        split=split,
        streaming=True,
        columns=list(HF_COLUMNS),
    )


def _coerce_source_row(raw_row: object, *, row_number: int) -> SourceRow:
    if not isinstance(raw_row, dict):
        raise ValueError(f"source row {row_number} is not an object")
    row_uuid = raw_row.get("uuid")
    text = raw_row.get("text")
    lang = raw_row.get("lang")
    if not isinstance(row_uuid, str) or row_uuid == "":
        raise ValueError(f"source row {row_number} missing uuid")
    if not isinstance(text, str):
        raise ValueError(f"source row {row_number} missing text")
    if not isinstance(lang, str) or lang == "":
        raise ValueError(f"source row {row_number} missing lang")
    token_count_raw = raw_row.get("token_count")
    token_count = int(token_count_raw) if isinstance(token_count_raw, int | float) else None
    return SourceRow(
        row_uuid=row_uuid,
        text=text,
        lang=lang,
        token_count=token_count,
    )


def _build_manifest_or_report(
    *,
    args: argparse.Namespace,
    config: PackingConfig,
    stats: MaterializationStats,
    packer: LinePacker,
    shards: list[JsonObject],
    materialized: bool,
) -> JsonObject:
    materialization_counts = stats.to_manifest_counts(
        skipped_oversized_lines=packer.skipped_oversized_lines,
        skipped_examples=packer.skipped_oversized_line_examples,
    )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "mode": "materialize" if materialized else "dry-run",
        "dataset": {
            "name": config.dataset,
            "split": config.split,
            "columns": list(HF_COLUMNS),
        },
        "tokenizer": {"name": config.tokenizer},
        "workspace": str(args.workspace.resolve()) if materialized else None,
        "source_lang": args.source_lang,
        "target_lang": args.target_lang,
        "source_lang_filter": args.source_lang_filter,
        "packing": {
            "max_tokens": config.max_tokens,
            "center_tokens": config.center_tokens,
            "relative_sigma": config.relative_sigma,
            "seed": config.seed,
        },
        "limits": {
            "source_row_limit": args.limit,
            "row_batch_size": args.row_batch_size,
            "token_batch_size": args.token_batch_size,
            "workers": args.workers,
            "shard_rows": args.shard_rows,
        },
        "materialization": materialization_counts,
        "shards": shards,
        "validation": {"status": "not_validated" if materialized else "not_applicable"},
    }


def _source_rows_for_task(task: PackedTask) -> list[JsonObject]:
    rows: list[JsonObject] = []
    seen: set[str] = set()
    line_counts: dict[str, int] = {}
    for line in task.lines:
        line_counts[line.row_uuid] = line_counts.get(line.row_uuid, 0) + 1
    for line in task.lines:
        if line.row_uuid in seen:
            continue
        seen.add(line.row_uuid)
        rows.append(
            {
                "uuid": line.row_uuid,
                "lang": line.lang,
                "token_count": line.source_row_token_count,
                "packed_line_count": line_counts[line.row_uuid],
            }
        )
    return rows


def _line_spans_for_task(task: PackedTask) -> list[JsonObject]:
    spans: list[JsonObject] = []
    for line in task.lines:
        if spans and _can_extend_span(spans[-1], line):
            spans[-1]["end_line_exclusive"] = line.line_index + 1
            spans[-1]["line_count"] += 1
            continue
        spans.append(
            {
                "row_uuid": line.row_uuid,
                "lang": line.lang,
                "source_row_token_count": line.source_row_token_count,
                "start_line": line.line_index,
                "end_line_exclusive": line.line_index + 1,
                "line_count": 1,
            }
        )
    return spans


def _can_extend_span(span: JsonObject, line: SourceLine) -> bool:
    return (
        span.get("row_uuid") == line.row_uuid
        and span.get("lang") == line.lang
        and span.get("source_row_token_count") == line.source_row_token_count
        and span.get("end_line_exclusive") == line.line_index
    )


def _validate_manifest_shape(manifest: JsonObject) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("manifest schema_version is unsupported")
    if not isinstance(manifest.get("dataset"), dict):
        errors.append("manifest dataset must be an object")
    if not isinstance(manifest.get("tokenizer"), dict):
        errors.append("manifest tokenizer must be an object")
    if not isinstance(manifest.get("packing"), dict):
        errors.append("manifest packing must be an object")
    if not isinstance(manifest.get("materialization"), dict):
        errors.append("manifest materialization must be an object")
    if not isinstance(manifest.get("shards"), list):
        errors.append("manifest shards must be a list")
    return errors


def _manifest_max_tokens(manifest: JsonObject) -> int:
    packing = manifest.get("packing")
    if isinstance(packing, dict) and isinstance(packing.get("max_tokens"), int):
        max_tokens = packing["max_tokens"]
        if max_tokens >= 1:
            return max_tokens
    return DEFAULT_MAX_TOKENS


def _manifest_shard_path(
    workspace: Path,
    shard: JsonObject,
    errors: list[str],
    shard_index: int,
) -> Path | None:
    relative_path = shard.get("path")
    if not isinstance(relative_path, str) or relative_path == "":
        _append_validation_error(errors, f"shards[{shard_index}] missing path")
        return None
    shard_path = (workspace / relative_path).resolve()
    if not shard_path.is_relative_to(workspace):
        _append_validation_error(errors, f"shards[{shard_index}] path escapes workspace")
        return None
    if not shard_path.exists():
        _append_validation_error(errors, f"shards[{shard_index}] file does not exist")
        return None
    return shard_path


def _iter_jsonl_records(
    path: Path,
    errors: list[str],
) -> Iterator[tuple[int, JsonObject]]:
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                _append_validation_error(
                    errors,
                    f"{path.name}:{line_number} invalid JSON: {exc.msg}",
                )
                continue
            if not isinstance(record, dict):
                _append_validation_error(
                    errors,
                    f"{path.name}:{line_number} must be a JSON object",
                )
                continue
            yield line_number, record


def _validate_provenance(
    provenance: JsonObject,
    manifest: JsonObject,
    line_label: str,
    errors: list[str],
) -> None:
    manifest_dataset = manifest.get("dataset")
    expected_dataset = (
        manifest_dataset.get("name") if isinstance(manifest_dataset, dict) else None
    )
    expected_split = (
        manifest_dataset.get("split") if isinstance(manifest_dataset, dict) else None
    )
    if expected_dataset is not None and provenance.get("hf_dataset") != expected_dataset:
        errors.append(f"{line_label} provenance dataset does not match manifest")
    if expected_split is not None and provenance.get("split") != expected_split:
        errors.append(f"{line_label} provenance split does not match manifest")
    if not isinstance(provenance.get("source_row_uuids"), list):
        errors.append(f"{line_label} source_row_uuids must be a list")
    if not isinstance(provenance.get("langs"), list):
        errors.append(f"{line_label} langs must be a list")
    if not isinstance(provenance.get("source_row_token_counts"), list):
        errors.append(f"{line_label} source_row_token_counts must be a list")
    line_spans = provenance.get("line_spans")
    if not isinstance(line_spans, list) or not line_spans:
        errors.append(f"{line_label} line_spans must be a non-empty list")


def _validate_packing_metadata(
    packing: JsonObject,
    manifest: JsonObject,
    line_label: str,
    errors: list[str],
) -> None:
    pack_token_count = packing.get("pack_token_count")
    max_tokens = _manifest_max_tokens(manifest)
    if not isinstance(pack_token_count, int):
        errors.append(f"{line_label} pack_token_count must be an integer")
    elif pack_token_count > max_tokens:
        errors.append(f"{line_label} pack_token_count exceeds max_tokens")
    elif pack_token_count < 0:
        errors.append(f"{line_label} pack_token_count must not be negative")
    if not isinstance(packing.get("target_tokens"), int):
        errors.append(f"{line_label} target_tokens must be an integer")
    if not isinstance(packing.get("line_count"), int) or packing.get("line_count") < 1:
        errors.append(f"{line_label} line_count must be a positive integer")
    manifest_packing = manifest.get("packing")
    if not isinstance(manifest_packing, dict):
        return
    for field_name in ("max_tokens", "center_tokens", "relative_sigma", "seed"):
        expected = manifest_packing.get(field_name)
        if expected is not None and packing.get(field_name) != expected:
            errors.append(f"{line_label} packing.{field_name} does not match manifest")


def _record_pack_token_count(record: JsonObject) -> int | None:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    packing = metadata.get("packing")
    if not isinstance(packing, dict):
        return None
    token_count = packing.get("pack_token_count")
    return token_count if isinstance(token_count, int) and token_count >= 0 else None


def _prepare_workspace(workspace: Path, *, overwrite: bool) -> None:
    manifest_path = workspace / MANIFEST_FILENAME
    shards_dir = workspace / SHARDS_DIRNAME
    existing_files = []
    if manifest_path.exists():
        existing_files.append(manifest_path)
    if shards_dir.exists():
        existing_files.extend(shards_dir.glob("tasks-*.jsonl"))
        existing_files.extend(shards_dir.glob("*.tmp"))
        existing_files.extend(shards_dir.glob(".*.tmp"))
    if existing_files and not overwrite:
        raise RuntimeError(
            f"workspace {workspace} already contains staged files; pass --overwrite"
        )
    workspace.mkdir(parents=True, exist_ok=True)
    shards_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for path in existing_files:
            try:
                path.unlink()
            except FileNotFoundError:
                continue


def _acquire_workspace_lock(workspace: Path) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    lock_path = workspace / ".materialize.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            f"workspace {workspace} is already being materialized; remove {lock_path} "
            "only after confirming no importer process is running"
        ) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(f"pid={os.getpid()}\ncreated_at={_utc_now()}\n")
    return lock_path


def _release_workspace_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return


def _task_set_metadata_from_manifest(manifest: JsonObject) -> JsonObject:
    materialization = manifest.get("materialization")
    return {
        "ingest_script": "backend/scripts/ingest_adam_arena_split.py",
        "manifest_schema_version": manifest.get("schema_version"),
        "dataset": manifest.get("dataset"),
        "tokenizer": manifest.get("tokenizer"),
        "packing": manifest.get("packing"),
        "source_lang": manifest.get("source_lang"),
        "target_lang": manifest.get("target_lang"),
        "source_lang_filter": manifest.get("source_lang_filter"),
        "tasks_written": (
            materialization.get("tasks_written")
            if isinstance(materialization, dict)
            else None
        ),
    }


def _read_json_object(path: Path) -> JsonObject:
    if not path.exists():
        raise ValueError(f"missing JSON file: {path}")
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json_atomic(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
    tmp_path.replace(path)


def _validate_packing_config(config: PackingConfig) -> None:
    if config.max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    if config.center_tokens < 1:
        raise ValueError("center_tokens must be at least 1")
    if config.relative_sigma < 0:
        raise ValueError("sigma must not be negative")
    if not config.dataset:
        raise ValueError("dataset must not be empty")
    if not config.split:
        raise ValueError("split must not be empty")
    if not config.tokenizer:
        raise ValueError("tokenizer must not be empty")


def _append_validation_error(errors: list[str], message: str) -> None:
    if len(errors) < MAX_VALIDATION_ERRORS:
        errors.append(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ensure_backend_on_path() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    backend_root_text = str(backend_root)
    if backend_root_text not in sys.path:
        sys.path.insert(0, backend_root_text)


def _add_workspace_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help="Repo-local staging workspace when run from backend/.",
    )


def _add_streaming_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_limit: int | None,
) -> None:
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--tokenizer", default=DEFAULT_TOKENIZER)
    _add_workspace_argument(parser)
    parser.add_argument("--source-lang", default=DEFAULT_SOURCE_LANG)
    parser.add_argument("--target-lang", default=DEFAULT_TARGET_LANG)
    parser.add_argument("--source-lang-filter", default=DEFAULT_SOURCE_LANG)
    parser.add_argument(
        "--no-source-lang-filter",
        action="store_const",
        const=None,
        dest="source_lang_filter",
        help="Disable filtering by the source row lang column.",
    )
    parser.add_argument("--max-tokens", type=_positive_int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument(
        "--center-tokens",
        "--median-tokens",
        dest="center_tokens",
        type=_positive_int,
        default=DEFAULT_CENTER_TOKENS,
    )
    parser.add_argument("--sigma", type=_non_negative_float, default=DEFAULT_RELATIVE_SIGMA)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--shard-rows",
        type=_positive_int,
        default=DEFAULT_SHARD_ROWS,
        help="Approximate number of task rows per JSONL shard.",
    )
    parser.add_argument(
        "--row-batch-size",
        type=_positive_int,
        default=DEFAULT_ROW_BATCH_SIZE,
        help="Accepted source rows buffered before tokenization.",
    )
    parser.add_argument(
        "--token-batch-size",
        type=_positive_int,
        default=DEFAULT_TOKEN_BATCH_SIZE,
        help="Line texts per tokenizer call.",
    )
    parser.add_argument(
        "--workers",
        type=_positive_int,
        default=1,
        help="Bounded tokenizer process count for tokenization chunks.",
    )
    parser.add_argument(
        "--limit",
        type=_optional_limit,
        default=default_limit,
        help="Accepted source row limit; use 0 for no limit.",
    )
    parser.add_argument(
        "--trust-remote-code",
        action="store_true",
        help="Pass trust_remote_code=True to AutoTokenizer.from_pretrained.",
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _optional_limit(value: str) -> int | None:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return None if parsed == 0 else parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # Avoid native extension finalizers crashing after HF streaming/tokenizer use.
    os._exit(exit_code)
