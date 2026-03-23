from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from .advice import AdviceContext, AdvicePolicy
from .compact import (
    compact_execution_payload,
    compact_history_entry,
    compact_inspect_payload,
    compact_run_entry,
    full_history_entry,
    preview_text,
    strip_ansi_lines,
)
from .contracts import (
    CommandResponse,
    ExecutionSink,
    KernelStatus,
    error_response,
    success_response,
)
from .errors import AgentNBException, KernelWaitTimedOutError
from .execution import ExecutionService, ManagedExecution
from .execution_invocation import ExecInvocationPolicy, ExecSourceKind, OutputSelector
from .introspection import HelperExecutionPolicy
from .ops import NotebookOps
from .payloads import (
    BulkDeleteResult,
    CompactExecPayloadInput,
    DoctorPayload,
    ExecPayload,
    HistoryPayload,
    InspectPreview,
    InspectResponsePayload,
    InterruptPayload,
    NamespaceDeltaEntry,
    NamespaceDeltaPayload,
    ReloadReport,
    RunLookupPayload,
    RunsListPayload,
    RunSnapshot,
    SessionsListPayload,
    SessionSummary,
    StartPayload,
    StatusPayload,
    StopPayload,
    VarDisplayEntry,
    VarsPayload,
)
from .runtime import (
    KernelRuntime,
    KernelWaitResult,
    RuntimeState,
    RuntimeStateKind,
)
from .selectors import (
    HistoryReference,
    HistorySelectorResolver,
    RunDefaultBehavior,
    RunReference,
    RunSelectorResolver,
)
from .session import DEFAULT_SESSION_ID
from .session_targeting import SessionTargetingPolicy
from .state import CommandLockInfo

StatusWaitFor = Literal["ready", "idle"]
_ACTIVE_RUN_STATUSES = frozenset({"starting", "running"})
_HELPER_EXECUTION_POLICY = HelperExecutionPolicy(
    ensure_started=True,
    wait_for_usable=True,
    retry_on_busy=True,
)


@dataclass(slots=True, frozen=True, kw_only=True)
class ProjectRequest:
    project_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionRequest(ProjectRequest):
    session_id: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ExecRequest(SessionRequest):
    code: str
    timeout_s: float = 30.0
    source_kind: ExecSourceKind = "argument"
    source_path: Path | None = None
    invocation: ExecInvocationPolicy = field(default_factory=ExecInvocationPolicy)


@dataclass(slots=True, frozen=True, kw_only=True)
class StartRequest(SessionRequest):
    python_executable: Path | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class StatusRequest(SessionRequest):
    wait_for: StatusWaitFor | None = None
    timeout_s: float = 30.0


@dataclass(slots=True, frozen=True, kw_only=True)
class WaitRequest(SessionRequest):
    timeout_s: float = 30.0


@dataclass(slots=True, frozen=True, kw_only=True)
class VarsRequest(SessionRequest):
    include_types: bool = True
    match_text: str | None = None
    recent: int | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class InspectRequest(SessionRequest):
    name: str


@dataclass(slots=True, frozen=True, kw_only=True)
class ReloadRequest(SessionRequest):
    module_name: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class HistoryRequest(SessionRequest):
    reference: HistoryReference | None = None
    errors: bool = False
    successes: bool = False
    latest: bool = False
    last: int | None = None
    include_internal: bool = False
    full: bool = False


@dataclass(slots=True, frozen=True, kw_only=True)
class InterruptRequest(SessionRequest):
    pass


@dataclass(slots=True, frozen=True, kw_only=True)
class ResetRequest(SessionRequest):
    timeout_s: float = 10.0


@dataclass(slots=True, frozen=True, kw_only=True)
class StopRequest(SessionRequest):
    pass


@dataclass(slots=True, frozen=True, kw_only=True)
class DoctorRequest(SessionRequest):
    python_executable: Path | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionsListRequest(ProjectRequest):
    pass


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionsDeleteRequest(ProjectRequest):
    session_name: str


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionsDeleteBulkRequest(ProjectRequest):
    stale_only: bool = False


@dataclass(slots=True, frozen=True, kw_only=True)
class RunsListRequest(SessionRequest):
    errors: bool = False
    last: int | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class RunLookupRequest(ProjectRequest):
    run_reference: RunReference | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class RunsWaitRequest(RunLookupRequest):
    timeout_s: float = 30.0


@dataclass(slots=True, frozen=True, kw_only=True)
class RunsFollowRequest(RunLookupRequest):
    timeout_s: float = 30.0
    tail: bool = False


@dataclass(slots=True, frozen=True, kw_only=True)
class RunsCancelRequest(RunLookupRequest):
    pass


