from __future__ import annotations

import json as _json
from collections.abc import Mapping
from typing import Any, cast

from .command_data import (
    CommandData,
    CommandDataLike,
    ExecCommandData,
    HistoryCommandData,
    InspectCommandData,
    KernelSessionData,
    ReloadCommandData,
    RunListEntryData,
    RunLookupCommandData,
    RunsListCommandData,
    RunSnapshotData,
    SerializedCommandData,
    VarsCommandData,
)
from .compact import compact_preview, preview_text, strip_ansi_lines, summarize_exec_label
from .execution_output import preview_from_result_text
from .journal import JournalEntry
from .payloads import (
    ExecPayload,
    HistoryEntryPayload,
    HistoryPayload,
    InspectPayload,
    InspectPreview,
    RunListEntryPayload,
    RunLookupPayload,
    RunSnapshot,
)

_SENTINEL = object()
_RESULT_LIMIT = 240
_STDOUT_LIMIT = 200
_MEMBER_LIMIT = 20
_HEAD_ROW_LIMIT = 3
_HEAD_COLUMN_LIMIT = 10
_PREVIEW_LIST_LIMIT = 3
_PREVIEW_DICT_LIMIT = 5


def serialize_command_data(command_name: str, data: CommandDataLike) -> dict[str, Any]:
    if isinstance(data, SerializedCommandData):
        return _mapping_to_dict(data.payload)
    if not isinstance(data, CommandData):
        return _mapping_to_dict(data)
    if isinstance(data, KernelSessionData):
        return _serialize_kernel_session_data(data)
    if isinstance(data, ExecCommandData):
        return _serialize_exec_data(data)
    if isinstance(data, RunsListCommandData):
        return {"runs": [_serialize_run_list_entry(item) for item in data.runs]}
    if isinstance(data, RunLookupCommandData):
        payload: RunLookupPayload = {"run": _serialize_run_snapshot(data.run)}
        if data.status is not None:
            payload["status"] = data.status
        if data.completion_reason is not None:
            payload["completion_reason"] = data.completion_reason
        if data.replayed_event_count is not None:
            payload["replayed_event_count"] = data.replayed_event_count
        if data.emitted_event_count is not None:
            payload["emitted_event_count"] = data.emitted_event_count
        return _with_switched_session(payload, data)
    if isinstance(data, VarsCommandData):
        payload = data.access_metadata.merge_data({"vars": list(data.values)})
        return _with_switched_session(payload, data)
    if isinstance(data, InspectCommandData):
        payload = data.access_metadata.merge_data(
            {"inspect": compact_inspect_payload(data.payload)}
        )
        return _with_switched_session(payload, data)
    if isinstance(data, ReloadCommandData):
        payload = data.access_metadata.merge_data(_mapping_to_dict(data.payload))
        return _with_switched_session(payload, data)
    if isinstance(data, HistoryCommandData):
        entries = [
            full_history_entry(entry) if data.full else compact_history_entry(entry)
            for entry in data.entries
        ]
        payload: HistoryPayload = {"entries": entries}
        return _with_switched_session(payload, data)
    raise ValueError(f"Unsupported command data type for {command_name}: {type(data).__name__}")


def project_agent_data(command_name: str, data: CommandDataLike) -> dict[str, Any]:
    serialized = serialize_command_data(command_name, data)
    if command_name in {"start", "status", "wait"}:
        return _subset(
            serialized,
            "alive",
            "pid",
            "busy",
            "lock_pid",
            "lock_acquired_at",
            "busy_for_ms",
            "runtime_state",
            "started_new",
            "waited",
            "waited_for",
            "waited_ms",
            "initial_runtime_state",
        )
    if command_name in {"stop", "interrupt"}:
        return dict(serialized)
    if command_name in {"exec", "reset"}:
        compacted = _subset(
            serialized,
            "status",
            "execution_id",
            "duration_ms",
            "background",
            "ensured_started",
            "started_new_session",
            "initial_runtime_state",
            "session_restarted",
            "session_python",
            "source_kind",
            "source_path",
            "namespace_delta",
            "wait_behavior",
            "waited_ms",
            "lock_pid",
            "lock_acquired_at",
            "busy_for_ms",
            "active_execution_id",
            "stdout_truncated",
            "stderr_truncated",
            "result_truncated",
        )
        for key in ("result", "stdout", "stderr", "selected_output", "selected_text"):
            value = serialized.get(key)
            if isinstance(value, str) and value:
                compacted[key] = value
        result_preview = serialized.get("result_preview")
        if isinstance(result_preview, dict):
            compacted["result_preview"] = dict(result_preview)
        result = compacted.get("result")
        if isinstance(result, str):
            parsed = _try_parse_result_json(result)
            if parsed is not _SENTINEL:
                compacted["result_json"] = parsed
        return compacted
    if command_name in {"history", "runs-list", "sessions-list"}:
        return dict(serialized)
    if command_name == "runs-show":
        return _project_run_lookup_agent(serialized, include_completion=False)
    if command_name == "runs-follow":
        return _project_run_lookup_agent(serialized, include_completion=True)
    if command_name == "runs-wait":
        return _project_runs_wait_agent(serialized)
    if command_name == "runs-cancel":
        return _subset(
            serialized,
            "execution_id",
            "session_id",
            "cancel_requested",
            "status",
            "run_status",
            "session_outcome",
        )
    return dict(serialized)


