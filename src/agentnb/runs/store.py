from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

from ..contracts import ExecutionEvent, ExecutionResult, ExecutionSink, utc_now_iso
from ..errors import AgentNBException
from ..execution_output import (
    ExecutionOutput,
    OutputItem,
    compatibility_output,
    execution_output_from_events,
    execution_output_from_legacy_fields,
)
from ..history import HistoryRecord
from ..payloads import ExecutionEventPayload, RunSnapshot, StoredRunSnapshot
from ..recording import CommandRecorder, CommandRecording
from ..state import StateRepository

TerminalReason = Literal["completed", "failed", "cancelled", "worker_exited"]

_CANCELLED_ERROR_TYPE = "CancelledError"
_CANCELLED_ERROR_VALUE = "Run was cancelled by user."
_CANCELLATION_RAW_ERROR_TYPES = frozenset(
    {
        "KeyboardInterrupt",
        _CANCELLED_ERROR_TYPE,
        "WorkerExitedError",
    }
)


@dataclass(slots=True)
class ExecutionRecord:
    execution_id: str
    ts: str
    session_id: str
    command_type: str
    status: str
    duration_ms: int
    code: str | None = None
    worker_pid: int | None = None
    stdout: str = ""
    stderr: str = ""
    result: str | None = None
    execution_count: int | None = None
    ename: str | None = None
    evalue: str | None = None
    traceback: list[str] | None = None
    outputs: list[OutputItem] = field(default_factory=list)
    events: list[ExecutionEvent] = field(default_factory=list)
    journal_entries: list[HistoryRecord] = field(default_factory=list)
    terminal_reason: TerminalReason | None = None
    cancel_requested: bool = False
    cancel_requested_at: str | None = None
    cancel_request_source: str | None = None
    recorded_status: str | None = None
    recorded_ename: str | None = None
    recorded_evalue: str | None = None
    recorded_traceback: list[str] | None = None

    def __post_init__(self) -> None:
        if self.outputs:
            output = ExecutionOutput(
                items=list(self.outputs),
                execution_count=self.execution_count,
            )
        elif self.events:
            output = execution_output_from_events(self.events, execution_count=self.execution_count)
        else:
            output = execution_output_from_legacy_fields(
                stdout=self.stdout,
                stderr=self.stderr,
                result=self.result,
                ename=self.ename,
                evalue=self.evalue,
                traceback=self.traceback,
                status="error" if self.status == "error" else "ok",
                execution_count=self.execution_count,
            )

        if not self.outputs:
            self.outputs = list(output.items)
        if not self.events:
            self.events = output.to_events()

        projected = compatibility_output(output)
        self.stdout = projected.stdout
        self.stderr = projected.stderr
        self.result = projected.result

        explicit_error = (
            self.status == "error"
            or self.ename is not None
            or self.evalue is not None
            or self.traceback is not None
        )
        if projected.status == "error":
            self.status = "error"
            self.ename = projected.ename
            self.evalue = projected.evalue
            self.traceback = projected.traceback
        elif explicit_error:
            self.status = "error"
        else:
            self.ename = projected.ename
            self.evalue = projected.evalue
            self.traceback = projected.traceback

        self._apply_terminal_projection()

    def to_dict(self) -> RunSnapshot:
        payload: RunSnapshot = {
            "execution_id": self.execution_id,
            "ts": self.ts,
            "session_id": self.session_id,
            "command_type": self.command_type,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "code": self.code,
            "worker_pid": self.worker_pid,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "result": self.result,
            "execution_count": self.execution_count,
            "ename": self.ename,
            "evalue": self.evalue,
            "traceback": self.traceback,
            "terminal_reason": self.terminal_reason,
            "cancel_requested": self.cancel_requested,
            "cancel_requested_at": self.cancel_requested_at,
            "cancel_request_source": self.cancel_request_source,
            "recorded_status": self.recorded_status,
            "recorded_ename": self.recorded_ename,
            "recorded_evalue": self.recorded_evalue,
            "recorded_traceback": self.recorded_traceback,
            "events": [
                ExecutionEventPayload(
                    kind=event.kind,
                    content=event.content,
                    metadata=event.metadata,
                )
                for event in self.events
            ],
        }
        return payload

    def to_storage_dict(self) -> StoredRunSnapshot:
        payload: StoredRunSnapshot = {
            **self.to_dict(),
            "outputs": [item.to_dict() for item in self.outputs],
            "journal_entries": cast(
                list[dict[str, object]],
                [entry.to_dict() for entry in self.journal_entries],
            ),
        }
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutionRecord:
        raw_outputs = payload.get("outputs", [])
        outputs: list[OutputItem] = []
        if isinstance(raw_outputs, list):
            for raw_output in raw_outputs:
                if not isinstance(raw_output, dict):
                    continue
                item = OutputItem.from_dict(raw_output)
                if item is not None:
                    outputs.append(item)
        raw_events = payload.get("events", [])
        events: list[ExecutionEvent] = []
        if isinstance(raw_events, list):
            for raw_event in raw_events:
                if not isinstance(raw_event, dict):
                    continue
                kind = raw_event.get("kind")
                if not isinstance(kind, str):
                    continue
                content = raw_event.get("content")
                metadata = raw_event.get("metadata", {})
                if content is not None and not isinstance(content, str):
                    content = str(content)
                if not isinstance(metadata, dict):
                    metadata = {}
                events.append(ExecutionEvent(kind=kind, content=content, metadata=metadata))
        raw_journal_entries = payload.get("journal_entries", [])
        journal_entries: list[HistoryRecord] = []
        if isinstance(raw_journal_entries, list):
            for raw_entry in raw_journal_entries:
                if not isinstance(raw_entry, dict):
                    continue
                try:
                    journal_entries.append(HistoryRecord.from_dict(raw_entry))
                except (TypeError, ValueError):
                    continue

        return cls(
            execution_id=_require_str(payload, "execution_id"),
            ts=_require_str(payload, "ts"),
            session_id=_require_str(payload, "session_id"),
            command_type=_require_str(payload, "command_type"),
            status=_require_str(payload, "status"),
            duration_ms=_require_int(payload, "duration_ms"),
            code=_optional_str(payload, "code"),
            worker_pid=_optional_int(payload, "worker_pid"),
            stdout=_optional_str(payload, "stdout") or "",
            stderr=_optional_str(payload, "stderr") or "",
            result=_optional_str(payload, "result"),
            execution_count=_optional_int(payload, "execution_count"),
            ename=_optional_str(payload, "ename"),
            evalue=_optional_str(payload, "evalue"),
            traceback=_optional_str_list(payload, "traceback"),
            outputs=outputs,
            events=events,
            journal_entries=journal_entries,
            terminal_reason=_optional_terminal_reason(payload, "terminal_reason"),
            cancel_requested=_optional_bool(payload, "cancel_requested") or False,
            cancel_requested_at=_optional_str(payload, "cancel_requested_at"),
            cancel_request_source=_optional_str(payload, "cancel_request_source"),
            recorded_status=_optional_str(payload, "recorded_status"),
            recorded_ename=_optional_str(payload, "recorded_ename"),
            recorded_evalue=_optional_str(payload, "recorded_evalue"),
            recorded_traceback=_optional_str_list(payload, "recorded_traceback"),
        )

    def to_execution_payload(self) -> RunSnapshot:
        return self.to_dict()

    def with_cancel_requested(
        self,
        *,
        requested_at: str,
        source: str,
    ) -> ExecutionRecord:
        if self.cancel_requested:
            return self
        return replace(
            self,
            cancel_requested=True,
            cancel_requested_at=requested_at,
            cancel_request_source=source,
        )

    def with_terminal_reason(self, terminal_reason: TerminalReason) -> ExecutionRecord:
        return replace(self, terminal_reason=terminal_reason)

    def _apply_terminal_projection(self) -> None:
        if self.status == "running":
            return

        if self.terminal_reason is None:
            self.terminal_reason = self._infer_terminal_reason()

        if self.terminal_reason != "cancelled":
            return

        if not self._is_projected_cancelled():
            if self.recorded_status is None:
                self.recorded_status = self.status
            if self.recorded_ename is None:
                self.recorded_ename = self.ename
            if self.recorded_evalue is None:
                self.recorded_evalue = self.evalue
            if self.recorded_traceback is None and self.traceback is not None:
                self.recorded_traceback = list(self.traceback)

        self.status = "error"
        self.ename = _CANCELLED_ERROR_TYPE
        self.evalue = _CANCELLED_ERROR_VALUE
        self.traceback = None
        self.journal_entries = [
            replace(entry, status="error", error_type=_CANCELLED_ERROR_TYPE)
            if entry.status == "error"
            else entry
            for entry in self.journal_entries
        ]

    def _infer_terminal_reason(self) -> TerminalReason:
        if (
            self.cancel_requested
            and self.status == "error"
            and self.ename in _CANCELLATION_RAW_ERROR_TYPES
        ):
            return "cancelled"
        if self.status == "ok":
            return "completed"
        if self.status == "error" and self.ename == "WorkerExitedError":
            return "worker_exited"
        return "failed"

    def _is_projected_cancelled(self) -> bool:
        return (
            self.status == "error"
            and self.ename == _CANCELLED_ERROR_TYPE
            and self.evalue == _CANCELLED_ERROR_VALUE
            and self.traceback is None
        )


