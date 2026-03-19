from __future__ import annotations

import os
import signal
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..contracts import utc_now_iso
from ..errors import (
    AgentNBException,
    KernelNotReadyError,
    NoKernelRunningError,
    RunWaitTimedOutError,
)
from ..payloads import CancelRunResult, RunSnapshot
from ..recording import CommandRecorder, CommandRecording
from ..session import pid_exists
from .executor import LocalRunExecutor, RunExecutor
from .manager import RunManager
from .models import RunObserver, RunPlan, RunSpec
from .store import ExecutionRecord, ExecutionRun, ExecutionStore, ManagedExecution, new_execution_id

if TYPE_CHECKING:
    from ..runtime import KernelRuntime

_CANCEL_SETTLE_TIMEOUT_S = 0.5
_ACTIVE_RUN_STATUSES = frozenset({"starting", "running"})


class LocalRunManager(RunManager):
    def __init__(
        self,
        runtime: KernelRuntime,
        recorder: CommandRecorder | None = None,
        executor: RunExecutor | None = None,
    ) -> None:
        self.runtime = runtime
        self._recorder = recorder or CommandRecorder()
        self._executor = executor or LocalRunExecutor(runtime)

    def submit(self, spec: RunSpec, *, observer: RunObserver | None = None) -> ManagedExecution:
        plan = spec.to_plan()
        self._validate_plan(plan)
        if plan.mode == "background":
            return self._submit_background(plan)
        return self._submit_foreground(plan, observer=observer)

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
        payload = record.to_dict()
        payload["snapshot_stale"] = record.status in _ACTIVE_RUN_STATUSES
        return payload

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
            if record.status not in _ACTIVE_RUN_STATUSES:
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
        skip_history: bool = False,
    ) -> RunSnapshot:
        """Replay and then stream events for a run until it finishes."""
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
                if skip_history:
                    emitted_events = len(record.events)
            if observer is not None:
                for event in record.events[emitted_events:]:
                    observer.accept(event)
                emitted_events = len(record.events)
            if record.status not in _ACTIVE_RUN_STATUSES:
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
            if record.status not in _ACTIVE_RUN_STATUSES:
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
                if latest is not None and latest.status not in _ACTIVE_RUN_STATUSES:
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
                if latest is not None and latest.status not in _ACTIVE_RUN_STATUSES:
                    return self._terminal_run_payload(latest)
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll_interval_s)

    def complete_background_run(self, *, project_root: Path, execution_id: str) -> None:
        record = self._load_run(project_root=project_root, execution_id=execution_id)
        if record is None or record.code is None or record.status not in _ACTIVE_RUN_STATUSES:
            return

        plan = self._plan_for_record(project_root=project_root, record=record)
        run = ExecutionRun(
            store=self._store(project_root),
            record=record,
            recording=self._recording(command_type=record.command_type, code=record.code),
            started=True,
        )

        try:
            execution = self._executor.complete_background_run(plan=plan, run=run)
            updated = run.result_record(execution)
        except Exception as exc:
            updated = run.error_record(exc)

        latest = self._load_run(project_root=project_root, execution_id=execution_id)
        if latest is None or latest.status not in _ACTIVE_RUN_STATUSES:
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
        plan: RunPlan,
        *,
        observer: RunObserver | None,
    ) -> ManagedExecution:
        started_new_session = self._ensure_plan_ready(plan)

        run = self._new_run(
            project_root=plan.project_root,
            session_id=plan.session_id,
            command_type=plan.command_type,
            code=plan.code,
            worker_pid=os.getpid(),
        )

        try:
            execution = self._executor.run_foreground(plan=plan, run=run, observer=observer)
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

    def _submit_background(self, plan: RunPlan) -> ManagedExecution:
        started_new_session = self._ensure_plan_ready(plan)

        run = self._new_run(
            project_root=plan.project_root,
            session_id=plan.session_id,
            command_type=plan.command_type,
            code=plan.code,
            status="starting",
        )
        run.start()

        try:
            record = self._executor.start_background(
                plan=plan,
                run=run,
            )
        except Exception as exc:
            run.finalize_error(exc)
            raise

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
        status: Literal["starting", "running"] = "running",
        worker_pid: int | None = None,
    ) -> ExecutionRun:
        return ExecutionRun(
            store=self._store(project_root),
            started_mono=time.monotonic(),
            recording=self._recording(command_type=command_type, code=code),
            record=ExecutionRecord(
                execution_id=new_execution_id(),
                ts=utc_now_iso(),
                session_id=session_id,
                command_type=command_type,
                status=status,
                duration_ms=0,
                code=code,
                worker_pid=worker_pid,
            ),
        )

    def _recording(self, *, command_type: str, code: str | None) -> CommandRecording:
        return self._recorder.for_execution(command_type=command_type, code=code)

    def _validate_plan(self, plan: RunPlan) -> None:
        if plan.command_type not in {"exec", "reset"}:
            raise ValueError(f"Unsupported run command type: {plan.command_type}")
        if plan.mode == "background" and not plan.supports_background:
            raise ValueError(f"Unsupported run mode for {plan.command_type}: {plan.mode}")

    def _ensure_plan_ready(self, plan: RunPlan) -> bool:
        started_new_session = False
        if plan.command_type == "exec" and plan.ensure_started:
            _, started_new_session = self.runtime.ensure_started(
                project_root=plan.project_root,
                session_id=plan.session_id,
            )
        return started_new_session

    def _plan_for_record(self, *, project_root: Path, record: ExecutionRecord) -> RunPlan:
        if record.command_type == "exec":
            return RunPlan.for_exec(
                project_root=project_root,
                session_id=record.session_id,
                code=record.code or "",
                mode="background",
                timeout_s=30.0,
            )
        if record.command_type == "reset":
            return RunPlan.for_reset(
                project_root=project_root,
                session_id=record.session_id,
                mode="foreground",
                timeout_s=30.0,
            )
        raise ValueError(f"Unsupported run command type: {record.command_type}")

    def _finalize_cancelled_run(
        self,
        *,
        project_root: Path,
        record: ExecutionRecord,
        session_outcome: str,
        started_mono: float | None = None,
    ) -> CancelRunResult:
        if record.worker_pid is not None:
            _terminate_process(record.worker_pid)
        duration_ms = record.duration_ms
        if started_mono is not None and duration_ms == 0:
            duration_ms = int((time.monotonic() - started_mono) * 1000)
        elif duration_ms == 0:
            duration_ms = _wall_clock_duration_ms(record.ts)
        updated = replace(
            record,
            status="error",
            duration_ms=duration_ms,
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
            if record is None or record.status not in _ACTIVE_RUN_STATUSES:
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
        if record.status not in _ACTIVE_RUN_STATUSES:
            return record
        if record.worker_pid is None:
            return record
        if pid_exists(record.worker_pid):
            return record

        duration_ms = record.duration_ms
        if duration_ms == 0:
            duration_ms = _wall_clock_duration_ms(record.ts)
        updated = replace(
            record,
            status="error",
            duration_ms=duration_ms,
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


def _wall_clock_duration_ms(iso_ts: str) -> int:
    """Compute elapsed milliseconds from an ISO timestamp to now."""
    from datetime import UTC, datetime

    try:
        started = datetime.fromisoformat(iso_ts)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - started).total_seconds()
        return max(0, int(elapsed * 1000))
    except Exception:
        return 0


def _terminate_process(pid: int) -> None:
    if not pid_exists(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
