from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .contracts import ExecutionEvent, ExecutionResult, ExecutionSink, utc_now_iso
from .errors import (
    AgentNBException,
    KernelNotReadyError,
    NoKernelRunningError,
    RunWaitTimedOutError,
)
from .execution_events import ExecutionResultAccumulator
from .session import DEFAULT_SESSION_ID, pid_exists
from .state import StateLayout

if TYPE_CHECKING:
    from .runtime import KernelRuntime

_CANCEL_SETTLE_TIMEOUT_S = 0.5


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
    events: list[ExecutionEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
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
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutionRecord:
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
            events=events,
        )

    def to_execution_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        return payload


class ExecutionStore:
    def __init__(self, project_root: Path) -> None:
        self.layout = StateLayout(project_root)
        self.project_root = self.layout.project_root
        self.state_dir = self.layout.state_dir
        self.executions_file = self.layout.executions_file

    def append(self, record: ExecutionRecord) -> None:
        self.layout.ensure_state_dir()
        with self.executions_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=True))
            handle.write("\n")

    def read(
        self,
        *,
        session_id: str | None = None,
        command_types: set[str] | None = None,
        errors_only: bool = False,
    ) -> list[ExecutionRecord]:
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
            events=execution.events,
        )

    def finalize_result(self, execution: ExecutionResult) -> ExecutionRecord:
        updated = self.result_record(execution)
        self.store.append(updated)
        self.record = updated
        return updated

    def error_record(self, error: Exception) -> ExecutionRecord:
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
        )

    def finalize_error(self, error: Exception) -> ExecutionRecord:
        updated = self.error_record(error)
        self.store.append(updated)
        self.record = updated
        return updated


