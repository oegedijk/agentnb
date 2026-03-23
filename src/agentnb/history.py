from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast

from .contracts import ExecutionResult, utc_now_iso
from .execution_output import compatibility_output
from .session import DEFAULT_SESSION_ID
from .state import StateRepository

HistoryKind = Literal["user_command", "kernel_execution"]
FailureOrigin = Literal["kernel", "control"]

_PREVIEW_LIMIT = 160


class HistoryRecordPayload(TypedDict, total=False):
    kind: HistoryKind
    ts: str
    session_id: str
    execution_id: str
    status: str
    duration_ms: int
    command_type: str
    label: str
    user_visible: bool
    input: str
    code: str
    origin: str
    error_type: str
    failure_origin: FailureOrigin
    result_preview: str
    stdout_preview: str


@dataclass(slots=True)
class HistoryRecord:
    kind: HistoryKind
    ts: str
    session_id: str
    execution_id: str | None
    status: str
    duration_ms: int
    command_type: str
    label: str
    user_visible: bool
    input: str | None = None
    code: str | None = None
    origin: str | None = None
    error_type: str | None = None
    failure_origin: FailureOrigin | None = None
    result_preview: str | None = None
    stdout_preview: str | None = None

    def to_dict(self) -> HistoryRecordPayload:
        payload: HistoryRecordPayload = {
            "kind": self.kind,
            "ts": self.ts,
            "session_id": self.session_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "command_type": self.command_type,
            "label": self.label,
            "user_visible": self.user_visible,
        }
        if self.execution_id is not None:
            payload["execution_id"] = self.execution_id
        if self.input is not None:
            payload["input"] = self.input
        if self.code is not None:
            payload["code"] = self.code
        if self.origin is not None:
            payload["origin"] = self.origin
        if self.error_type is not None:
            payload["error_type"] = self.error_type
        if self.failure_origin is not None:
            payload["failure_origin"] = self.failure_origin
        if self.result_preview is not None:
            payload["result_preview"] = self.result_preview
        if self.stdout_preview is not None:
            payload["stdout_preview"] = self.stdout_preview
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> HistoryRecord:
        return cls(
            kind=cast(
                HistoryKind,
                _require_literal(payload, "kind", {"user_command", "kernel_execution"}),
            ),
            ts=_require_str(payload, "ts"),
            session_id=_require_str(payload, "session_id"),
            execution_id=_optional_str(payload, "execution_id"),
            status=_require_str(payload, "status"),
            duration_ms=_require_int(payload, "duration_ms"),
            command_type=_require_str(payload, "command_type"),
            label=_require_str(payload, "label"),
            user_visible=_require_bool(payload, "user_visible"),
            input=_optional_str(payload, "input"),
            code=_optional_str(payload, "code"),
            origin=_optional_str(payload, "origin"),
            error_type=_optional_str(payload, "error_type"),
            failure_origin=_optional_failure_origin(payload, "failure_origin"),
            result_preview=_optional_str(payload, "result_preview"),
            stdout_preview=_optional_str(payload, "stdout_preview"),
        )