class AgentNBApp:
    def __init__(
        self,
        *,
        runtime: KernelRuntime | None = None,
        executions: ExecutionService | None = None,
        ops: NotebookOps | None = None,
        advisor: AdvicePolicy | None = None,
        run_selectors: RunSelectorResolver | None = None,
        history_selectors: HistorySelectorResolver | None = None,
        session_targeting: SessionTargetingPolicy | None = None,
    ) -> None:
        resolved_runtime = runtime or KernelRuntime()
        resolved_executions = executions or ExecutionService(resolved_runtime)
        self.runtime = resolved_runtime
        self.executions = resolved_executions
        self.ops = ops or NotebookOps(resolved_runtime, executions=resolved_executions)
        self.advisor = advisor or AdvicePolicy()
        self.run_selectors = run_selectors or RunSelectorResolver(self.executions)
        self.history_selectors = history_selectors or HistorySelectorResolver()
        self.session_targeting = session_targeting or SessionTargetingPolicy(resolved_runtime)

    def start(self, request: StartRequest) -> CommandResponse:
        return self._handle_command(
            command_name="start",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=False,
            announce_session_switch=True,
            handler=lambda session_id: self._start_payload(request=request, session_id=session_id),
        )

    def exec(
        self,
        request: ExecRequest,
        *,
        event_sink: ExecutionSink | None = None,
    ) -> CommandResponse:
        validation_error = self._validate_exec_request(request)
        if validation_error is not None:
            return validation_error

        return self._handle_command(
            command_name="exec",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            announce_session_switch=True,
            handler=lambda session_id: self._exec_payload(
                request=request,
                session_id=session_id,
                event_sink=event_sink,
            ),
        )

    def status(self, request: StatusRequest) -> CommandResponse:
        return self._handle_command(
            command_name="status",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            announce_session_switch=True,
            handler=lambda session_id: self._status_payload(request=request, session_id=session_id),
        )

    def wait(self, request: WaitRequest) -> CommandResponse:
        return self._handle_command(
            command_name="wait",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            announce_session_switch=True,
            handler=lambda session_id: self._wait_payload(request=request, session_id=session_id),
        )

    def vars(self, request: VarsRequest) -> CommandResponse:
        return self._handle_command(
            command_name="vars",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            project_starting_state=True,
            announce_session_switch=True,
            handler=lambda session_id: self._vars_payload(request=request, session_id=session_id),
        )

    def inspect(self, request: InspectRequest) -> CommandResponse:
        return self._handle_command(
            command_name="inspect",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            project_starting_state=True,
            announce_session_switch=True,
            handler=lambda session_id: self._inspect_payload(
                request=request,
                session_id=session_id,
            ),
        )

    def reload(self, request: ReloadRequest) -> CommandResponse:
        return self._handle_command(
            command_name="reload",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            project_starting_state=True,
            announce_session_switch=True,
            handler=lambda session_id: self._reload_payload(request=request, session_id=session_id),
        )

    def history(self, request: HistoryRequest) -> CommandResponse:
        validation_error = self._validate_history_request(request)
        if validation_error is not None:
            return validation_error

        return self._handle_command(
            command_name="history",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            announce_session_switch=True,
            handler=lambda session_id: self._history_payload(
                request=request,
                session_id=session_id,
            ),
        )

    def interrupt(self, request: InterruptRequest) -> CommandResponse:
        return self._handle_command(
            command_name="interrupt",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            announce_session_switch=True,
            handler=lambda session_id: self._interrupt_payload(
                request=request, session_id=session_id
            ),
        )

    def reset(self, request: ResetRequest) -> CommandResponse:
        return self._handle_command(
            command_name="reset",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            announce_session_switch=True,
            handler=lambda session_id: self._reset_payload(request=request, session_id=session_id),
        )

    def stop(self, request: StopRequest) -> CommandResponse:
        return self._handle_command(
            command_name="stop",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            announce_session_switch=True,
            handler=lambda session_id: self._stop_payload(request=request, session_id=session_id),
        )

    def doctor(self, request: DoctorRequest) -> CommandResponse:
        return self._handle_command(
            command_name="doctor",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=False,
            handler=lambda session_id: self._doctor_payload(request=request, session_id=session_id),
        )

    def sessions_list(self, request: SessionsListRequest) -> CommandResponse:
        return self._handle_command(
            command_name="sessions-list",
            project_root=request.project_root,
            requested_session_id=None,
            require_live_session=False,
            handler=lambda _: _sessions_list_payload(
                self.runtime.list_sessions(project_root=request.project_root),
                hidden_non_live_count=self.runtime.hidden_non_live_session_count(
                    project_root=request.project_root
                ),
            ),
        )

    def sessions_delete(self, request: SessionsDeleteRequest) -> CommandResponse:
        return self._handle_command(
            command_name="sessions-delete",
            project_root=request.project_root,
            requested_session_id=request.session_name,
            require_live_session=False,
            handler=lambda _: self.runtime.delete_session(
                project_root=request.project_root,
                session_id=request.session_name,
            ),
        )

    def sessions_delete_bulk(self, request: SessionsDeleteBulkRequest) -> CommandResponse:
        return self._handle_command(
            command_name="sessions-delete-bulk",
            project_root=request.project_root,
            requested_session_id=None,
            require_live_session=False,
            handler=lambda _: self._sessions_delete_bulk_payload(request),
        )

    def _sessions_delete_bulk_payload(self, request: SessionsDeleteBulkRequest) -> BulkDeleteResult:
        if request.stale_only:
            deleted = self.runtime.cleanup_stale_sessions(project_root=request.project_root)
            return {"deleted": deleted, "count": len(deleted)}

        deleted: list[str] = []
        for session in self.runtime.session_inventory(project_root=request.project_root):
            sid = session.session_id
            try:
                self.runtime.delete_session(project_root=request.project_root, session_id=sid)
                deleted.append(sid)
            except Exception:
                pass
        return {"deleted": deleted, "count": len(deleted)}

    def runs_list(self, request: RunsListRequest) -> CommandResponse:
        return self._handle_command(
            command_name="runs-list",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=False,
            handler=lambda _: self._runs_list_payload(request=request),
        )

    def runs_show(self, request: RunLookupRequest) -> CommandResponse:
        return self._handle_command(
            command_name="runs-show",
            project_root=request.project_root,
            requested_session_id=None,
            require_live_session=False,
            handler=lambda _: self._run_lookup_payload(
                project_root=request.project_root,
                run_reference=request.run_reference,
                timeout_s=None,
                event_sink=None,
                default_behavior="latest",
            ),
        )

    def runs_wait(self, request: RunsWaitRequest) -> CommandResponse:
        return self._handle_command(
            command_name="runs-wait",
            project_root=request.project_root,
            requested_session_id=None,
            require_live_session=False,
            handler=lambda _: self._run_lookup_payload(
                project_root=request.project_root,
                run_reference=request.run_reference,
                timeout_s=request.timeout_s,
                event_sink=None,
                default_behavior="active",
            ),
        )

    def runs_follow(
        self,
        request: RunsFollowRequest,
        *,
        event_sink: ExecutionSink | None = None,
    ) -> CommandResponse:
        return self._handle_command(
            command_name="runs-follow",
            project_root=request.project_root,
            requested_session_id=None,
            require_live_session=False,
            handler=lambda _: self._run_lookup_payload(
                project_root=request.project_root,
                run_reference=request.run_reference,
                timeout_s=request.timeout_s,
                event_sink=event_sink,
                default_behavior="active",
                skip_history=True,
            ),
            response_session_id_resolver=_run_response_session_id,
        )

    def runs_cancel(self, request: RunsCancelRequest) -> CommandResponse:
        return self._handle_command(
            command_name="runs-cancel",
            project_root=request.project_root,
            requested_session_id=None,
            require_live_session=False,
            handler=lambda _: self.executions.cancel_run(
                project_root=request.project_root,
                execution_id=self.run_selectors.resolve_execution_id(
                    project_root=request.project_root,
                    reference=request.run_reference,
                    current_session_id=self.session_targeting.current_run_preference(
                        project_root=request.project_root,
                    ),
                    default_behavior="active",
                ),
            ),
        )

    def _start_payload(self, *, request: StartRequest, session_id: str) -> StartPayload:
        status, started_new = self.runtime.start(
            project_root=request.project_root,
            session_id=session_id,
            python_executable=request.python_executable,
        )
        payload = cast(StartPayload, _kernel_status_payload(status))
        payload["started_new"] = started_new
        return payload

    def _exec_payload(
        self,
        *,
        request: ExecRequest,
        session_id: str,
        event_sink: ExecutionSink | None,
    ) -> ExecPayload:
        invocation = request.invocation
        payload_builder = _ExecPayloadBuilder(
            runtime=self.runtime,
            ops=self.ops,
            request=request,
            session_id=session_id,
        )
        if invocation.is_background:
            managed = self.executions.start_background_code(
                project_root=request.project_root,
                session_id=session_id,
                code=request.code,
                ensure_started=invocation.ensure_started,
            )
        else:
            managed = self.executions.execute_code(
                project_root=request.project_root,
                session_id=session_id,
                code=request.code,
                timeout_s=request.timeout_s,
                ensure_started=invocation.ensure_started,
                event_sink=invocation.streaming_sink(event_sink),
            )

        payload = payload_builder.build(managed)

        if not invocation.is_background and managed.record.status == "error":
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message="Execution failed",
                ename=managed.record.ename,
                evalue=managed.record.evalue,
                traceback=managed.record.traceback,
                data=dict(payload),
            )
        return payload

    def _status_payload(self, *, request: StatusRequest, session_id: str) -> StatusPayload:
        if request.wait_for == "idle":
            wait_result = self._wait_for_idle_status(
                project_root=request.project_root,
                session_id=session_id,
                timeout_s=request.timeout_s,
            )
            payload = _kernel_status_payload(
                wait_result.status,
                runtime_state=wait_result.runtime_state,
                session_exists=True,
            )
            payload["waited"] = wait_result.waited
            payload["waited_for"] = wait_result.waited_for or "idle"
            waited_ms = getattr(wait_result, "waited_ms", None)
            payload["waited_ms"] = waited_ms if isinstance(waited_ms, int) else 0
            initial_runtime_state = getattr(wait_result, "initial_runtime_state", None)
            if isinstance(initial_runtime_state, str):
                payload["initial_runtime_state"] = initial_runtime_state
            return payload
        if request.wait_for == "ready":
            wait_result = self.runtime.wait_until_ready(
                project_root=request.project_root,
                session_id=session_id,
                timeout_s=request.timeout_s,
            )
            payload = _kernel_status_payload(
                wait_result.status,
                runtime_state=wait_result.runtime_state,
                session_exists=True,
            )
            payload["waited"] = wait_result.waited
            payload["waited_for"] = wait_result.waited_for or "ready"
            waited_ms = getattr(wait_result, "waited_ms", None)
            payload["waited_ms"] = waited_ms if isinstance(waited_ms, int) else 0
            initial_runtime_state = getattr(wait_result, "initial_runtime_state", None)
            if isinstance(initial_runtime_state, str):
                payload["initial_runtime_state"] = initial_runtime_state
            return payload
        return _status_payload_from_runtime_state(
            self.runtime.runtime_state(
                project_root=request.project_root,
                session_id=session_id,
            )
        )

    def _wait_for_idle_status(
        self,
        *,
        project_root: Path,
        session_id: str,
        timeout_s: float,
    ) -> KernelWaitResult:
        started_at = time.monotonic()
        waited_for_run = False
        initial_runtime_state: RuntimeStateKind | None = None
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KernelWaitTimedOutError(timeout_s, waiting_for="idle")
            wait_result = self.runtime.wait_until_idle(
                project_root=project_root,
                session_id=session_id,
                timeout_s=remaining,
            )
            if initial_runtime_state is None:
                initial_runtime_state = (
                    wait_result.initial_runtime_state or wait_result.runtime_state
                )
            active_execution_id = self._active_execution_id_for_session(
                project_root=project_root,
                session_id=session_id,
            )
            if active_execution_id is None:
                if waited_for_run:
                    waited_ms = max(
                        wait_result.waited_ms,
                        int((time.monotonic() - started_at) * 1000),
                    )
                    return KernelWaitResult(
                        status=wait_result.status,
                        waited=True,
                        waited_for=wait_result.waited_for or "idle",
                        runtime_state=wait_result.runtime_state,
                        waited_ms=waited_ms,
                        initial_runtime_state=initial_runtime_state,
                    )
                return wait_result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KernelWaitTimedOutError(timeout_s, waiting_for="idle")
            self.executions.wait_for_run(
                project_root=project_root,
                execution_id=active_execution_id,
                timeout_s=remaining,
            )
            waited_for_run = True

    def _active_execution_id_for_session(
        self,
        *,
        project_root: Path,
        session_id: str,
    ) -> str | None:
        runs = self.executions.list_runs(
            project_root=project_root,
            session_id=session_id,
        )
        for run in reversed(runs):
            status = run.get("status")
            execution_id = run.get("execution_id")
            if status in _ACTIVE_RUN_STATUSES and isinstance(execution_id, str) and execution_id:
                return execution_id
        return None

    def _wait_payload(self, *, request: WaitRequest, session_id: str) -> StatusPayload:
        wait_result = self.runtime.wait_for_usable(
            project_root=request.project_root,
            session_id=session_id,
            timeout_s=request.timeout_s,
        )
        payload = _kernel_status_payload(
            wait_result.status,
            runtime_state=wait_result.runtime_state,
            session_exists=wait_result.status.alive,
        )
        payload["waited"] = wait_result.waited
        if wait_result.waited_for is not None:
            payload["waited_for"] = wait_result.waited_for
        waited_ms = getattr(wait_result, "waited_ms", None)
        payload["waited_ms"] = waited_ms if isinstance(waited_ms, int) else 0
        initial_runtime_state = getattr(wait_result, "initial_runtime_state", None)
        if isinstance(initial_runtime_state, str):
            payload["initial_runtime_state"] = initial_runtime_state
        return payload

    def _vars_payload(self, *, request: VarsRequest, session_id: str) -> VarsPayload:
        helper_result = self.ops.list_vars_result(
            project_root=request.project_root,
            session_id=session_id,
            execution_policy=_HELPER_EXECUTION_POLICY,
        )
        values = helper_result.payload
        if request.match_text:
            match_lower = request.match_text.lower()
            values = [item for item in values if match_lower in str(item["name"]).lower()]
        if request.recent is not None:
            values = values[-request.recent :]
        if not request.include_types:
            display_values: list[VarDisplayEntry] = [
                {"name": item["name"], "repr": item["repr"]} for item in values
            ]
            payload = cast(VarsPayload, {"vars": display_values})
            payload.update(helper_result.access_metadata.merge_data())
            return payload
        payload = cast(VarsPayload, {"vars": cast(list[VarDisplayEntry], values)})
        payload.update(helper_result.access_metadata.merge_data())
        return payload

    def _inspect_payload(
        self, *, request: InspectRequest, session_id: str
    ) -> InspectResponsePayload:
        helper_result = self.ops.inspect_var_result(
            project_root=request.project_root,
            session_id=session_id,
            name=request.name,
            execution_policy=_HELPER_EXECUTION_POLICY,
        )
        payload = cast(
            InspectResponsePayload,
            {"inspect": compact_inspect_payload(helper_result.payload)},
        )
        payload.update(helper_result.access_metadata.merge_data())
        return payload

    def _reload_payload(self, *, request: ReloadRequest, session_id: str) -> ReloadReport:
        helper_result = self.ops.reload_module_result(
            project_root=request.project_root,
            session_id=session_id,
            module_name=request.module_name,
            execution_policy=_HELPER_EXECUTION_POLICY,
        )
        payload = dict(helper_result.payload)
        payload.update(helper_result.access_metadata.merge_data())
        return cast(ReloadReport, payload)

    def _history_payload(self, *, request: HistoryRequest, session_id: str) -> HistoryPayload:
        selection = self.runtime.select_history(
            project_root=request.project_root,
            query=self.history_selectors.resolve_query(
                session_id=session_id,
                include_internal=request.include_internal,
                errors_only=request.errors,
                success_only=request.successes,
                latest=request.latest,
                last=request.last,
                reference=request.reference,
            ),
        )
        entries = selection.entries
        formatter = full_history_entry if request.full else compact_history_entry
        entries = [formatter(entry) for entry in entries]
        return {"entries": entries}

    def _interrupt_payload(self, *, request: InterruptRequest, session_id: str) -> InterruptPayload:
        self.runtime.interrupt(project_root=request.project_root, session_id=session_id)
        return {"interrupted": True}

    def _reset_payload(self, *, request: ResetRequest, session_id: str) -> ExecPayload:
        managed = self.executions.reset_session(
            project_root=request.project_root,
            session_id=session_id,
            timeout_s=request.timeout_s,
        )
        payload = compact_execution_payload(
            cast(CompactExecPayloadInput, managed.record.to_execution_payload())
        )
        if managed.record.status == "error":
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message="Reset failed",
                ename=managed.record.ename,
                evalue=managed.record.evalue,
                traceback=managed.record.traceback,
                data=dict(payload),
            )
        return payload

    def _stop_payload(self, *, request: StopRequest, session_id: str) -> StopPayload:
        self.runtime.stop(project_root=request.project_root, session_id=session_id)
        return {"stopped": True}

    def _doctor_payload(self, *, request: DoctorRequest, session_id: str) -> DoctorPayload:
        return self.runtime.doctor(
            project_root=request.project_root,
            session_id=session_id,
            python_executable=request.python_executable,
        )

    def _runs_list_payload(self, *, request: RunsListRequest) -> RunsListPayload:
        entries = self.executions.list_runs(
            project_root=request.project_root,
            session_id=request.session_id if request.session_id is not None else None,
            errors_only=request.errors,
        )
        compacted = [compact_run_entry(entry) for entry in entries]
        if request.last is not None:
            compacted = compacted[-request.last :]
        return {"runs": compacted}

    def _run_lookup_payload(
        self,
        *,
        project_root: Path,
        run_reference: RunReference | None,
        timeout_s: float | None,
        event_sink: ExecutionSink | None,
        default_behavior: RunDefaultBehavior,
        skip_history: bool = False,
    ) -> RunLookupPayload:
        execution_id = self.run_selectors.resolve_execution_id(
            project_root=project_root,
            reference=run_reference,
            current_session_id=self.session_targeting.current_run_preference(
                project_root=project_root
            ),
            default_behavior=default_behavior,
        )
        observation = None
        if event_sink is not None:
            observation = self.executions.observe_run(
                project_root=project_root,
                execution_id=execution_id,
                timeout_s=30.0 if timeout_s is None else timeout_s,
                event_sink=event_sink,
                skip_history=skip_history,
            )
            run = observation.run
        elif timeout_s is not None:
            run = self.executions.wait_for_run(
                project_root=project_root,
                execution_id=execution_id,
                timeout_s=timeout_s,
            )
        else:
            run = self.executions.get_run(
                project_root=project_root,
                execution_id=execution_id,
            )
        payload: RunLookupPayload = {
            "run": _public_run_payload(
                run,
                include_output=not (observation is not None and skip_history),
            )
        }
        status = run.get("status")
        if isinstance(status, str):
            payload["status"] = status
        if observation is not None:
            payload["completion_reason"] = observation.completion_reason
            payload["replayed_event_count"] = observation.replayed_event_count
            payload["emitted_event_count"] = observation.emitted_event_count
        return payload

    def _validate_exec_request(self, request: ExecRequest) -> CommandResponse | None:
        message = request.invocation.validation_error()
        if message is not None:
            return self._input_error(
                command_name="exec",
                project_root=request.project_root,
                session_id=request.session_id,
                message=message,
            )
        return None

    def _validate_history_request(self, request: HistoryRequest) -> CommandResponse | None:
        try:
            self.history_selectors.resolve_query(
                session_id=request.session_id,
                include_internal=request.include_internal,
                errors_only=request.errors,
                success_only=request.successes,
                latest=request.latest,
                last=request.last,
                reference=request.reference,
            )
        except ValueError as exc:
            return self._input_error(
                command_name="history",
                project_root=request.project_root,
                session_id=request.session_id,
                message=str(exc),
            )
        return None

    def _input_error(
        self,
        *,
        command_name: str,
        project_root: Path,
        session_id: str | None,
        message: str,
    ) -> CommandResponse:
        return error_response(
            command=command_name,
            project=str(project_root),
            session_id=session_id or DEFAULT_SESSION_ID,
            code="INVALID_INPUT",
            message=message,
            suggestions=self.advisor.suggestions(
                AdviceContext(
                    command_name=command_name,
                    response_status="error",
                    data={},
                    error_code="INVALID_INPUT",
                )
            ),
            suggestion_actions=self.advisor.suggestion_actions(
                AdviceContext(
                    command_name=command_name,
                    response_status="error",
                    data={},
                    error_code="INVALID_INPUT",
                )
            ),
        )

    def _handle_command(
        self,
        *,
        command_name: str,
        project_root: Path,
        requested_session_id: str | None,
        require_live_session: bool,
        handler: Callable[[str], Mapping[str, object]],
        project_starting_state: bool = False,
        announce_session_switch: bool = False,
        response_session_id_resolver: Callable[[str, Mapping[str, object]], str] | None = None,
    ) -> CommandResponse:
        response_session_id = requested_session_id or DEFAULT_SESSION_ID

        try:
            decision = self.session_targeting.resolve_command_target(
                project_root=project_root,
                requested_session_id=requested_session_id,
                require_live_session=require_live_session,
                persist_explicit_preference=_should_persist_explicit_session_preference(
                    command_name
                ),
                announce_switch=announce_session_switch,
            )
            resolved_session_id = decision.session_id
            response_session_id = resolved_session_id
            if project_starting_state:
                state = self.runtime.runtime_state(
                    project_root=project_root,
                    session_id=resolved_session_id,
                )
                if state.kind == "starting":
                    error = AgentNBException(
                        code="KERNEL_NOT_READY",
                        message=(
                            "Kernel startup is still in progress or not yet ready. Wait and retry."
                        ),
                        data=dict(_status_payload_from_runtime_state(state)),
                    )
                    return error_response(
                        command=command_name,
                        project=str(project_root),
                        session_id=response_session_id,
                        code=error.code,
                        message=error.message,
                        ename=error.ename,
                        evalue=error.evalue,
                        traceback=error.traceback,
                        data=error.data,
                        suggestions=self.advisor.suggestions(
                            AdviceContext(
                                command_name=command_name,
                                response_status="error",
                                data=error.data,
                                error_code=error.code,
                                error_name=error.ename,
                                error_value=error.evalue,
                                session_id=response_session_id,
                            )
                        ),
                        suggestion_actions=self.advisor.suggestion_actions(
                            AdviceContext(
                                command_name=command_name,
                                response_status="error",
                                data=error.data,
                                error_code=error.code,
                                error_name=error.ename,
                                error_value=error.evalue,
                                session_id=response_session_id,
                            )
                        ),
                    )
            data = handler(resolved_session_id)
            if decision.switched_session is not None:
                data = dict(data)
                data["switched_session"] = decision.switched_session
            if response_session_id_resolver is not None:
                response_session_id = response_session_id_resolver(response_session_id, data)
            return success_response(
                command=command_name,
                project=str(project_root),
                session_id=response_session_id,
                data=data,
                suggestions=self.advisor.suggestions(
                    AdviceContext(
                        command_name=command_name,
                        response_status="ok",
                        data=data,
                        session_id=resolved_session_id,
                    )
                ),
                suggestion_actions=self.advisor.suggestion_actions(
                    AdviceContext(
                        command_name=command_name,
                        response_status="ok",
                        data=data,
                        session_id=resolved_session_id,
                    )
                ),
            )
        except AgentNBException as exc:
            return error_response(
                command=command_name,
                project=str(project_root),
                session_id=response_session_id,
                code=exc.code,
                message=exc.message,
                ename=exc.ename,
                evalue=exc.evalue,
                traceback=exc.traceback,
                data=exc.data,
                suggestions=self.advisor.suggestions(
                    AdviceContext(
                        command_name=command_name,
                        response_status="error",
                        data=exc.data,
                        error_code=exc.code,
                        error_name=exc.ename,
                        error_value=exc.evalue,
                        session_id=response_session_id,
                    )
                ),
                suggestion_actions=self.advisor.suggestion_actions(
                    AdviceContext(
                        command_name=command_name,
                        response_status="error",
                        data=exc.data,
                        error_code=exc.code,
                        error_name=exc.ename,
                        error_value=exc.evalue,
                        session_id=response_session_id,
                    )
                ),
            )
        except Exception as exc:
            return error_response(
                command=command_name,
                project=str(project_root),
                session_id=response_session_id,
                code="INTERNAL_ERROR",
                message=str(exc),
                ename=type(exc).__name__,
                evalue=str(exc),
                suggestions=self.advisor.suggestions(
                    AdviceContext(
                        command_name=command_name,
                        response_status="error",
                        data={},
                        error_code="INTERNAL_ERROR",
                        error_name=type(exc).__name__,
                        error_value=str(exc),
                        session_id=response_session_id,
                    )
                ),
                suggestion_actions=self.advisor.suggestion_actions(
                    AdviceContext(
                        command_name=command_name,
                        response_status="error",
                        data={},
                        error_code="INTERNAL_ERROR",
                        error_name=type(exc).__name__,
                        error_value=str(exc),
                        session_id=response_session_id,
                    )
                ),
            )