def selected_exec_output(payload: Mapping[str, object], selector: str) -> str:
    if selector == "result":
        preview = payload.get("result_preview")
        result = payload.get("result")
        if isinstance(preview, dict) and _prefer_preview_text(preview, result):
            return preview_text(cast(InspectPreview, preview))
        return "" if result is None else str(result)
    value = payload.get(selector)
    return "" if value is None else str(value)


def compact_execution_payload(
    data: ExecCommandData,
) -> ExecPayload:
    outcome = data.record.outcome()
    compacted: ExecPayload = {
        "duration_ms": data.record.duration_ms,
        "status": data.record.status,
        "execution_id": data.record.execution_id,
    }

    execution_count = outcome.execution_count
    if execution_count is not None:
        compacted["execution_count"] = execution_count

    stdout = outcome.stdout
    if stdout:
        if data.no_truncate:
            compacted["stdout"] = stdout
        else:
            summary = summarize_history_text(stdout, limit=_STDOUT_LIMIT)
            if summary is not None:
                if len(stdout) > _STDOUT_LIMIT:
                    compacted["stdout_truncated"] = True
                    summary = summary + f" [{len(stdout) - _STDOUT_LIMIT} chars truncated]"
                compacted["stdout"] = summary

    stderr = outcome.stderr
    if stderr:
        if data.no_truncate:
            compacted["stderr"] = stderr
        else:
            summary = summarize_history_text(stderr, limit=_STDOUT_LIMIT)
            if summary is not None:
                if len(stderr) > _STDOUT_LIMIT:
                    compacted["stderr_truncated"] = True
                    summary = summary + f" [{len(stderr) - _STDOUT_LIMIT} chars truncated]"
                compacted["stderr"] = summary

    result = outcome.result
    if isinstance(result, str) and result:
        if data.no_truncate:
            compacted["result"] = result
        else:
            summary = summarize_history_text(result, limit=_RESULT_LIMIT)
            if summary is not None:
                if len(result) > _RESULT_LIMIT:
                    compacted["result_truncated"] = True
                compacted["result"] = summary

    result_preview = compact_result_preview(
        result_preview=outcome.result_preview,
        result=result,
    )
    if result_preview is not None:
        compacted["result_preview"] = result_preview

    if isinstance(outcome.ename, str):
        compacted["ename"] = outcome.ename
    if isinstance(outcome.evalue, str):
        compacted["evalue"] = outcome.evalue

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
    if entry.error_type is not None:
        compacted["error_type"] = entry.error_type
    if entry.execution_id is not None:
        compacted["execution_id"] = entry.execution_id
    if entry.user_visible and entry.code is not None:
        summary = summarize_history_multiline(entry.code, limit=_RESULT_LIMIT)
        if summary is not None:
            compacted["code"] = summary
    return compacted


def compact_run_entry(data: RunListEntryData) -> RunListEntryPayload:
    return _serialize_run_list_entry(data)


def serialize_run_lookup_payload(data: RunLookupCommandData) -> RunLookupPayload:
    return cast(RunLookupPayload, serialize_command_data("runs-show", data))