class ExecutionService:
    def __init__(self, runtime: KernelRuntime) -> None:
        self.runtime = runtime

    def execute_code(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        code: str,
        timeout_s: float,
        ensure_started: bool = False,
        event_sink: ExecutionSink | None = None,
    ) -> ManagedExecution:
        started_new_session = False
        if ensure_started:
            _, started_new_session = self.runtime.ensure_started(
                project_root=project_root,
                session_id=session_id,
            )

        run = self._new_run(
            project_root=project_root,
            session_id=session_id,
            command_type="exec",
            code=code,
            worker_pid=os.getpid(),
        )

        try:
            execution = self.runtime.execute(
                project_root=project_root,
                session_id=session_id,
                code=code,
                timeout_s=timeout_s,
                before_backend=lambda: run.start(event_sink),
                event_sink=event_sink,
            )
        except Exception as exc:
            if isinstance(exc, (NoKernelRunningError, KernelNotReadyError)):
                raise
            record = run.finalize_error(exc)
            if isinstance(exc, AgentNBException):
                raise AgentNBException(
                    code=exc.code,
                    message=exc.message,
                    ename=exc.ename,
                    evalue=exc.evalue,
                    traceback=exc.traceback,
                    data=record.to_execution_payload(),
                ) from exc
            raise

        record = run.finalize_result(execution)
        return ManagedExecution(record=record, started_new_session=started_new_session)

    def start_background_code(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        code: str,
        ensure_started: bool = False,
    ) -> ManagedExecution:
        started_new_session = False
        if ensure_started:
            _, started_new_session = self.runtime.ensure_started(
                project_root=project_root,
                session_id=session_id,
            )

        run = self._new_run(
            project_root=project_root,
            session_id=session_id,
            command_type="exec",
            code=code,
        )
        run.start()

        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agentnb.cli",
                    "_background-run",
                    "--project",
                    str(project_root),
                    run.record.execution_id,
                ],
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            run.finalize_error(exc)
            raise

        record = run.replace(worker_pid=process.pid)
        return ManagedExecution(record=record, started_new_session=started_new_session)

    def reset_session(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float,
    ) -> ManagedExecution:
        try:
            execution = self.runtime.reset(
                project_root=project_root,
                session_id=session_id,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            if isinstance(exc, (NoKernelRunningError, KernelNotReadyError)):
                raise
            record = self._record_from_exception(
                session_id=session_id,
                command_type="reset",
                code=None,
                error=exc,
            )
            self._store(project_root).append(record)
            if isinstance(exc, AgentNBException):
                raise AgentNBException(
                    code=exc.code,
                    message=exc.message,
                    ename=exc.ename,
                    evalue=exc.evalue,
                    traceback=exc.traceback,
                    data=record.to_execution_payload(),
                ) from exc
            raise

        record = self._record_from_result(
            session_id=session_id,
            command_type="reset",
            code=None,
            execution=execution,
        )
        self._store(project_root).append(record)
        return ManagedExecution(record=record)

    def list_runs(
        self,
        *,
        project_root: Path,
        session_id: str | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            record.to_dict()
            for record in self._read_runs(
                project_root=project_root,
                session_id=session_id,
                command_types={"exec", "reset"},
                errors_only=errors_only,
            )
        ]

    def get_run(self, *, project_root: Path, execution_id: str) -> dict[str, Any]:
        record = self._load_run(project_root=project_root, execution_id=execution_id)
        if record is None:
            raise AgentNBException(
                code="EXECUTION_NOT_FOUND",
                message=f"Execution not found: {execution_id}",
            )
        return record.to_dict()

    def wait_for_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while True:
            record = self._load_run(project_root=project_root, execution_id=execution_id)
            if record is None:
                raise AgentNBException(
                    code="EXECUTION_NOT_FOUND",
                    message=f"Execution not found: {execution_id}",
                )
            if record.status != "running":
                return record.to_dict()
            if time.monotonic() >= deadline:
                raise RunWaitTimedOutError(timeout_s)
            time.sleep(poll_interval_s)

    def follow_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
        event_sink: ExecutionSink | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        emitted_events = 0
        started_sink = False
        while True:
            record = self._load_run(project_root=project_root, execution_id=execution_id)
            if record is None:
                raise AgentNBException(
                    code="EXECUTION_NOT_FOUND",
                    message=f"Execution not found: {execution_id}",
                )
            if event_sink is not None and not started_sink:
                event_sink.started(
                    execution_id=record.execution_id,
                    session_id=record.session_id,
                )
                started_sink = True
            if event_sink is not None:
                for event in record.events[emitted_events:]:
                    event_sink.accept(event)
                emitted_events = len(record.events)
            if record.status != "running":
                return record.to_dict()
            if time.monotonic() >= deadline:
                raise RunWaitTimedOutError(timeout_s)
            time.sleep(poll_interval_s)

    def cancel_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while True:
            record = self._load_run(project_root=project_root, execution_id=execution_id)
            if record is None:
                raise AgentNBException(
                    code="EXECUTION_NOT_FOUND",
                    message=f"Execution not found: {execution_id}",
                )
            if record.status != "running":
                return {
                    "execution_id": execution_id,
                    "session_id": record.session_id,
                    "cancel_requested": False,
                    "status": record.status,
                    "run_status": record.status,
                    "session_outcome": "unchanged",
                }

            try:
                self.runtime.interrupt(project_root=project_root, session_id=record.session_id)
                latest = self._wait_for_run_state_change(
                    project_root=project_root,
                    execution_id=execution_id,
                    timeout_s=min(timeout_s, _CANCEL_SETTLE_TIMEOUT_S),
                    poll_interval_s=poll_interval_s,
                )
                if latest is not None and latest.status != "running":
                    return self._terminal_run_payload(latest)
                return self._finalize_cancelled_run(
                    project_root=project_root,
                    record=record,
                    session_outcome="preserved",
                )
            except KernelNotReadyError:
                self.runtime.stop_starting(project_root=project_root, session_id=record.session_id)
                return self._finalize_cancelled_run(
                    project_root=project_root,
                    record=record,
                    session_outcome="stopped",
                )
            except NoKernelRunningError:
                latest = self._load_run(project_root=project_root, execution_id=execution_id)
                if latest is not None and latest.status != "running":
                    return self._terminal_run_payload(latest)
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll_interval_s)

    def complete_background_run(self, *, project_root: Path, execution_id: str) -> None:
        record = self._load_run(project_root=project_root, execution_id=execution_id)
        if record is None or record.code is None or record.status != "running":
            return

        run = ExecutionRun(store=self._store(project_root), record=record, started=True)
        progress_sink = _ExecutionProgressSink(run)

        try:
            execution = self.runtime.execute(
                project_root=project_root,
                session_id=record.session_id,
                code=record.code,
                timeout_s=30.0,
                event_sink=progress_sink,
            )
            updated = run.result_record(execution)
        except Exception as exc:
            updated = run.error_record(exc)

        latest = self._load_run(project_root=project_root, execution_id=execution_id)
        if latest is None or latest.status != "running":
            return
        run.replace(
            status=updated.status,
            duration_ms=updated.duration_ms,
            stdout=updated.stdout,
            stderr=updated.stderr,
            result=updated.result,
            execution_count=updated.execution_count,
            ename=updated.ename,
            evalue=updated.evalue,
            traceback=updated.traceback,
            events=updated.events,
        )

    def _record_from_result(
        self,
        *,
        session_id: str,
        command_type: str,
        code: str | None,
        execution: ExecutionResult,
    ) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=_new_execution_id(),
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
            events=execution.events,
        )

    def _record_from_exception(
        self,
        *,
        session_id: str,
        command_type: str,
        code: str | None,
        error: Exception,
    ) -> ExecutionRecord:
        ename = type(error).__name__
        evalue = str(error)
        traceback = None
        if isinstance(error, AgentNBException):
            ename = error.ename or ename
            evalue = error.evalue or error.message
            traceback = error.traceback
        return ExecutionRecord(
            execution_id=_new_execution_id(),
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

    def _store(self, project_root: Path) -> ExecutionStore:
        return ExecutionStore(project_root)

    def _new_run(
        self,
        *,
        project_root: Path,
        session_id: str,
        command_type: str,
        code: str | None,
        worker_pid: int | None = None,
    ) -> ExecutionRun:
        return ExecutionRun(
            store=self._store(project_root),
            record=ExecutionRecord(
                execution_id=_new_execution_id(),
                ts=utc_now_iso(),
                session_id=session_id,
                command_type=command_type,
                status="running",
                duration_ms=0,
                code=code,
                worker_pid=worker_pid,
            ),
        )

    def _finalize_cancelled_run(
        self,
        *,
        project_root: Path,
        record: ExecutionRecord,
        session_outcome: str,
    ) -> dict[str, Any]:
        if record.worker_pid is not None:
            _terminate_process(record.worker_pid)
        updated = replace(
            record,
            status="error",
            ename="CancelledError",
            evalue="Run was cancelled by user.",
        )
        self._store(project_root).append(updated)
        return self._terminal_run_payload(
            updated,
            cancel_requested=True,
            session_outcome=session_outcome,
        )

    def _terminal_run_payload(
        self,
        record: ExecutionRecord,
        *,
        cancel_requested: bool = False,
        session_outcome: str = "unchanged",
    ) -> dict[str, Any]:
        return {
            "execution_id": record.execution_id,
            "session_id": record.session_id,
            "cancel_requested": cancel_requested,
            "status": record.status,
            "run_status": record.status,
            "session_outcome": session_outcome,
        }

    def _wait_for_run_state_change(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float,
        poll_interval_s: float,
    ) -> ExecutionRecord | None:
        deadline = time.monotonic() + timeout_s
        while True:
            record = self._load_run(project_root=project_root, execution_id=execution_id)
            if record is None or record.status != "running":
                return record
            if time.monotonic() >= deadline:
                return record
            time.sleep(poll_interval_s)

    def _read_runs(
        self,
        *,
        project_root: Path,
        session_id: str | None,
        command_types: set[str],
        errors_only: bool,
    ) -> list[ExecutionRecord]:
        records = self._store(project_root).read(
            session_id=session_id,
            command_types=command_types,
            errors_only=errors_only,
        )
        return [
            self._normalize_run_state(project_root=project_root, record=record)
            for record in records
        ]

    def _load_run(self, *, project_root: Path, execution_id: str) -> ExecutionRecord | None:
        record = self._store(project_root).get(execution_id)
        if record is None:
            return None
        return self._normalize_run_state(project_root=project_root, record=record)

    def _normalize_run_state(
        self,
        *,
        project_root: Path,
        record: ExecutionRecord,
    ) -> ExecutionRecord:
        if record.status != "running":
            return record
        if record.worker_pid is not None and pid_exists(record.worker_pid):
            return record

        updated = replace(
            record,
            status="error",
            ename="WorkerExitedError",
            evalue="Background worker exited before recording a result.",
        )
        self._store(project_root).append(updated)
        return updated


def _new_execution_id() -> str:
    return uuid.uuid4().hex[:12]


class _ExecutionProgressSink(ExecutionSink):
    def __init__(self, run: ExecutionRun) -> None:
        self._run = run
        self._accumulator = ExecutionResultAccumulator()

    def started(self, *, execution_id: str, session_id: str) -> None:
        del execution_id, session_id

    def accept(self, event: ExecutionEvent) -> None:
        self._accumulator.accept(event)
        snapshot = self._accumulator.build(duration_ms=0)
        status = "error" if snapshot.status == "error" else "running"

        self._run.replace(
            status=status,
            stdout=snapshot.stdout,
            stderr=snapshot.stderr,
            result=snapshot.result,
            ename=snapshot.ename,
            evalue=snapshot.evalue,
            traceback=snapshot.traceback,
            events=snapshot.events,
        )


def _terminate_process(pid: int) -> None:
    if not pid_exists(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


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