def _run_response_session_id(current_session_id: str, data: Mapping[str, object]) -> str:
    run = data.get("run")
    if isinstance(run, dict):
        run_payload = cast(Mapping[str, object], run)
        session_id = run_payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            return session_id
    return current_session_id


def _should_persist_explicit_session_preference(command_name: str) -> bool:
    return command_name in {
        "start",
        "exec",
        "status",
        "wait",
        "vars",
        "inspect",
        "reload",
        "history",
        "interrupt",
        "reset",
        "stop",
    }


def select_exec_output(payload: Mapping[str, object], selector: OutputSelector) -> str:
    if selector == "result":
        preview = payload.get("result_preview")
        result = payload.get("result")
        if isinstance(preview, dict) and _prefer_preview_text(preview, result):
            return preview_text(cast(InspectPreview, preview))
        result = payload.get("result")
        return "" if result is None else str(result)
    value = payload.get(selector)
    return "" if value is None else str(value)


def _prefer_preview_text(preview: object, result: object) -> bool:
    if isinstance(preview, dict):
        preview_map = cast(dict[str, object], preview)
        if preview_map.get("kind") == "dataframe-like":
            return True
    if not isinstance(result, str):
        return True
    return "\n" in result or len(result) > 120


def _status_payload_from_runtime_state(state: RuntimeState) -> StatusPayload:
    return _kernel_status_payload(
        state.to_kernel_status(),
        runtime_state=state.kind,
        session_exists=state.session_exists,
        command_lock=state.command_lock,
    )