class ExecutionStore:
    def __init__(self, project_root: Path) -> None:
        self.repository = StateRepository(project_root)
        self.project_root = self.repository.project_root
        self.state_dir = self.repository.state_dir
        self.executions_file = self.repository.executions_file

    def append(self, record: ExecutionRecord) -> None:
        self.repository.ensure_compatible()
        self.repository.ensure_state_dir()
        with self.executions_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_storage_dict(), ensure_ascii=True))
            handle.write("\n")

    def read(
        self,
        *,
        session_id: str | None = None,
        command_types: set[str] | None = None,
        errors_only: bool = False,
    ) -> list[ExecutionRecord]:
        self.repository.ensure_compatible()
        if not self.executions_file.exists():
            return []

        entries_by_id: dict[str, ExecutionRecord] = {}
        for line in self.executions_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = ExecutionRecord.from_dict(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if session_id is not None and record.session_id != session_id:
                continue
            if command_types is not None and record.command_type not in command_types:
                continue
            previous = entries_by_id.get(record.execution_id)
            if previous is not None:
                record = _merge_records(previous, record)
            entries_by_id[record.execution_id] = record
        records = list(entries_by_id.values())
        if errors_only:
            records = [record for record in records if record.status == "error"]
        return records

    def get(self, execution_id: str) -> ExecutionRecord | None:
        for record in reversed(self.read()):
            if record.execution_id == execution_id:
                return record
        return None


@dataclass(slots=True)
class ManagedExecution:
    record: ExecutionRecord
    started_new_session: bool = False


@dataclass(slots=True)
class ExecutionRun:
    store: ExecutionStore
    record: ExecutionRecord
    recording: CommandRecording | None = None
    started: bool = False

    def start(self, sink: ExecutionSink | None = None) -> None:
        if self.started:
            return
        self.store.append(self.record)
        self.started = True
        if sink is not None:
            sink.started(
                execution_id=self.record.execution_id,
                session_id=self.record.session_id,
            )

    def replace(self, **changes: object) -> ExecutionRecord:
        updated = replace(self.record, **changes)
        self.store.append(updated)
        self.record = updated
        return updated

    def result_record(self, execution: ExecutionResult) -> ExecutionRecord:
        recording = self._recording()
        return replace(
            self.record,
            status=execution.status,
            duration_ms=execution.duration_ms,
            stdout=execution.stdout,
            stderr=execution.stderr,
            result=execution.result,
            execution_count=execution.execution_count,
            ename=execution.ename,
            evalue=execution.evalue,
            traceback=execution.traceback,
            outputs=list(execution.outputs),
            events=list(execution.events),
            journal_entries=recording.build_records(
                ts=self.record.ts,
                session_id=self.record.session_id,
                execution_id=self.record.execution_id,
                execution=execution,
            ),
        )

    def finalize_result(self, execution: ExecutionResult) -> ExecutionRecord:
        updated = self.result_record(execution)
        self.store.append(updated)
        self.record = updated
        return updated

    def error_record(self, error: Exception) -> ExecutionRecord:
        recording = self._recording()
        ename = type(error).__name__
        evalue = str(error)
        traceback = None
        if isinstance(error, AgentNBException):
            ename = error.ename or ename
            evalue = error.evalue or error.message
            traceback = error.traceback
        return replace(
            self.record,
            status="error",
            ename=ename,
            evalue=evalue,
            traceback=traceback,
            journal_entries=recording.build_records(
                ts=self.record.ts,
                session_id=self.record.session_id,
                execution_id=self.record.execution_id,
                error=error,
            ),
        )

    def finalize_error(self, error: Exception) -> ExecutionRecord:
        updated = self.error_record(error)
        self.store.append(updated)
        self.record = updated
        return updated

    def _recording(self) -> CommandRecording:
        if self.recording is not None:
            return self.recording
        return CommandRecorder().for_execution(
            command_type=self.record.command_type,
            code=self.record.code,
        )


def new_execution_id() -> str:
    return uuid.uuid4().hex[:12]


def execution_record_from_result(
    *,
    session_id: str,
    command_type: str,
    code: str | None,
    execution: ExecutionResult,
    recording: CommandRecording,
) -> ExecutionRecord:
    record = ExecutionRecord(
        execution_id=new_execution_id(),
        ts=utc_now_iso(),
        session_id=session_id,
        command_type=command_type,
        status=execution.status,
        duration_ms=execution.duration_ms,
        code=code,
        stdout=execution.stdout,
        stderr=execution.stderr,
        result=execution.result,
        execution_count=execution.execution_count,
        ename=execution.ename,
        evalue=execution.evalue,
        traceback=execution.traceback,
        outputs=list(execution.outputs),
        events=list(execution.events),
    )
    return replace(
        record,
        journal_entries=recording.build_records(
            ts=record.ts,
            session_id=session_id,
            execution_id=record.execution_id,
            execution=execution,
        ),
    )


def execution_record_from_exception(
    *,
    session_id: str,
    command_type: str,
    code: str | None,
    error: Exception,
    recording: CommandRecording,
) -> ExecutionRecord:
    ename = type(error).__name__
    evalue = str(error)
    traceback = None
    if isinstance(error, AgentNBException):
        ename = error.ename or ename
        evalue = error.evalue or error.message
        traceback = error.traceback
    record = ExecutionRecord(
        execution_id=new_execution_id(),
        ts=utc_now_iso(),
        session_id=session_id,
        command_type=command_type,
        status="error",
        duration_ms=0,
        code=code,
        ename=ename,
        evalue=evalue,
        traceback=traceback,
    )
    return replace(
        record,
        journal_entries=recording.build_records(
            ts=record.ts,
            session_id=session_id,
            execution_id=record.execution_id,
            error=error,
        ),
    )


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing {key}")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid {key}")
    return value


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Invalid {key}")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"Invalid {key}")
    return value


