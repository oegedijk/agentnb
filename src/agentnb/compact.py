from __future__ import annotations

import re
from typing import Any, cast
from urllib.parse import urlsplit

from .history import summarize_history_text
from .journal import JournalEntry
from .payloads import (
    DataframePreview,
    ExecPayload,
    HistoryEntryPayload,
    InspectPayload,
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
_PREVIEW_LIST_LIMIT = 3
_PREVIEW_DICT_LIMIT = 5
_RESULT_LIMIT = 240
_STDOUT_LIMIT = 200
_HISTORY_INPUT_LIMIT = 64


def compact_traceback(lines: list[str] | None) -> list[str] | None:
    if not lines:
        return None
    cleaned = [_ANSI_ESCAPE_RE.sub("", line) for line in lines if line]
    if len(cleaned) <= _TRACEBACK_HEAD_LINES + _TRACEBACK_TAIL_LINES:
        return cleaned
    return [
        *cleaned[:_TRACEBACK_HEAD_LINES],
        "...",
        *cleaned[-_TRACEBACK_TAIL_LINES:],
    ]


def compact_execution_payload(payload: RunSnapshot) -> ExecPayload:
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
        summary = summarize_history_text(stdout, limit=_STDOUT_LIMIT)
        if summary is not None:
            compacted["stdout"] = summary

    stderr = payload.get("stderr")
    if isinstance(stderr, str) and stderr:
        summary = summarize_history_text(stderr, limit=_STDOUT_LIMIT)
        if summary is not None:
            compacted["stderr"] = summary

    result = payload.get("result")
    if isinstance(result, str) and result:
        summary = summarize_history_text(result, limit=_RESULT_LIMIT)
        if summary is not None:
            compacted["result"] = summary

    ename = payload.get("ename")
    if isinstance(ename, str):
        compacted["ename"] = ename

    evalue = payload.get("evalue")
    if isinstance(evalue, str):
        compacted["evalue"] = evalue

    selected_output = payload.get("selected_output")
    if isinstance(selected_output, str):
        compacted["selected_output"] = selected_output
        compacted["selected_text"] = str(payload.get("selected_text", ""))

    return compacted


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
        if preview.get("kind") == "dataframe-like":
            compacted["preview"] = compact_dataframe_preview(cast(DataframePreview, preview))
            return compacted
        if preview.get("kind") in {"sequence-like", "mapping-like"}:
            compacted["preview"] = compact_collection_preview(
                cast(MappingPreview | SequencePreview, preview)
            )
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


def compact_dataframe_preview(preview: DataframePreview) -> DataframePreview:
    compacted: DataframePreview = {"kind": "dataframe-like"}
    for key in ("shape", "columns", "dtypes", "null_counts"):
        value = preview.get(key)
        if value:
            compacted[key] = value

    head = preview.get("head")
    if isinstance(head, list) and head:
        compacted["head"] = head[:_HEAD_ROW_LIMIT]

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

    if depth >= 2:
        text = summarize_history_text(repr(value), limit=80)
        return text if text is not None else repr(value)

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

    text = summarize_history_text(repr(value), limit=80)
    return text if text is not None else repr(value)