def _kernel_status_payload(
    status: KernelStatus,
    *,
    runtime_state: RuntimeStateKind | None = None,
    session_exists: bool | None = None,
    command_lock: CommandLockInfo | None = None,
) -> StatusPayload:
    payload: StatusPayload = {
        "alive": status.alive,
        "pid": status.pid,
        "connection_file": status.connection_file,
        "started_at": status.started_at,
        "uptime_s": status.uptime_s,
        "python": status.python,
        "busy": status.busy,
    }
    if runtime_state is not None:
        payload["runtime_state"] = runtime_state
    if session_exists is not None:
        payload["session_exists"] = session_exists
    if command_lock is not None:
        payload["lock_pid"] = command_lock.pid
        if command_lock.acquired_at is not None:
            payload["lock_acquired_at"] = command_lock.acquired_at
        busy_for_ms = command_lock.busy_for_ms()
        if busy_for_ms is not None:
            payload["busy_for_ms"] = busy_for_ms
    return payload


def _runtime_state_from_status(status: KernelStatus) -> RuntimeStateKind:
    if not status.alive:
        return "missing"
    if status.busy:
        return "busy"
    return "ready"


def _sessions_list_payload(
    sessions: list[SessionSummary],
    *,
    hidden_non_live_count: int = 0,
) -> SessionsListPayload:
    payload: SessionsListPayload = {"sessions": sessions}
    if hidden_non_live_count > 0:
        payload["hidden_non_live_count"] = hidden_non_live_count
    return payload


