from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..contracts import ExecutionEvent, ExecutionSink, utc_now_iso
from ..errors import (
    AgentNBException,
    KernelNotReadyError,
    NoKernelRunningError,
    RunWaitTimedOutError,
)
from ..execution_events import ExecutionResultAccumulator
from ..payloads import CancelRunResult, RunSnapshot
from ..recording import CommandRecorder, CommandRecording
from ..session import pid_exists
from .manager import RunManager
from .models import RunObserver, RunSpec
from .store import ExecutionRecord, ExecutionRun, ExecutionStore, ManagedExecution, new_execution_id

if TYPE_CHECKING:
    from ..runtime import KernelRuntime

_CANCEL_SETTLE_TIMEOUT_S = 0.5


class LocalRunManager(RunManager):
    def __init__(
        self,
        runtime: KernelRuntime,
        recorder: CommandRecorder | None = None,
    ) -> None:
        self.runtime = runtime
        self._recorder = recorder or CommandRecorder()

    def submit(self, spec: RunSpec, *, observer: RunObserver | None = None) -> ManagedExecution:
        if spec.command_type != "exec":
            raise ValueError(f"Unsupported run command type: {spec.command_type}")
        if spec.mode == "background":
            return self._submit_background(spec)
        return self._submit_foreground(spec, observer=observer)

    def list_runs(
        self,
        *,
        project_root: Path,
        session_id: str | None = None,
        errors_only: bool = False,
    ) -> list[RunSnapshot]:
        return [
            record.to_dict()
            for record in self._read_runs(
                project_root=project_root,
                session_id=session_id,
                command_types={"exec", "reset"},
                errors_only=errors_only,
            )
        ]

    def get_run(self, *, project_root: Path, execution_id: str) -> RunSnapshot:
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
    ) -> RunSnapshot:
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
        observer: RunObserver | None = None,
    ) -> RunSnapshot:
        deadline = time.monotonic() + timeout_s
        emitted_events = 0
        started_observer = False
        while True:
            record = self._load_run(project_root=project_root, execution_id=execution_id)
            if record is None:
                raise AgentNBException(
                    code="EXECUTION_NOT_FOUND",
                    message=f"Execution not found: {execution_id}",
                )
            if observer is not None and not started_observer:
                observer.started(
                    execution_id=record.execution_id,
                    session_id=record.session_id,
                )
                started_observer = True
            if observer is not None:
                for event in record.events[emitted_events:]:
                    observer.accept(event)
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
    ) -> CancelRunResult:
        if not self.runtime.capabilities.supports_interrupt:
            raise AgentNBException(
                code="UNSUPPORTED_OPERATION",
                message="Current backend does not support interrupting runs.",
            )

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
            record = self._record_cancel_request(project_root=project_root, record=record)

            try:
                self.runtime.interrupt(project_root=project_root, session_id=record.session_id)
                latest = self._wait_for_run_state_change(
                    project_root=project_root,
                    execution_id=execution_id,
                    timeout_s=min(timeout_s, _CANCEL_SETTLE_TIMEOUT_S),
                    poll_interval_s=poll_interval_s,
                )
                if latest is not None and latest.status != "running":
                    return self._terminal_run_payload(
                        latest,
                        cancel_requested=True,
                        session_outcome="preserved",
                    )
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

        run = ExecutionRun(
            store=self._store(project_root),
            record=record,
            recording=self._recording(command_type=record.command_type, code=record.code),
            started=True,
        )
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
            outputs=updated.outputs,
            events=updated.events,
            journal_entries=updated.journal_entries,
        )

    def _submit_foreground(
        self,
        spec: RunSpec,
        *,
        observer: RunObserver | None,
    ) -> ManagedExecution:
        started_new_session = False
        if spec.ensure_started:
            _, started_new_session = self.runtime.ensure_started(
                project_root=spec.project_root,
                session_id=spec.session_id,
            )

        run = self._new_run(
            project_root=spec.project_root,
            session_id=spec.session_id,
            command_type=spec.command_type,
            code=spec.code,
            worker_pid=os.getpid(),
        )

        try:
            execution = self.runtime.execute(
                project_root=spec.project_root,
                session_id=spec.session_id,
                code=spec.code or "",
                timeout_s=spec.timeout_s,
                before_backend=lambda: run.start(observer),
                event_sink=observer,
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
                    data=dict(record.to_execution_payload()),
                ) from exc
            raise

        record = run.finalize_result(execution)
        return ManagedExecution(record=record, started_new_session=started_new_session)

    def _submit_background(self, spec: RunSpec) -> ManagedExecution:
        started_new_session = False
        if spec.ensure_started:
            _, started_new_session = self.runtime.ensure_started(
                project_root=spec.project_root,
                session_id=spec.session_id,
            )

        run = self._new_run(
            project_root=spec.project_root,
            session_id=spec.session_id,
            command_type=spec.command_type,
            code=spec.code,
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
                    str(spec.project_root),
                    run.record.execution_id,
                ],
                cwd=str(spec.project_root),
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
            recording=self._recording(command_type=command_type, code=code),
            record=ExecutionRecord(
                execution_id=new_execution_id(),
                ts=utc_now_iso(),
                session_id=session_id,
                command_type=command_type,
                status="running",
                duration_ms=0,
                code=code,
                worker_pid=worker_pid,
            ),
        )

    def _recording(self, *, command_type: str, code: str | None) -> CommandRecording:
        return self._recorder.for_execution(command_type=command_type, code=code)

    def _finalize_cancelled_run(
        self,
        *,
        project_root: Path,
        record: ExecutionRecord,
        session_outcome: str,
    ) -> CancelRunResult:
        if record.worker_pid is not None:
            _terminate_process(record.worker_pid)
        updated = replace(
            record,
            status="error",
            ename="CancelledError",
            evalue="Run was cancelled by user.",
            terminal_reason="cancelled",
        )
        updated = replace(
            updated,
            journal_entries=self._recording(
                command_type=updated.command_type,
                code=updated.code,
            ).build_records(
                ts=updated.ts,
                session_id=updated.session_id,
                execution_id=updated.execution_id,
                status=updated.status,
                duration_ms=updated.duration_ms,
                error_type=updated.ename,
                stdout=updated.stdout,
                result=updated.result,
            ),
        )
        self._store(project_root).append(updated)
        return self._terminal_run_payload(
            updated,
            session_outcome=session_outcome,
        )

    def _terminal_run_payload(
        self,
        record: ExecutionRecord,
        *,
        cancel_requested: bool | None = None,
        session_outcome: str = "unchanged",
    ) -> CancelRunResult:
        return {
            "execution_id": record.execution_id,
            "session_id": record.session_id,
            "cancel_requested": record.cancel_requested
            if cancel_requested is None
            else cancel_requested,
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
            terminal_reason="cancelled" if record.cancel_requested else "worker_exited",
        )
        updated = replace(
            updated,
            journal_entries=self._recording(
                command_type=updated.command_type,
                code=updated.code,
            ).build_records(
                ts=updated.ts,
                session_id=updated.session_id,
                execution_id=updated.execution_id,
                status=updated.status,
                duration_ms=updated.duration_ms,
                error_type=updated.ename,
                stdout=updated.stdout,
                result=updated.result,
            ),
        )
        self._store(project_root).append(updated)
        return updated

    def _record_cancel_request(
        self,
        *,
        project_root: Path,
        record: ExecutionRecord,
    ) -> ExecutionRecord:
        updated = record.with_cancel_requested(
            requested_at=utc_now_iso(),
            source="user",
        )
        if updated is record:
            return record
        self._store(project_root).append(updated)
        return updated


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
            outputs=list(snapshot.outputs),
            events=list(snapshot.events),
        )


def _terminate_process(pid: int) -> None:
    if not pid_exists(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
