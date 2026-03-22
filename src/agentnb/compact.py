from __future__ import annotations

import re
from typing import Any, cast
from urllib.parse import urlsplit

from .execution_output import preview_from_result_text
from .history import summarize_history_text
from .journal import JournalEntry
from .payloads import (
    CompactExecPayloadInput,
    DataframePreview,
    ExecPayload,
    HistoryEntryPayload,
    InspectPayload,
    InspectPreview,
    JSONValue,
    MappingPreview,
    RunListEntryPayload,
    RunSnapshot,
    SequencePreview,
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_URL_RE = re.compile(r"https?://[^\s'\"`]+")
_QUOTED_LITERAL_RE = re.compile(r"(['\"])([^'\"\n]{25,})\1")
_TRACEBACK_HEAD_LINES = 2
_TRACEBACK_TAIL_LINES = 3
_MEMBER_LIMIT = 20
_HEAD_ROW_LIMIT = 3
_HEAD_COLUMN_LIMIT = 10
_PREVIEW_LIST_LIMIT = 3
_PREVIEW_DICT_LIMIT = 5
_RESULT_LIMIT = 240
_STDOUT_LIMIT = 200
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


def compact_execution_payload(
    payload: CompactExecPayloadInput,
    *,
    no_truncate: bool = False,
) -> ExecPayload:
    compacted: ExecPayload = {"duration_ms": payload.get("duration_ms", 0)}

    status = payload.get("status")
    if status is not None:
        compacted["status"] = status

    execution_id = payload.get("execution_id")
    if execution_id is not None:
        compacted["execution_id"] = execution_id

    execution_count = payload.get("execution_count")
    if execution_count is not None:
        compacted["execution_count"] = execution_count

    stdout = payload.get("stdout")
    if isinstance(stdout, str) and stdout:
        if no_truncate:
            compacted["stdout"] = stdout
        else:
            summary = summarize_history_text(stdout, limit=_STDOUT_LIMIT)
            if summary is not None:
                if len(stdout) > _STDOUT_LIMIT:
                    summary = summary + f" [{len(stdout) - _STDOUT_LIMIT} chars truncated]"
                compacted["stdout"] = summary

    stderr = payload.get("stderr")
    if isinstance(stderr, str) and stderr:
        if no_truncate:
            compacted["stderr"] = stderr
        else:
            summary = summarize_history_text(stderr, limit=_STDOUT_LIMIT)
            if summary is not None:
                if len(stderr) > _STDOUT_LIMIT:
                    summary = summary + f" [{len(stderr) - _STDOUT_LIMIT} chars truncated]"
                compacted["stderr"] = summary

    result = payload.get("result")
    if isinstance(result, str) and result:
        if no_truncate:
            compacted["result"] = result
        else:
            summary = summarize_history_text(result, limit=_RESULT_LIMIT)
            if summary is not None:
                compacted["result"] = summary

    result_preview = compact_result_preview(
        result_preview=payload.get("result_preview"),
        result=result,
    )
    if result_preview is not None:
        compacted["result_preview"] = result_preview

    ename = payload.get("ename")
    if isinstance(ename, str):
        compacted["ename"] = ename

    evalue = payload.get("evalue")
    if isinstance(evalue, str):
        compacted["evalue"] = evalue

    source_kind = payload.get("source_kind")
    if isinstance(source_kind, str):
        compacted["source_kind"] = cast(Any, source_kind)
    source_path = payload.get("source_path")
    if isinstance(source_path, str) and source_path:
        compacted["source_path"] = source_path
    namespace_delta = payload.get("namespace_delta")
    if isinstance(namespace_delta, dict):
        compacted["namespace_delta"] = cast(Any, dict(namespace_delta))

    selected_output = payload.get("selected_output")
    if isinstance(selected_output, str):
        compacted["selected_output"] = selected_output
        compacted["selected_text"] = str(payload.get("selected_text", ""))

    return compacted


def compact_result_preview(
    *,
    result_preview: object,
    result: object,
) -> InspectPreview | None:
    if isinstance(result_preview, dict):
        return compact_preview(cast(InspectPreview, result_preview))
    if isinstance(result, str):
        derived_preview = preview_from_result_text(result)
        if isinstance(derived_preview, dict):
            return compact_preview(derived_preview)
    return None


def compact_inspect_payload(payload: InspectPayload) -> InspectPayload:
    compacted: InspectPayload = {}
    name = payload.get("name")
    if isinstance(name, str):
        compacted["name"] = name
    type_name = payload.get("type")
    if isinstance(type_name, str):
        compacted["type"] = type_name
    preview = payload.get("preview")
    if isinstance(preview, dict):
        compacted_preview = compact_preview(cast(InspectPreview, preview))
        if compacted_preview:
            compacted["preview"] = compacted_preview
            return compacted

    repr_text = payload.get("repr")
    if isinstance(repr_text, str) and repr_text:
        summary = summarize_history_text(repr_text, limit=_RESULT_LIMIT)
        if summary is not None:
            compacted["repr"] = summary

    members = payload.get("members")
    if isinstance(members, list) and members:
        compacted["members"] = [str(member) for member in members[:_MEMBER_LIMIT]]

    return compacted


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
            parts.append(f"columns={', '.join(str(column) for column in columns[:5])}")
        return " ".join(parts)
    if kind == "mapping-like":
        mapping = cast(MappingPreview, preview)
        parts = [f"mapping len={mapping.get('length', 0)}"]
        keys = mapping.get("keys")
        if isinstance(keys, list) and keys:
            parts.append(f"keys={', '.join(str(key) for key in keys[:5])}")
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
        return " ".join(parts)
    return str(preview)


def compact_dataframe_preview(preview: DataframePreview) -> DataframePreview:
    compacted: DataframePreview = {"kind": "dataframe-like"}
    for key in ("shape", "columns", "dtypes", "null_counts"):
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
        if isinstance(sample, dict):
            compacted["sample"] = cast(dict[str, JSONValue], _compact_jsonish(sample))
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
    if isinstance(sample, list):
        compacted["sample"] = cast(list[JSONValue], _compact_jsonish(sample))
    return compacted


def full_history_entry(entry: JournalEntry) -> HistoryEntryPayload:
    payload: HistoryEntryPayload = {
        "kind": entry.kind,
        "ts": entry.ts,
        "status": entry.status,
        "duration_ms": entry.duration_ms,
        "command_type": entry.command_type,
        "label": entry.label,
        "user_visible": entry.user_visible,
    }
    if entry.error_type is not None:
        payload["error_type"] = entry.error_type
    if entry.execution_id is not None:
        payload["execution_id"] = entry.execution_id
    if entry.code is not None:
        payload["code"] = entry.code
    return payload


def compact_history_entry(entry: JournalEntry) -> HistoryEntryPayload:
    label = entry.label
    command_type = entry.command_type
    if command_type == "exec":
        is_internal = entry.kind == "kernel_execution" or not entry.user_visible
        if entry.status == "error":
            error_type = entry.error_type
            if is_internal:
                label = (
                    "exec kernel error" if error_type is None else f"exec kernel error {error_type}"
                )
            else:
                label = "exec error" if error_type is None else f"exec error {error_type}"
        else:
            preview = summarize_exec_label(entry.code or entry.input or "")
            if is_internal:
                label = (
                    "exec kernel execution"
                    if preview is None
                    else f"exec kernel execution {preview}"
                )
            else:
                label = "exec" if preview is None else f"exec {preview}"

    compacted: HistoryEntryPayload = {
        "kind": entry.kind,
        "ts": entry.ts,
        "status": entry.status,
        "duration_ms": entry.duration_ms,
        "command_type": command_type,
        "label": label,
        "user_visible": entry.user_visible,
    }
    error_type = entry.error_type
    if error_type is not None:
        compacted["error_type"] = error_type
    execution_id = entry.execution_id
    if execution_id is not None:
        compacted["execution_id"] = execution_id
    if entry.user_visible and entry.code is not None:
        summary = summarize_history_text(entry.code)
        if summary is not None:
            compacted["code"] = summary
    return compacted


def compact_run_entry(entry: RunSnapshot) -> RunListEntryPayload:
    compacted: RunListEntryPayload = {
        "execution_id": entry.get("execution_id"),
        "ts": entry.get("ts"),
        "session_id": entry.get("session_id"),
        "command_type": entry.get("command_type"),
        "status": entry.get("status"),
        "duration_ms": entry.get("duration_ms"),
    }

    terminal_reason = entry.get("terminal_reason")
    if terminal_reason is not None:
        compacted["terminal_reason"] = terminal_reason

    if "cancel_requested" in entry:
        compacted["cancel_requested"] = bool(entry.get("cancel_requested"))

    result_preview = compact_result_preview(
        result_preview=entry.get("result_preview"),
        result=entry.get("result"),
    )
    if result_preview is not None:
        compacted["result_preview"] = result_preview
    else:
        result = entry.get("result")
        if isinstance(result, str) and result:
            summary = summarize_history_text(result, limit=_RESULT_LIMIT)
            if summary is not None:
                compacted["result_preview"] = summary

    stdout = entry.get("stdout")
    if isinstance(stdout, str) and stdout:
        summary = summarize_history_text(stdout, limit=_STDOUT_LIMIT)
        if summary is not None:
            compacted["stdout_preview"] = summary

    ename = entry.get("ename")
    if ename is not None:
        compacted["error_type"] = ename

    return compacted


def summarize_exec_label(value: str | None, limit: int = _HISTORY_INPUT_LIMIT) -> str | None:
    if value is None:
        return None

    compact = " ".join(value.strip().split())
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
