from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from .contracts import (
    ExecutionSink,
    HelperAccessMetadata,
    HelperInitialRuntimeState,
    HelperWaitFor,
)
from .errors import KernelWaitTimedOutError, RunWaitTimedOutError
from .recording import CommandRecorder
from .runs import (
    ExecutionRecord,
    ExecutionRun,
    ExecutionStore,
    LocalRunManager,
    ManagedExecution,
    RunCancelOutcome,
    RunManager,
    RunObservationCompletion,
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


@dataclass(slots=True, frozen=True, kw_only=True)
class ExecutionCommandRequest:
    project_root: Path
    session_id: str = DEFAULT_SESSION_ID
    command_type: Literal["exec", "reset"]
    mode: Literal["foreground", "background"] = "foreground"
    code: str | None = None
    timeout_s: float = 30.0
    ensure_started: bool = False
    event_sink: ExecutionSink | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class RunListRequest:
    project_root: Path
    session_id: str | None = None
    errors_only: bool = False


@dataclass(slots=True, frozen=True, kw_only=True)
class RunRetrievalRequest:
    project_root: Path
    execution_id: str
    mode: Literal["get", "wait", "follow"] = "get"
    timeout_s: float = 30.0
    poll_interval_s: float = 0.1
    event_sink: ExecutionSink | None = None
    skip_history: bool = False


@dataclass(slots=True, frozen=True)
class RunRetrievalOutcome:
    run: ExecutionRecord
    completion_reason: RunObservationCompletion | None = None
    replayed_event_count: int = 0
    emitted_event_count: int = 0


@dataclass(slots=True, frozen=True, kw_only=True)
class RunCancelRequest:
    project_root: Path
    execution_id: str
    timeout_s: float = 10.0
    poll_interval_s: float = 0.1


class SessionAccessProvider(Protocol):
    def wait_for_session_access(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        target: SessionAccessTarget,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> SessionAccessOutcome: ...


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

    def execute(self, request: ExecutionCommandRequest) -> ManagedExecution:
        return self._run_manager.submit(
            RunSpec(
                project_root=request.project_root,
                session_id=request.session_id,
                command_type=request.command_type,
                code=request.code,
                mode=request.mode,
                timeout_s=request.timeout_s,
                ensure_started=request.ensure_started,
            ),
            observer=request.event_sink,
        )

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
        return self.execute(
            ExecutionCommandRequest(
                project_root=project_root,
                session_id=session_id,
                command_type="exec",
                code=code,
                timeout_s=timeout_s,
                ensure_started=ensure_started,
                event_sink=event_sink,
            )
        )

    def start_background_code(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        code: str,
        ensure_started: bool = False,
    ) -> ManagedExecution:
        return self.execute(
            ExecutionCommandRequest(
                project_root=project_root,
                session_id=session_id,
                command_type="exec",
                mode="background",
                code=code,
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
        return self.execute(
            ExecutionCommandRequest(
                project_root=project_root,
                session_id=session_id,
                command_type="reset",
                timeout_s=timeout_s,
            )
        )

    def list_runs(
        self,
        request: RunListRequest | None = None,
        *,
        project_root: Path | None = None,
        session_id: str | None = None,
        errors_only: bool = False,
    ) -> list[ExecutionRecord]:
        resolved = request or RunListRequest(
            project_root=_require_path(project_root, name="project_root"),
            session_id=session_id,
            errors_only=errors_only,
        )
        return self._run_manager.list_runs(
            project_root=resolved.project_root,
            session_id=resolved.session_id,
            errors_only=resolved.errors_only,
        )

    def retrieve_run(self, request: RunRetrievalRequest) -> RunRetrievalOutcome:
        if request.mode == "follow":
            observation = self._run_manager.follow_run(
                project_root=request.project_root,
                execution_id=request.execution_id,
                timeout_s=request.timeout_s,
                poll_interval_s=request.poll_interval_s,
                observer=request.event_sink,
                skip_history=request.skip_history,
            )
            return RunRetrievalOutcome(
                run=observation.run,
                completion_reason=observation.completion_reason,
                replayed_event_count=observation.replayed_event_count,
                emitted_event_count=observation.emitted_event_count,
            )
        if request.mode == "wait":
            return RunRetrievalOutcome(
                run=self._run_manager.wait_for_run(
                    project_root=request.project_root,
                    execution_id=request.execution_id,
                    timeout_s=request.timeout_s,
                    poll_interval_s=request.poll_interval_s,
                )
            )
        return RunRetrievalOutcome(
            run=self._run_manager.get_run(
                project_root=request.project_root,
                execution_id=request.execution_id,
            )
        )

    def get_run(
        self,
        request: RunRetrievalRequest | None = None,
        *,
        project_root: Path | None = None,
        execution_id: str | None = None,
    ) -> ExecutionRecord:
        resolved = request or RunRetrievalRequest(
            project_root=_require_path(project_root, name="project_root"),
            execution_id=_require_str(execution_id, name="execution_id"),
        )
        return self.retrieve_run(replace(resolved, mode="get")).run

    def wait_for_run(
        self,
        request: RunRetrievalRequest | None = None,
        *,
        project_root: Path | None = None,
        execution_id: str | None = None,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> ExecutionRecord:
        resolved = request or RunRetrievalRequest(
            project_root=_require_path(project_root, name="project_root"),
            execution_id=_require_str(execution_id, name="execution_id"),
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
        return self.retrieve_run(replace(resolved, mode="wait")).run

    def observe_run(
        self,
        request: RunRetrievalRequest | None = None,
        *,
        project_root: Path | None = None,
        execution_id: str | None = None,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
        event_sink: ExecutionSink | None = None,
        skip_history: bool = False,
    ) -> RunRetrievalOutcome:
        resolved = request or RunRetrievalRequest(
            project_root=_require_path(project_root, name="project_root"),
            execution_id=_require_str(execution_id, name="execution_id"),
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            event_sink=event_sink,
            skip_history=skip_history,
        )
        return self.retrieve_run(replace(resolved, mode="follow"))

    def follow_run(self, request: RunRetrievalRequest) -> ExecutionRecord:
        outcome = self.observe_run(request)
        if outcome.completion_reason == "window_elapsed":
            raise RunWaitTimedOutError(request.timeout_s)
        return outcome.run

    def cancel_run(
        self,
        request: RunCancelRequest | None = None,
        *,
        project_root: Path | None = None,
        execution_id: str | None = None,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> RunCancelOutcome:
        resolved = request or RunCancelRequest(
            project_root=_require_path(project_root, name="project_root"),
            execution_id=_require_str(execution_id, name="execution_id"),
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
        return self._run_manager.cancel_run(
            project_root=resolved.project_root,
            execution_id=resolved.execution_id,
            timeout_s=resolved.timeout_s,
            poll_interval_s=resolved.poll_interval_s,
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
            active_run = self._run_manager.active_run_for_session(
                project_root=request.project_root,
                session_id=request.session_id,
            )
            if active_run is None:
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
                RunRetrievalRequest(
                    project_root=request.project_root,
                    execution_id=active_run.execution_id,
                    timeout_s=remaining,
                    poll_interval_s=request.poll_interval_s,
                )
            )
            waited_for_run = True


def _require_path(value: Path | None, *, name: str) -> Path:
    if value is None:
        raise TypeError(f"{name} is required")
    return value


def _require_str(value: str | None, *, name: str) -> str:
    if value is None:
        raise TypeError(f"{name} is required")
    return value


__all__ = [
    "ExecutionCommandRequest",
    "ExecutionRecord",
    "ExecutionRun",
    "ExecutionService",
    "ExecutionStore",
    "ManagedExecution",
    "RunCancelRequest",
    "RunListRequest",
    "RunRetrievalOutcome",
    "RunRetrievalRequest",
    "SessionAccessOutcome",
    "SessionAccessProvider",
    "SessionAccessRequest",
    "SessionAccessTarget",
    "StartOutcome",
    "_ExecutionProgressSink",
]