def _serialize_kernel_session_data(data: KernelSessionData) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "alive": data.alive,
        "pid": data.pid,
        "connection_file": data.connection_file,
        "started_at": data.started_at,
        "uptime_s": data.uptime_s,
        "python": data.python,
        "busy": data.busy,
    }
    if data.runtime_state is not None:
        payload["runtime_state"] = data.runtime_state
    if data.session_exists is not None:
        payload["session_exists"] = data.session_exists
    if data.lock_pid is not None:
        payload["lock_pid"] = data.lock_pid
    if data.lock_acquired_at is not None:
        payload["lock_acquired_at"] = data.lock_acquired_at
    if data.busy_for_ms is not None:
        payload["busy_for_ms"] = data.busy_for_ms
    if data.waited is not None:
        payload["waited"] = data.waited
    if data.waited_for is not None:
        payload["waited_for"] = data.waited_for
    if data.waited_ms is not None:
        payload["waited_ms"] = data.waited_ms
    if data.initial_runtime_state is not None:
        payload["initial_runtime_state"] = data.initial_runtime_state
    if data.started_new is not None:
        payload["started_new"] = data.started_new
    return _with_switched_session(payload, data)


def _serialize_exec_data(data: ExecCommandData) -> dict[str, Any]:
    payload = dict(compact_execution_payload(data))
    if data.source_kind is not None:
        payload["source_kind"] = data.source_kind
    if data.source_path is not None:
        payload["source_path"] = data.source_path
    if data.background:
        payload["background"] = True
    if data.ensured_started:
        payload["ensured_started"] = True
        payload["started_new_session"] = data.started_new_session
        if data.initial_runtime_state is not None:
            payload["initial_runtime_state"] = data.initial_runtime_state
        if data.session_restarted:
            payload["session_restarted"] = True
    if data.selected_output is not None:
        payload["selected_output"] = data.selected_output
        payload["selected_text"] = data.selected_text or ""
    if data.session_python is not None:
        payload["session_python"] = data.session_python
    if data.namespace_delta is not None:
        payload["namespace_delta"] = data.namespace_delta
    return _with_switched_session(payload, data)


