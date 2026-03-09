from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from .history import summarize_history_text

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


def compact_execution_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {
        "status": payload.get("status"),
        "duration_ms": payload.get("duration_ms", 0),
    }

    execution_count = payload.get("execution_count")
    if execution_count is not None:
        compacted["execution_count"] = execution_count

    for key in ("stdout", "stderr", "result"):
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            continue
        limit = _STDOUT_LIMIT if key in {"stdout", "stderr"} else _RESULT_LIMIT
        compacted[key] = summarize_history_text(value, limit=limit)

    for key in ("ename", "evalue"):
        value = payload.get(key)
        if value is not None:
            compacted[key] = value

    selected_output = payload.get("selected_output")
    if selected_output is not None:
        compacted["selected_output"] = selected_output
        compacted["selected_text"] = payload.get("selected_text", "")

    return compacted


def compact_inspect_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {
        "name": payload.get("name"),
        "type": payload.get("type"),
    }
    preview = payload.get("preview")
    if isinstance(preview, dict):
        if preview.get("kind") == "dataframe-like":
            compacted["preview"] = compact_dataframe_preview(preview)
            return compacted
        if preview.get("kind") in {"sequence-like", "mapping-like"}:
            compacted["preview"] = compact_collection_preview(preview)
            return compacted

    repr_text = payload.get("repr")
    if isinstance(repr_text, str) and repr_text:
        compacted["repr"] = summarize_history_text(repr_text, limit=_RESULT_LIMIT)

    members = payload.get("members")
    if isinstance(members, list) and members:
        compacted["members"] = [str(member) for member in members[:_MEMBER_LIMIT]]

    return compacted


def compact_dataframe_preview(preview: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {"kind": "dataframe-like"}
    for key in ("shape", "columns", "dtypes", "null_counts"):
        value = preview.get(key)
        if value:
            compacted[key] = value

    head = preview.get("head")
    if isinstance(head, list) and head:
        compacted["head"] = head[:_HEAD_ROW_LIMIT]

    return compacted


def compact_collection_preview(preview: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {"kind": preview.get("kind")}

    length = preview.get("length")
    if isinstance(length, int):
        compacted["length"] = length

    item_type = preview.get("item_type")
    if isinstance(item_type, str) and item_type:
        compacted["item_type"] = item_type

    for key in ("keys", "sample_keys"):
        value = preview.get(key)
        if isinstance(value, list) and value:
            compacted[key] = [str(item) for item in value[:_PREVIEW_DICT_LIMIT]]

    sample = preview.get("sample")
    if sample is not None:
        compacted["sample"] = _compact_jsonish(sample)

    return compacted


def compact_history_entry(entry: dict[str, Any]) -> dict[str, Any]:
    label = entry.get("label")
    command_type = entry.get("command_type")
    if command_type == "exec":
        if entry.get("status") == "error":
            error_type = entry.get("error_type")
            label = "exec error" if error_type is None else f"exec error {error_type}"
        else:
            preview = summarize_exec_label(str(entry.get("code") or entry.get("input") or ""))
            label = "exec" if preview is None else f"exec {preview}"

    compacted: dict[str, Any] = {
        "kind": entry.get("kind"),
        "ts": entry.get("ts"),
        "status": entry.get("status"),
        "duration_ms": entry.get("duration_ms"),
        "command_type": command_type,
        "label": label,
        "user_visible": entry.get("user_visible"),
    }
    error_type = entry.get("error_type")
    if error_type is not None:
        compacted["error_type"] = error_type
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
