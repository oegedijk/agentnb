from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .contracts import ExecutionSink
from .payloads import CancelRunResult, RunSnapshot
from .recording import CommandRecorder
from .runs import (
    ExecutionRecord,
    ExecutionRun,
    ExecutionStore,
    LocalRunManager,
    ManagedExecution,
    RunManager,
    RunSpec,
    _ExecutionProgressSink,
)
from .session import DEFAULT_SESSION_ID

if TYPE_CHECKING:
    from .runtime import KernelRuntime


class ExecutionService:
    def __init__(
        self,
        runtime: KernelRuntime,
        recorder: CommandRecorder | None = None,
        run_manager: RunManager | None = None,
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
        return self._run_manager.submit(
            RunSpec(
                project_root=project_root,
                session_id=session_id,
                command_type="reset",
                code=None,
                mode="foreground",
                timeout_s=timeout_s,
            )
        )

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
        skip_history: bool = False,
    ) -> RunSnapshot:
        return self._run_manager.follow_run(
            project_root=project_root,
            execution_id=execution_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            observer=event_sink,
            skip_history=skip_history,
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


__all__ = [
    "ExecutionRecord",
    "ExecutionRun",
    "ExecutionService",
    "ExecutionStore",
    "ManagedExecution",
    "_ExecutionProgressSink",
]