class HistoryStore:
    def __init__(self, project_root: Path, session_id: str | None = DEFAULT_SESSION_ID) -> None:
        self.repository = StateRepository(project_root)
        self.project_root = self.repository.project_root
        self.session_id = session_id
        self.state_dir = self.repository.state_dir
        self.history_file = self.repository.history_file

    def append(self, record: HistoryRecord) -> None:
        self.repository.ensure_compatible()
        self.repository.ensure_state_dir()
        with self.history_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=True))
            handle.write("\n")

    def read(
        self,
        *,
        include_internal: bool = False,
        errors_only: bool = False,
    ) -> list[HistoryRecord]:
        self.repository.ensure_compatible()
        if not self.history_file.exists():
            return []

        entries: list[HistoryRecord] = []
        for line in self.history_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, Mapping):
                    continue
                record = HistoryRecord.from_dict(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if self.session_id is not None and record.session_id != self.session_id:
                continue
            if not include_internal and not record.user_visible:
                continue
            if errors_only and record.status != "error":
                continue
            entries.append(record)
        return entries


def user_command_record(
    *,
    ts: str | None = None,
    session_id: str,
    execution_id: str | None = None,
    command_type: str,
    label: str,
    input_text: str | None = None,
    code: str | None = None,
    origin: str | None = None,
    execution: ExecutionResult | None = None,
    error: Exception | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
    error_type: str | None = None,
    failure_origin: FailureOrigin | None = None,
    stdout: str | None = None,
    result: str | None = None,
) -> HistoryRecord:
    resolved_status, resolved_duration, resolved_error_type, result_preview, stdout_preview = (
        _resolve_execution_metadata(
            execution=execution,
            error=error,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            stdout=stdout,
            result=result,
        )
    )
    return HistoryRecord(
        kind="user_command",
        ts=utc_now_iso() if ts is None else ts,
        session_id=session_id,
        execution_id=execution_id,
        status=resolved_status,
        duration_ms=resolved_duration,
        command_type=command_type,
        label=label,
        input=input_text,
        code=code,
        origin=origin,
        user_visible=True,
        error_type=resolved_error_type,
        failure_origin=_resolved_failure_origin(
            execution=execution,
            error=error,
            status=resolved_status,
            failure_origin=failure_origin,
        ),
        result_preview=result_preview,
        stdout_preview=stdout_preview,
    )


def kernel_execution_record(
    *,
    ts: str | None = None,
    session_id: str,
    execution_id: str | None = None,
    command_type: str,
    label: str,
    code: str | None,
    origin: str,
    execution: ExecutionResult | None = None,
    error: Exception | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
    error_type: str | None = None,
    failure_origin: FailureOrigin | None = None,
    stdout: str | None = None,
    result: str | None = None,
) -> HistoryRecord:
    resolved_status, resolved_duration, resolved_error_type, result_preview, stdout_preview = (
        _resolve_execution_metadata(
            execution=execution,
            error=error,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            stdout=stdout,
            result=result,
        )
    )
    return HistoryRecord(
        kind="kernel_execution",
        ts=utc_now_iso() if ts is None else ts,
        session_id=session_id,
        execution_id=execution_id,
        status=resolved_status,
        duration_ms=resolved_duration,
        command_type=command_type,
        label=label,
        code=code,
        origin=origin,
        user_visible=False,
        error_type=resolved_error_type,
        failure_origin=_resolved_failure_origin(
            execution=execution,
            error=error,
            status=resolved_status,
            failure_origin=failure_origin,
        ),
        result_preview=result_preview,
        stdout_preview=stdout_preview,
    )


def summarize_history_text(value: str | None, limit: int = _PREVIEW_LIMIT) -> str | None:
    if value is None:
        return None
    compact = " ".join(value.strip().split())
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def summarize_history_lines_inline(
    value: str | None,
    *,
    limit: int = _PREVIEW_LIMIT,
    separator: str = " | ",
) -> str | None:
    lines = _normalized_history_lines(value)
    if not lines:
        return None
    compact = separator.join(lines)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def summarize_history_multiline(
    value: str | None,
    *,
    limit: int = _PREVIEW_LIMIT,
    max_lines: int = 3,
) -> str | None:
    lines = _normalized_history_lines(value)
    if not lines:
        return None
    preview_lines = lines[:max_lines]
    compact = "\n".join(preview_lines)
    if len(lines) > max_lines:
        compact = f"{compact}\n..."
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip("\n") + "..."


def _resolve_execution_metadata(
    *,
    execution: ExecutionResult | None,
    error: Exception | None,
    status: str | None,
    duration_ms: int | None,
    error_type: str | None,
    stdout: str | None,
    result: str | None,
) -> tuple[str, int, str | None, str | None, str | None]:
    resolved_status = status
    resolved_duration = 0 if duration_ms is None else duration_ms
    resolved_error_type = error_type
    resolved_stdout = stdout
    resolved_result = result

    if execution is not None:
        projected = compatibility_output(execution_output_from_execution_result(execution))
        if resolved_status is None:
            resolved_status = execution.status
        if duration_ms is None:
            resolved_duration = execution.duration_ms
        if resolved_error_type is None:
            resolved_error_type = projected.ename
        if resolved_stdout is None:
            resolved_stdout = projected.stdout
        if resolved_result is None:
            resolved_result = projected.result

    if error is not None:
        if resolved_status is None:
            resolved_status = "error"
        if resolved_error_type is None:
            resolved_error_type = type(error).__name__

    if resolved_status is None:
        resolved_status = "ok"

    return (
        resolved_status,
        resolved_duration,
        resolved_error_type,
        summarize_history_text(resolved_result),
        summarize_history_text(resolved_stdout),
    )


def _normalized_history_lines(value: str | None) -> list[str]:
    if value is None:
        return []
    return [" ".join(line.split()) for line in value.strip().splitlines() if line.strip()]


def execution_output_from_execution_result(execution: ExecutionResult):
    from .execution_output import ExecutionOutput

    return ExecutionOutput(
        items=list(execution.outputs),
        execution_count=execution.execution_count,
    )


def _require_literal(payload: Mapping[str, object], key: str, allowed: set[str]) -> str:
    value = _require_str(payload, key)
    if value not in allowed:
        raise ValueError(f"Invalid {key}: {value}")
    return value


def _require_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing {key}")
    return value


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid {key}")
    return value


def _require_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Invalid {key}")
    return value


def _require_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Invalid {key}")
    return value


def _optional_failure_origin(payload: Mapping[str, object], key: str) -> FailureOrigin | None:
    value = payload.get(key)
    if value is None:
        return None
    if value not in {"kernel", "control"}:
        raise ValueError(f"Invalid {key}")
    return cast(FailureOrigin, value)


def _resolved_failure_origin(
    *,
    execution: ExecutionResult | None,
    error: Exception | None,
    status: str,
    failure_origin: FailureOrigin | None,
) -> FailureOrigin | None:
    if failure_origin is not None:
        return failure_origin
    if status != "error":
        return None
    if execution is not None or error is not None:
        return "kernel"
    return None
