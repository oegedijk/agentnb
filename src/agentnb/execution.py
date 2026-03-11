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

from .contracts import ExecutionEvent, ExecutionResult, utc_now_iso
from .errors import (
    AgentNBException,
    KernelNotReadyError,
    NoKernelRunningError,
    RunWaitTimedOutError,
)
from .history import HistoryRecord, kernel_execution_record, user_command_record
from .session import DEFAULT_SESSION_ID, STATE_DIR_NAME, pid_exists

if TYPE_CHECKING:
    from .runtime import KernelRuntime

EXECUTIONS_FILE_NAME = "executions.jsonl"


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

    def to_history_records(self) -> list[HistoryRecord]:
        label = "reset" if self.command_type == "reset" else "exec"
        helper_label = (
            "reset kernel state" if self.command_type == "reset" else "exec kernel execution"
        )
        return [
            kernel_execution_record(
                ts=self.ts,
                session_id=self.session_id,
                execution_id=self.execution_id,
                command_type=self.command_type,
                label=helper_label,
                code=self.code,
                origin="execution_service",
                status=self.status,
                duration_ms=self.duration_ms,
                error_type=self.ename,
                stdout=self.stdout,
                result=self.result,
            ),
            user_command_record(
                ts=self.ts,
                session_id=self.session_id,
                execution_id=self.execution_id,
                command_type=self.command_type,
                label=label,
                input_text=self.code,
                code=self.code,
                origin="execution_service",
                status=self.status,
                duration_ms=self.duration_ms,
                error_type=self.ename,
                stdout=self.stdout,
                result=self.result,
            ),
        ]


class ExecutionStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.state_dir = self.project_root / STATE_DIR_NAME
        self.executions_file = self.state_dir / EXECUTIONS_FILE_NAME

    def append(self, record: ExecutionRecord) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
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
    ) -> ManagedExecution:
        started_new_session = False
        if ensure_started:
            _, started_new_session = self.runtime.ensure_started(
                project_root=project_root,
                session_id=session_id,
            )

        try:
            execution = self.runtime.execute(
                project_root=project_root,
                session_id=session_id,
                code=code,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            if isinstance(exc, (NoKernelRunningError, KernelNotReadyError)):
                raise
            record = self._record_from_exception(
                session_id=session_id,
                command_type="exec",
                code=code,
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
            command_type="exec",
            code=code,
            execution=execution,
        )
        self._store(project_root).append(record)
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

        record = ExecutionRecord(
            execution_id=_new_execution_id(),
            ts=utc_now_iso(),
            session_id=session_id,
            command_type="exec",
            status="running",
            duration_ms=0,
            code=code,
        )
        store = self._store(project_root)
        store.append(record)

        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agentnb.cli",
                    "_background-run",
                    "--project",
                    str(project_root),
                    record.execution_id,
                ],
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            updated = replace(
                record,
                status="error",
                ename=type(exc).__name__,
                evalue=str(exc),
            )
            store.append(updated)
            raise

        updated = replace(record, worker_pid=process.pid)
        store.append(updated)
        return ManagedExecution(record=updated, started_new_session=started_new_session)

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
                    "cancel_requested": False,
                    "status": record.status,
                }

            try:
                self.runtime.interrupt(project_root=project_root, session_id=record.session_id)
                return self._finalize_cancelled_run(project_root=project_root, record=record)
            except KernelNotReadyError:
                self.runtime.stop_starting(project_root=project_root, session_id=record.session_id)
                return self._finalize_cancelled_run(project_root=project_root, record=record)
            except NoKernelRunningError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll_interval_s)

    def complete_background_run(self, *, project_root: Path, execution_id: str) -> None:
        record = self._load_run(project_root=project_root, execution_id=execution_id)
        if record is None or record.code is None or record.status != "running":
            return

        try:
            execution = self.runtime.execute(
                project_root=project_root,
                session_id=record.session_id,
                code=record.code,
                timeout_s=30.0,
            )
            updated = replace(
                record,
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
        except Exception as exc:
            ename = type(exc).__name__
            evalue = str(exc)
            traceback = None
            if isinstance(exc, AgentNBException):
                ename = exc.ename or ename
                evalue = exc.evalue or exc.message
                traceback = exc.traceback
            updated = replace(
                record,
                status="error",
                ename=ename,
                evalue=evalue,
                traceback=traceback,
            )

        latest = self._load_run(project_root=project_root, execution_id=execution_id)
        if latest is None or latest.status != "running":
            return
        self._store(project_root).append(updated)

    def history_entries(
        self,
        *,
        project_root: Path,
        session_id: str,
        include_internal: bool,
        errors_only: bool,
    ) -> list[dict[str, Any]]:
        entries: list[HistoryRecord] = []
        for record in self._read_runs(
            session_id=session_id,
            command_types={"exec", "reset"},
            errors_only=errors_only,
            project_root=project_root,
        ):
            if record.status not in {"ok", "error"}:
                continue
            projections = record.to_history_records()
            if include_internal:
                entries.extend(projections)
            else:
                entries.append(projections[-1])
        return [entry.to_dict() for entry in entries]

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

    def _finalize_cancelled_run(
        self,
        *,
        project_root: Path,
        record: ExecutionRecord,
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
        return {
            "execution_id": record.execution_id,
            "cancel_requested": True,
            "status": updated.status,
        }

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
