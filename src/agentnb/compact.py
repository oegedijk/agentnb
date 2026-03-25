from __future__ import annotations

import re
from typing import Any, cast
from urllib.parse import urlsplit

from .history import summarize_history_lines_inline, summarize_history_text
from .payloads import (
    DataframePreview,
    InspectPreview,
    JSONValue,
    MappingPreview,
    SequencePreview,
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_URL_RE = re.compile(r"https?://[^\s'\"`]+")
_QUOTED_LITERAL_RE = re.compile(r"(['\"])([^'\"\n]{25,})\1")
_TRACEBACK_HEAD_LINES = 2
_TRACEBACK_TAIL_LINES = 3
_HEAD_ROW_LIMIT = 3
_HEAD_COLUMN_LIMIT = 10
_PREVIEW_LIST_LIMIT = 3
_PREVIEW_DICT_LIMIT = 5
_HISTORY_INPUT_LIMIT = 64


def compact_traceback(lines: list[str] | None) -> list[str] | None:
    if not lines:
        return None
    cleaned = strip_ansi_lines(lines)
    if len(cleaned) <= _TRACEBACK_HEAD_LINES + _TRACEBACK_TAIL_LINES:
        return cleaned
    return [
        *cleaned[:_TRACEBACK_HEAD_LINES],
        "...",
        *cleaned[-_TRACEBACK_TAIL_LINES:],
    ]


def strip_ansi_lines(lines: list[str]) -> list[str]:
    return [_ANSI_ESCAPE_RE.sub("", line) for line in lines if line]


def compact_preview(preview: InspectPreview) -> InspectPreview:
    if preview.get("kind") == "dataframe-like":
        return compact_dataframe_preview(cast(DataframePreview, preview))
    if preview.get("kind") in {"sequence-like", "mapping-like"}:
        return compact_collection_preview(cast(MappingPreview | SequencePreview, preview))
    return preview


def preview_text(preview: InspectPreview) -> str:
    kind = preview.get("kind")
    if kind == "dataframe-like":
        dataframe = cast(DataframePreview, preview)
        parts = ["DataFrame"]
        shape = dataframe.get("shape")
        if isinstance(shape, list) and len(shape) == 2:
            parts.append(f"shape=({shape[0]}, {shape[1]})")
        columns = dataframe.get("columns")
        if isinstance(columns, list) and columns:
            shown = ", ".join(str(column) for column in columns[:5])
            column_count = dataframe.get("column_count")
            if isinstance(column_count, int) and column_count > len(columns):
                shown = shown + ", ..."
            parts.append(f"columns={shown}")
        return " ".join(parts)
    if kind == "mapping-like":
        mapping = cast(MappingPreview, preview)
        parts = [f"mapping len={mapping.get('length', 0)}"]
        keys = mapping.get("keys")
        if isinstance(keys, list) and keys:
            parts.append(f"keys={', '.join(str(key) for key in keys[:5])}")
        sample = mapping.get("sample")
        if isinstance(sample, dict) and sample:
            summary = summarize_history_text(str(sample), limit=80)
            if summary is not None:
                parts.append(f"sample={summary}")
        return " ".join(parts)
    if kind == "sequence-like":
        sequence = cast(SequencePreview, preview)
        parts = [f"sequence len={sequence.get('length', 0)}"]
        item_type = sequence.get("item_type")
        if isinstance(item_type, str) and item_type:
            parts.append(f"item_type={item_type}")
        sample_keys = sequence.get("sample_keys")
        if isinstance(sample_keys, list) and sample_keys:
            parts.append(f"keys={', '.join(str(key) for key in sample_keys[:5])}")
        sample = sequence.get("sample")
        if isinstance(sample, list) and sample:
            summary = summarize_history_text(str(sample[0]), limit=80)
            if summary is not None:
                parts.append(f"sample={summary}")
        return " ".join(parts)
    return str(preview)


def compact_dataframe_preview(preview: DataframePreview) -> DataframePreview:
    compacted: DataframePreview = {"kind": "dataframe-like"}
    for key in (
        "shape",
        "columns",
        "column_count",
        "columns_shown",
        "dtypes",
        "dtypes_shown",
        "null_counts",
        "null_count_fields_shown",
        "head_rows_shown",
    ):
        value = preview.get(key)
        if value:
            compacted[key] = value

    head = preview.get("head")
    if isinstance(head, list) and head:
        truncated_rows = head[:_HEAD_ROW_LIMIT]
        compacted["head"] = [
            {k: v for i, (k, v) in enumerate(row.items()) if i < _HEAD_COLUMN_LIMIT}
            if isinstance(row, dict) and len(row) > _HEAD_COLUMN_LIMIT
            else row
            for row in truncated_rows
        ]

    return compacted


def compact_collection_preview(
    preview: MappingPreview | SequencePreview,
) -> MappingPreview | SequencePreview:
    length = preview.get("length")
    sample = preview.get("sample")

    if preview.get("kind") == "mapping-like":
        compacted: MappingPreview = {
            "kind": "mapping-like",
            "length": 0 if not isinstance(length, int) else length,
            "keys": [],
            "sample": {},
        }
        keys = preview.get("keys")
        if isinstance(keys, list) and keys:
            compacted["keys"] = [str(item) for item in keys[:_PREVIEW_DICT_LIMIT]]
        keys_shown = preview.get("keys_shown")
        if isinstance(keys_shown, int):
            compacted["keys_shown"] = keys_shown
        if isinstance(sample, dict):
            compacted["sample"] = cast(dict[str, JSONValue], _compact_jsonish(sample))
        sample_items_shown = preview.get("sample_items_shown")
        if isinstance(sample_items_shown, int):
            compacted["sample_items_shown"] = sample_items_shown
        if isinstance(preview.get("sample_truncated"), bool):
            compacted["sample_truncated"] = cast(bool, preview.get("sample_truncated"))
        return compacted

    compacted: SequencePreview = {
        "kind": "sequence-like",
        "length": 0 if not isinstance(length, int) else length,
    }
    item_type = preview.get("item_type")
    if isinstance(item_type, str) and item_type:
        compacted["item_type"] = item_type
    sample_keys = preview.get("sample_keys")
    if isinstance(sample_keys, list) and sample_keys:
        compacted["sample_keys"] = [str(item) for item in sample_keys[:_PREVIEW_DICT_LIMIT]]
    sample_keys_shown = preview.get("sample_keys_shown")
    if isinstance(sample_keys_shown, int):
        compacted["sample_keys_shown"] = sample_keys_shown
    if isinstance(sample, list):
        compacted["sample"] = cast(list[JSONValue], _compact_jsonish(sample))
    sample_items_shown = preview.get("sample_items_shown")
    if isinstance(sample_items_shown, int):
        compacted["sample_items_shown"] = sample_items_shown
    if isinstance(preview.get("sample_truncated"), bool):
        compacted["sample_truncated"] = cast(bool, preview.get("sample_truncated"))
    return compacted


def summarize_exec_label(value: str | None, limit: int = _HISTORY_INPUT_LIMIT) -> str | None:
    if value is None:
        return None

    compact = summarize_history_lines_inline(value, limit=limit * 2)
    if not compact:
        return None

    compact = _URL_RE.sub(_compact_url_match, compact)
    compact = _QUOTED_LITERAL_RE.sub(_compact_literal_match, compact)
    return summarize_history_text(compact, limit=limit)


def _compact_url_match(match: re.Match[str]) -> str:
    url = match.group(0)
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return summarize_history_text(url, limit=28) or url

    path = parsed.path if parsed.path and parsed.path != "/" else ""
    shortened_path = path
    if len(path) > 18:
        shortened_path = path[:15] + "..."
    compact = f"{parsed.scheme}://{parsed.netloc}{shortened_path}"
    if parsed.query:
        compact += "?..."
    return compact


def _compact_literal_match(match: re.Match[str]) -> str:
    quote = match.group(1)
    value = match.group(2)
    if "://" in value:
        return f"{quote}{value}{quote}"
    shortened = summarize_history_text(value, limit=20) or value[:20]
    return f"{quote}{shortened}{quote}"


def _compact_jsonish(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value

    if isinstance(value, str):
        return summarize_history_text(value, limit=80) or ""

    if depth >= 6:
        text = str(value)
        return summarize_history_text(text, limit=80) or text[:80]

    if isinstance(value, list):
        return [_compact_jsonish(item, depth=depth + 1) for item in value[:_PREVIEW_LIST_LIMIT]]

    if isinstance(value, tuple):
        return [_compact_jsonish(item, depth=depth + 1) for item in value[:_PREVIEW_LIST_LIMIT]]

    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _PREVIEW_DICT_LIMIT:
                break
            compacted[str(key)] = _compact_jsonish(item, depth=depth + 1)
        return compacted

    text = str(value)
    return summarize_history_text(text, limit=80) or text[:80]
