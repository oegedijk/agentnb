from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

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
        )

    def to_execution_payload(self) -> RunSnapshot:
        return self.to_dict()


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
            if errors_only and record.status != "error":
                continue
            if record.execution_id in entries_by_id:
                del entries_by_id[record.execution_id]
            entries_by_id[record.execution_id] = record
        return list(entries_by_id.values())

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