@dataclass(slots=True)
class _ExecPayloadBuilder:
    runtime: KernelRuntime
    ops: NotebookOps
    request: ExecRequest
    session_id: str
    namespace_before: list[VarDisplayEntry] | None = field(init=False)

    def __post_init__(self) -> None:
        self.namespace_before = self._file_exec_namespace_snapshot()

    def build(self, managed: ManagedExecution) -> ExecPayload:
        invocation = self.request.invocation
        payload = compact_execution_payload(
            cast(CompactExecPayloadInput, managed.record.to_execution_payload()),
            no_truncate=invocation.no_truncate,
        )
        payload["source_kind"] = self.request.source_kind
        if self.request.source_path is not None:
            payload["source_path"] = str(self.request.source_path)
        if invocation.is_background:
            payload["background"] = True
        if invocation.ensure_started:
            payload["ensured_started"] = True
            payload["started_new_session"] = managed.start_outcome.started_new_session
            if managed.start_outcome.initial_runtime_state is not None:
                payload["initial_runtime_state"] = managed.start_outcome.initial_runtime_state
            if managed.start_outcome.session_restarted:
                payload["session_restarted"] = True
        if invocation.output_selector is not None:
            payload["selected_output"] = invocation.output_selector
            payload["selected_text"] = select_exec_output(payload, invocation.output_selector)
        session_python = self._session_python()
        if session_python is not None:
            payload["session_python"] = session_python
        namespace_delta = self._file_exec_namespace_delta(payload)
        if namespace_delta is not None:
            payload["namespace_delta"] = namespace_delta
        return payload

    def _file_exec_namespace_snapshot(self) -> list[VarDisplayEntry] | None:
        if self.request.source_kind != "file" or self.request.invocation.is_background:
            return None
        state = self.runtime.runtime_state(
            project_root=self.request.project_root,
            session_id=self.session_id,
        )
        if state.kind in {"missing", "dead", "stale"}:
            return []
        if state.kind == "starting":
            return None
        return self._ephemeral_vars_snapshot(
            ensure_started=self.request.invocation.ensure_started,
            wait_for_usable=True,
        )

    def _file_exec_namespace_delta(self, payload: ExecPayload) -> NamespaceDeltaPayload | None:
        if self.request.source_kind != "file" or self.request.invocation.is_background:
            return None
        if payload.get("status") == "error":
            return None
        if self.namespace_before is None or not _exec_payload_is_empty(payload):
            return None
        after = self._ephemeral_vars_snapshot(
            ensure_started=False,
            wait_for_usable=False,
        )
        if after is None:
            return None
        return _namespace_delta(before=self.namespace_before, after=after)

    def _ephemeral_vars_snapshot(
        self,
        *,
        ensure_started: bool,
        wait_for_usable: bool,
    ) -> list[VarDisplayEntry] | None:
        try:
            helper_result = self.ops.list_vars_result(
                project_root=self.request.project_root,
                session_id=self.session_id,
                execution_policy=HelperExecutionPolicy(
                    ensure_started=ensure_started,
                    wait_for_usable=wait_for_usable,
                    retry_on_busy=wait_for_usable,
                    record_history=False,
                ),
            )
        except AgentNBException:
            return None
        return cast(list[VarDisplayEntry], helper_result.payload)

    def _session_python(self) -> str | None:
        state = self.runtime.runtime_state(
            project_root=self.request.project_root,
            session_id=self.session_id,
        )
        if state.session is not None and state.session.python_executable:
            return state.session.python_executable
        status = state.to_kernel_status()
        if status.python:
            return status.python
        return None