def _serialize_run_snapshot(data: RunSnapshotData) -> RunSnapshot:
    raw_payload = _mapping_to_dict(data.payload)
    hidden_keys = {"outputs"}
    if not data.include_output:
        hidden_keys.update({"stdout", "stderr", "result", "events"})
    payload = {key: value for key, value in raw_payload.items() if key not in hidden_keys}
    if data.snapshot_stale or payload.get("status") in {"starting", "running"}:
        payload["snapshot_stale"] = True
    for key in ("traceback", "recorded_traceback"):
        value = payload.get(key)
        if isinstance(value, list):
            payload[key] = strip_ansi_lines(cast(list[str], value))
    events = payload.get("events")
    if isinstance(events, list):
        sanitized_events: list[dict[str, object]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            sanitized_event = dict(event)
            metadata = sanitized_event.get("metadata")
            if isinstance(metadata, dict):
                sanitized_metadata = dict(metadata)
                traceback = sanitized_metadata.get("traceback")
                if isinstance(traceback, list):
                    sanitized_metadata["traceback"] = strip_ansi_lines(cast(list[str], traceback))
                sanitized_event["metadata"] = sanitized_metadata
            sanitized_events.append(sanitized_event)
        payload["events"] = sanitized_events
    payload = _with_switched_session(payload, data)
    return cast(RunSnapshot, payload)


def _serialize_run_list_entry(data: RunListEntryData) -> RunListEntryPayload:
    payload = data.payload
    compacted: RunListEntryPayload = {
        "execution_id": cast(str | None, payload.get("execution_id")),
        "ts": cast(str | None, payload.get("ts")),
        "session_id": cast(str | None, payload.get("session_id")),
        "command_type": cast(str | None, payload.get("command_type")),
        "status": cast(str | None, payload.get("status")),
        "duration_ms": cast(int | None, payload.get("duration_ms")),
        "cancel_requested": bool(payload.get("cancel_requested")),
    }
    terminal_reason = payload.get("terminal_reason")
    if terminal_reason is not None:
        compacted["terminal_reason"] = cast(str, terminal_reason)

    result_preview = compact_result_preview(
        result_preview=payload.get("result_preview"),
        result=payload.get("result"),
    )
    if result_preview is not None:
        compacted["result_preview"] = result_preview
    else:
        result = payload.get("result")
        summary = summarize_history_text(result, limit=_RESULT_LIMIT)
        if summary is not None:
            compacted["result_preview"] = summary

    stdout = payload.get("stdout")
    if isinstance(stdout, str) and stdout:
        summary = summarize_history_text(stdout, limit=_STDOUT_LIMIT)
        if summary is not None:
            compacted["stdout_preview"] = summary

    ename = payload.get("ename")
    if isinstance(ename, str) and ename:
        compacted["error_type"] = ename

    return cast(RunListEntryPayload, _with_switched_session(compacted, data))


def _project_run_lookup_agent(
    serialized: Mapping[str, object],
    *,
    include_completion: bool,
) -> dict[str, Any]:
    run = serialized.get("run")
    if not isinstance(run, dict):
        return {}
    projected: dict[str, object] = {
        "run": _serialize_run_snapshot_for_agent(cast(RunSnapshot, run))
    }
    status = serialized.get("status")
    if isinstance(status, str):
        projected["status"] = status
    if include_completion:
        completion_reason = serialized.get("completion_reason")
        if isinstance(completion_reason, str):
            projected["completion_reason"] = completion_reason
        for key in ("replayed_event_count", "emitted_event_count"):
            value = serialized.get(key)
            if isinstance(value, int):
                projected[key] = value
    return projected


def _project_runs_wait_agent(serialized: Mapping[str, object]) -> dict[str, Any]:
    run = serialized.get("run")
    if not isinstance(run, dict):
        return {}
    projected: dict[str, object] = {
        "run": _serialize_run_snapshot_for_agent(cast(RunSnapshot, run))
    }
    status = serialized.get("status")
    if isinstance(status, str):
        projected["status"] = status
    return projected


def _serialize_run_snapshot_for_agent(run: RunSnapshot) -> dict[str, object]:
    payload: dict[str, object] = {
        "execution_id": run.get("execution_id"),
        "ts": run.get("ts"),
        "session_id": run.get("session_id"),
        "command_type": run.get("command_type"),
        "status": run.get("status"),
        "duration_ms": run.get("duration_ms"),
    }
    terminal_reason = run.get("terminal_reason")
    if terminal_reason is not None:
        payload["terminal_reason"] = terminal_reason
    if "cancel_requested" in run:
        payload["cancel_requested"] = bool(run.get("cancel_requested"))

    result_preview = run.get("result_preview")
    result = run.get("result")
    if isinstance(result_preview, dict):
        payload["result_preview"] = dict(result_preview)
    elif isinstance(result, str) and result:
        summary = summarize_history_text(result, limit=_RESULT_LIMIT)
        if summary is not None:
            payload["result_preview"] = summary

    stdout = run.get("stdout")
    if isinstance(stdout, str) and stdout:
        summary = summarize_history_text(stdout, limit=_STDOUT_LIMIT)
        if summary is not None:
            payload["stdout_preview"] = summary

    ename = run.get("ename")
    if isinstance(ename, str) and ename:
        payload["error_type"] = ename
    return payload


def _with_switched_session(
    payload: Mapping[str, object],
    data: CommandData,
) -> dict[str, Any]:
    serialized = _mapping_to_dict(payload)
    if data.switched_session is not None:
        serialized["switched_session"] = data.switched_session
    return serialized


def _subset(data: Mapping[str, object], *keys: str) -> dict[str, Any]:
    return {key: value for key in keys if key in data for value in [data[key]] if value is not None}


def _try_parse_result_json(result: str) -> Any:
    try:
        return _json.loads(result)
    except (ValueError, _json.JSONDecodeError):
        pass
    if len(result) >= 2 and result[0] in ("'", '"') and result[-1] == result[0]:
        inner = result[1:-1]
        try:
            return _json.loads(inner)
        except (ValueError, _json.JSONDecodeError):
            pass
    return _SENTINEL


def _prefer_preview_text(preview: object, result: object) -> bool:
    if isinstance(preview, dict):
        preview_map = cast(dict[str, object], preview)
        if preview_map.get("kind") == "dataframe-like":
            return True
    if not isinstance(result, str):
        return True
    return "\n" in result or len(result) > 120


def summarize_history_text(value: object, limit: int = 100) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.strip().split())
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def summarize_history_multiline(value: object, limit: int = 100) -> str | None:
    if not isinstance(value, str):
        return None
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return None
    compact = " | ".join(lines)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _mapping_to_dict(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items()}


__all__ = [
    "compact_execution_payload",
    "compact_history_entry",
    "compact_inspect_payload",
    "compact_result_preview",
    "compact_run_entry",
    "full_history_entry",
    "project_agent_data",
    "selected_exec_output",
    "serialize_command_data",
    "serialize_run_lookup_payload",
]
