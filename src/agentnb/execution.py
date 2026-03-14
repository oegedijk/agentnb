from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .contracts import ExecutionSink
from .errors import AgentNBException, KernelNotReadyError, NoKernelRunningError
from .payloads import CancelRunResult, RunSnapshot
from .recording import CommandRecorder
from .runs import (
    ExecutionRecord,
    ExecutionRun,
    ExecutionStore,
    LocalRunManager,
    ManagedExecution,
    RunSpec,
    _ExecutionProgressSink,
)
from .runs.store import execution_record_from_exception, execution_record_from_result
from .session import DEFAULT_SESSION_ID

if TYPE_CHECKING:
    from .runtime import KernelRuntime


class ExecutionService:
    def __init__(
        self,
        runtime: KernelRuntime,
        recorder: CommandRecorder | None = None,
        run_manager: LocalRunManager | None = None,
    ) -> None:
        self.runtime = runtime
        self._recorder = recorder or CommandRecorder()
        self._run_manager = run_manager or LocalRunManager(runtime, recorder=self._recorder)

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
        return self._run_manager.submit(
            RunSpec(
                project_root=project_root,
                session_id=session_id,
                command_type="exec",
                code=code,
                mode="foreground",
                timeout_s=timeout_s,
                ensure_started=ensure_started,
            ),
            observer=event_sink,
        )

    def start_background_code(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        code: str,
        ensure_started: bool = False,
    ) -> ManagedExecution:
        return self._run_manager.submit(
            RunSpec(
                project_root=project_root,
                session_id=session_id,
                command_type="exec",
                code=code,
                mode="background",
                ensure_started=ensure_started,
            )
        )

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
            record = execution_record_from_exception(
                session_id=session_id,
                command_type="reset",
                code=None,
                error=exc,
                recording=self._recording(command_type="reset", code=None),
            )
            self._store(project_root).append(record)
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

        record = execution_record_from_result(
            session_id=session_id,
            command_type="reset",
            code=None,
            execution=execution,
            recording=self._recording(command_type="reset", code=None),
        )
        self._store(project_root).append(record)
        return ManagedExecution(record=record)

    def list_runs(
        self,
        *,
        project_root: Path,
        session_id: str | None = None,
        errors_only: bool = False,
    ) -> list[RunSnapshot]:
        return self._run_manager.list_runs(
            project_root=project_root,
            session_id=session_id,
            errors_only=errors_only,
        )

    def get_run(self, *, project_root: Path, execution_id: str) -> RunSnapshot:
        return self._run_manager.get_run(project_root=project_root, execution_id=execution_id)

    def wait_for_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> RunSnapshot:
        return self._run_manager.wait_for_run(
            project_root=project_root,
            execution_id=execution_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )

    def follow_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
        event_sink: ExecutionSink | None = None,
    ) -> RunSnapshot:
        return self._run_manager.follow_run(
            project_root=project_root,
            execution_id=execution_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            observer=event_sink,
        )

    def cancel_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> CancelRunResult:
        return self._run_manager.cancel_run(
            project_root=project_root,
            execution_id=execution_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )

    def complete_background_run(self, *, project_root: Path, execution_id: str) -> None:
        self._run_manager.complete_background_run(
            project_root=project_root,
            execution_id=execution_id,
        )

    def _store(self, project_root: Path) -> ExecutionStore:
        return ExecutionStore(project_root)

    def _recording(self, *, command_type: str, code: str | None):
        return self._recorder.for_execution(command_type=command_type, code=code)


__all__ = [
    "ExecutionRecord",
    "ExecutionRun",
    "ExecutionService",
    "ExecutionStore",
    "ManagedExecution",
    "_ExecutionProgressSink",
]