def _exec_payload_is_empty(payload: ExecPayload) -> bool:
    for key in ("result", "stdout", "stderr", "selected_text"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return False
    namespace_delta = payload.get("namespace_delta")
    entries = namespace_delta.get("entries") if isinstance(namespace_delta, dict) else None
    return not entries


def _namespace_delta(
    *,
    before: list[VarDisplayEntry],
    after: list[VarDisplayEntry],
) -> NamespaceDeltaPayload | None:
    before_by_name = {
        str(item.get("name")): item
        for item in before
        if isinstance(item.get("name"), str) and item.get("name")
    }
    changed: list[NamespaceDeltaEntry] = []
    new_count = 0
    updated_count = 0
    for item in after:
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        previous = before_by_name.get(name)
        current_repr = item.get("repr")
        current_type = item.get("type")
        change: str | None = None
        if previous is None:
            change = "new"
            new_count += 1
        elif previous.get("repr") != current_repr or previous.get("type") != current_type:
            change = "updated"
            updated_count += 1
        if change is None:
            continue
        changed.append(
            {
                "name": name,
                "type": str(current_type or ""),
                "repr": str(current_repr or ""),
                "change": change,
            }
        )
    if not changed:
        return None
    limited = changed[:5]
    return {
        "entries": limited,
        "new_count": new_count,
        "updated_count": updated_count,
        "truncated": len(changed) > len(limited),
    }


def _public_run_payload(run: Mapping[str, object], *, include_output: bool = True) -> RunSnapshot:
    hidden_keys = {"outputs"}
    if not include_output:
        hidden_keys.update({"stdout", "stderr", "result", "events"})
    payload = {key: value for key, value in run.items() if key not in hidden_keys}
    for key in ("traceback", "recorded_traceback"):
        value = payload.get(key)
        if isinstance(value, list):
            payload[key] = strip_ansi_lines(cast(list[str], value))
    events = payload.get("events")
    if isinstance(events, list):
        sanitized_events: list[dict[str, object]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            sanitized_event = dict(event)
            metadata = sanitized_event.get("metadata")
            if isinstance(metadata, dict):
                sanitized_metadata = dict(metadata)
                traceback = sanitized_metadata.get("traceback")
                if isinstance(traceback, list):
                    sanitized_metadata["traceback"] = strip_ansi_lines(cast(list[str], traceback))
                sanitized_event["metadata"] = sanitized_metadata
            sanitized_events.append(sanitized_event)
        payload["events"] = sanitized_events
    return cast(RunSnapshot, payload)