def _optional_str_list(payload: dict[str, Any], key: str) -> list[str] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Invalid {key}")
    return list(value)


def _optional_bool(payload: dict[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Invalid {key}")
    return value


def _optional_terminal_reason(payload: dict[str, Any], key: str) -> TerminalReason | None:
    value = payload.get(key)
    if value is None:
        return None
    if value not in {"completed", "failed", "cancelled", "worker_exited"}:
        raise ValueError(f"Invalid {key}")
    return cast(TerminalReason, value)


def _merge_records(previous: ExecutionRecord, current: ExecutionRecord) -> ExecutionRecord:
    changes: dict[str, object] = {}
    merged_cancel_requested = current.cancel_requested or previous.cancel_requested
    if previous.cancel_requested and not current.cancel_requested:
        changes["cancel_requested"] = True
    if current.cancel_requested_at is None and previous.cancel_requested_at is not None:
        changes["cancel_requested_at"] = previous.cancel_requested_at
    if current.cancel_request_source is None and previous.cancel_request_source is not None:
        changes["cancel_request_source"] = previous.cancel_request_source
    if (
        merged_cancel_requested
        and current.status == "error"
        and current.ename in _CANCELLATION_RAW_ERROR_TYPES
    ):
        changes["terminal_reason"] = "cancelled"
    elif current.terminal_reason is None and previous.terminal_reason is not None:
        changes["terminal_reason"] = previous.terminal_reason
    if current.recorded_status is None and previous.recorded_status is not None:
        changes["recorded_status"] = previous.recorded_status
    if current.recorded_ename is None and previous.recorded_ename is not None:
        changes["recorded_ename"] = previous.recorded_ename
    if current.recorded_evalue is None and previous.recorded_evalue is not None:
        changes["recorded_evalue"] = previous.recorded_evalue
    if current.recorded_traceback is None and previous.recorded_traceback is not None:
        changes["recorded_traceback"] = list(previous.recorded_traceback)
    if not changes:
        return current
    return replace(current, **changes)
