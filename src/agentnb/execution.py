from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .contracts import (
    ExecutionSink,
    HelperAccessMetadata,
    HelperInitialRuntimeState,
    HelperWaitFor,
)
from .errors import KernelWaitTimedOutError, RunWaitTimedOutError
from .payloads import CancelRunResult
from .recording import CommandRecorder
from .runs import (
    ExecutionRecord,
    ExecutionRun,
    ExecutionStore,
    LocalRunManager,
    ManagedExecution,
    RunManager,
    RunObservationResult,
    RunSpec,
    StartOutcome,
    _ExecutionProgressSink,
)
from .session import DEFAULT_SESSION_ID

if TYPE_CHECKING:
    from .runtime import KernelRuntime, KernelStatus, KernelWaitResult, RuntimeStateKind


SessionAccessTarget = Literal["ready", "usable", "idle", "helper"]


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionAccessRequest:
    project_root: Path
    session_id: str = DEFAULT_SESSION_ID
    target: SessionAccessTarget
    timeout_s: float = 30.0
    poll_interval_s: float = 0.1


@dataclass(slots=True, frozen=True)
class SessionAccessOutcome:
    status: KernelStatus | None
    waited: bool
    waited_for: HelperWaitFor | None = None
    runtime_state: RuntimeStateKind | None = None
    waited_ms: int = 0
    initial_runtime_state: HelperInitialRuntimeState | None = None
    blocking_execution_id: str | None = None

    @classmethod
    def from_wait_result(cls, wait_result: KernelWaitResult) -> SessionAccessOutcome:
        return cls(
            status=wait_result.status,
            waited=wait_result.waited,
            waited_for=wait_result.waited_for,
            runtime_state=wait_result.runtime_state,
            waited_ms=wait_result.waited_ms,
            initial_runtime_state=wait_result.initial_runtime_state,
        )

    def to_helper_access_metadata(self) -> HelperAccessMetadata:
        return HelperAccessMetadata(
            waited=self.waited,
            waited_for=self.waited_for,
            waited_ms=self.waited_ms,
            initial_runtime_state=self.initial_runtime_state,
            blocking_execution_id=self.blocking_execution_id,
        )


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
    ) -> list[ExecutionRecord]:
        return self._run_manager.list_runs(
            project_root=project_root,
            session_id=session_id,
            errors_only=errors_only,
        )

    def get_run(self, *, project_root: Path, execution_id: str) -> ExecutionRecord:
        return self._run_manager.get_run(project_root=project_root, execution_id=execution_id)

    def wait_for_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> ExecutionRecord:
        return self._run_manager.wait_for_run(
            project_root=project_root,
            execution_id=execution_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )

    def observe_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
        event_sink: ExecutionSink | None = None,
        skip_history: bool = False,
    ) -> RunObservationResult:
        return self._run_manager.follow_run(
            project_root=project_root,
            execution_id=execution_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            observer=event_sink,
            skip_history=skip_history,
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
    ) -> ExecutionRecord:
        observation = self.observe_run(
            project_root=project_root,
            execution_id=execution_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            event_sink=event_sink,
            skip_history=skip_history,
        )
        if observation.completion_reason == "window_elapsed":
            raise RunWaitTimedOutError(timeout_s)
        return observation.run

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

    def wait_for_helper_session_access(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> HelperAccessMetadata:
        outcome = self.wait_for_session_access(
            project_root=project_root,
            session_id=session_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            target="helper",
        )
        return outcome.to_helper_access_metadata()

    def wait_for_session_access(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        target: SessionAccessTarget,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> SessionAccessOutcome:
        request = SessionAccessRequest(
            project_root=project_root,
            session_id=session_id,
            target=target,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
        if request.target == "ready":
            return SessionAccessOutcome.from_wait_result(
                self.runtime.wait_until_ready(
                    project_root=request.project_root,
                    session_id=request.session_id,
                    timeout_s=request.timeout_s,
                    poll_interval_s=request.poll_interval_s,
                )
            )
        if request.target == "usable":
            return SessionAccessOutcome.from_wait_result(
                self.runtime.wait_for_usable(
                    project_root=request.project_root,
                    session_id=request.session_id,
                    timeout_s=request.timeout_s,
                    poll_interval_s=request.poll_interval_s,
                )
            )
        if request.target == "idle":
            return self._wait_for_idle_access(request)
        access = self._run_manager.wait_for_helper_session_access(
            project_root=request.project_root,
            session_id=request.session_id,
            timeout_s=request.timeout_s,
            poll_interval_s=request.poll_interval_s,
        )
        state = self.runtime.runtime_state(
            project_root=request.project_root,
            session_id=request.session_id,
        )
        return SessionAccessOutcome(
            status=state.to_kernel_status(),
            waited=access.waited,
            waited_for=access.waited_for,
            runtime_state=state.kind,
            waited_ms=access.waited_ms,
            initial_runtime_state=access.initial_runtime_state,
            blocking_execution_id=access.blocking_execution_id,
        )

    def complete_background_run(self, *, project_root: Path, execution_id: str) -> None:
        self._run_manager.complete_background_run(
            project_root=project_root,
            execution_id=execution_id,
        )

    def _wait_for_idle_access(self, request: SessionAccessRequest) -> SessionAccessOutcome:
        started_at = time.monotonic()
        deadline = started_at + request.timeout_s
        waited_for_run = False
        initial_runtime_state: HelperInitialRuntimeState | None = None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KernelWaitTimedOutError(request.timeout_s, waiting_for="idle")
            wait_result = self.runtime.wait_until_idle(
                project_root=request.project_root,
                session_id=request.session_id,
                timeout_s=remaining,
                poll_interval_s=request.poll_interval_s,
            )
            if initial_runtime_state is None:
                initial_runtime_state = (
                    wait_result.initial_runtime_state or wait_result.runtime_state
                )
            active_execution_id = self._active_execution_id_for_session(
                project_root=request.project_root,
                session_id=request.session_id,
            )
            if active_execution_id is None:
                if not waited_for_run:
                    return SessionAccessOutcome.from_wait_result(wait_result)
                return SessionAccessOutcome(
                    status=wait_result.status,
                    waited=True,
                    waited_for=wait_result.waited_for or "idle",
                    runtime_state=wait_result.runtime_state,
                    waited_ms=max(
                        wait_result.waited_ms,
                        int((time.monotonic() - started_at) * 1000),
                    ),
                    initial_runtime_state=initial_runtime_state,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KernelWaitTimedOutError(request.timeout_s, waiting_for="idle")
            self.wait_for_run(
                project_root=request.project_root,
                execution_id=active_execution_id,
                timeout_s=remaining,
                poll_interval_s=request.poll_interval_s,
            )
            waited_for_run = True

    def _active_execution_id_for_session(
        self,
        *,
        project_root: Path,
        session_id: str,
    ) -> str | None:
        runs = self._run_manager.list_runs(
            project_root=project_root,
            session_id=session_id,
        )
        for run in reversed(runs):
            status = _run_field(run, "status")
            execution_id = _run_field(run, "execution_id")
            if status in {"starting", "running"} and isinstance(execution_id, str) and execution_id:
                return execution_id
        return None


def _run_field(run: ExecutionRecord | Mapping[str, object], key: str) -> object:
    if isinstance(run, ExecutionRecord):
        return getattr(run, key, None)
    return run.get(key)


__all__ = [
    "ExecutionRecord",
    "ExecutionRun",
    "ExecutionService",
    "ExecutionStore",
    "ManagedExecution",
    "SessionAccessOutcome",
    "SessionAccessRequest",
    "SessionAccessTarget",
    "StartOutcome",
    "_ExecutionProgressSink",
]
