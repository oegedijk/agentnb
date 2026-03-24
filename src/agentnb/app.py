from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from .advice import AdviceContext, AdvicePolicy
from .command_data import (
    CommandDataLike,
    ExecCommandData,
    HistoryCommandData,
    InspectCommandData,
    KernelSessionData,
    ReloadCommandData,
    RunListEntryData,
    RunLookupCommandData,
    RunsListCommandData,
    RunSnapshotData,
    VarsCommandData,
    normalize_run_payload,
    run_lookup_session_id,
    with_switched_session,
)
from .contracts import (
    CommandResponse,
    ExecutionSink,
    error_response,
    success_response,
)
from .errors import AgentNBException
from .execution import ExecutionService, ManagedExecution, SessionAccessOutcome
from .execution_invocation import ExecInvocationPolicy, ExecSourceKind
from .introspection import HelperExecutionPolicy
from .ops import NotebookOps
from .payloads import (
    BulkDeleteResult,
    DoctorPayload,
    InterruptPayload,
    NamespaceDeltaEntry,
    NamespaceDeltaPayload,
    SessionsListPayload,
    SessionSummary,
    StopPayload,
    VarDisplayEntry,
)
from .response_serialization import selected_exec_output, serialize_command_data
from .runtime import (
    KernelRuntime,
)
from .selectors import (
    HistoryReference,
    HistorySelectorResolver,
    RunDefaultBehavior,
    RunReference,
    RunSelectorResolver,
)
from .session import DEFAULT_SESSION_ID
from .session_targeting import (
    CommandSemantics,
    ResolutionSource,
    SessionTargetingPolicy,
)

StatusWaitFor = Literal["ready", "idle"]
_HELPER_EXECUTION_POLICY = HelperExecutionPolicy(
    ensure_started=True,
    wait_for_usable=True,
    retry_on_busy=True,
)
_START_COMMAND = CommandSemantics(
    require_live_session=False,
    persist_explicit_preference=True,
    announce_switch=True,
)
_LIVE_COMMAND = CommandSemantics(
    require_live_session=True,
    persist_explicit_preference=True,
    announce_switch=True,
)
_LIVE_READ_COMMAND = CommandSemantics(
    require_live_session=True,
    persist_explicit_preference=True,
    announce_switch=True,
    reject_starting_session=True,
)
_PASSIVE_COMMAND = CommandSemantics(require_live_session=False)


@dataclass(slots=True, frozen=True, kw_only=True)
class ProjectRequest:
    project_root: Path
    project_override: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())
        if self.project_override is not None:
            object.__setattr__(self, "project_override", self.project_override.resolve())


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
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_START_COMMAND,
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
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_COMMAND,
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
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_COMMAND,
            handler=lambda session_id: self._status_payload(request=request, session_id=session_id),
        )

    def wait(self, request: WaitRequest) -> CommandResponse:
        return self._handle_command(
            command_name="wait",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_COMMAND,
            handler=lambda session_id: self._wait_payload(request=request, session_id=session_id),
        )

    def vars(self, request: VarsRequest) -> CommandResponse:
        return self._handle_command(
            command_name="vars",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_READ_COMMAND,
            handler=lambda session_id: self._vars_payload(request=request, session_id=session_id),
        )

    def inspect(self, request: InspectRequest) -> CommandResponse:
        return self._handle_command(
            command_name="inspect",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_READ_COMMAND,
            handler=lambda session_id: self._inspect_payload(
                request=request,
                session_id=session_id,
            ),
        )

    def reload(self, request: ReloadRequest) -> CommandResponse:
        return self._handle_command(
            command_name="reload",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_READ_COMMAND,
            handler=lambda session_id: self._reload_payload(request=request, session_id=session_id),
        )

    def history(self, request: HistoryRequest) -> CommandResponse:
        validation_error = self._validate_history_request(request)
        if validation_error is not None:
            return validation_error

        return self._handle_command(
            command_name="history",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_COMMAND,
            handler=lambda session_id: self._history_payload(
                request=request,
                session_id=session_id,
            ),
        )

    def interrupt(self, request: InterruptRequest) -> CommandResponse:
        return self._handle_command(
            command_name="interrupt",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_COMMAND,
            handler=lambda session_id: self._interrupt_payload(
                request=request, session_id=session_id
            ),
        )

    def reset(self, request: ResetRequest) -> CommandResponse:
        return self._handle_command(
            command_name="reset",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_COMMAND,
            handler=lambda session_id: self._reset_payload(request=request, session_id=session_id),
        )

    def stop(self, request: StopRequest) -> CommandResponse:
        return self._handle_command(
            command_name="stop",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_LIVE_COMMAND,
            handler=lambda session_id: self._stop_payload(request=request, session_id=session_id),
        )

    def doctor(self, request: DoctorRequest) -> CommandResponse:
        return self._handle_command(
            command_name="doctor",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_PASSIVE_COMMAND,
            handler=lambda session_id: self._doctor_payload(request=request, session_id=session_id),
        )

    def sessions_list(self, request: SessionsListRequest) -> CommandResponse:
        return self._handle_command(
            command_name="sessions-list",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=None,
            semantics=_PASSIVE_COMMAND,
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
            project_override=request.project_override,
            requested_session_id=request.session_name,
            semantics=_PASSIVE_COMMAND,
            handler=lambda _: self.runtime.delete_session(
                project_root=request.project_root,
                session_id=request.session_name,
            ),
        )

    def sessions_delete_bulk(self, request: SessionsDeleteBulkRequest) -> CommandResponse:
        return self._handle_command(
            command_name="sessions-delete-bulk",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=None,
            semantics=_PASSIVE_COMMAND,
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
            project_override=request.project_override,
            requested_session_id=request.session_id,
            semantics=_PASSIVE_COMMAND,
            handler=lambda _: self._runs_list_payload(request=request),
        )

    def runs_show(self, request: RunLookupRequest) -> CommandResponse:
        return self._handle_command(
            command_name="runs-show",
            project_root=request.project_root,
            project_override=request.project_override,
            requested_session_id=None,
            semantics=_PASSIVE_COMMAND,
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
            project_override=request.project_override,
            requested_session_id=None,
            semantics=_PASSIVE_COMMAND,
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
            project_override=request.project_override,
            requested_session_id=None,
            semantics=_PASSIVE_COMMAND,
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
            project_override=request.project_override,
            requested_session_id=None,
            semantics=_PASSIVE_COMMAND,
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

    def _start_payload(self, *, request: StartRequest, session_id: str) -> KernelSessionData:
        status, started_new = self.runtime.start(
            project_root=request.project_root,
            session_id=session_id,
            python_executable=request.python_executable,
        )
        return KernelSessionData.from_kernel_status(status, started_new=started_new)

    def _exec_payload(
        self,
        *,
        request: ExecRequest,
        session_id: str,
        event_sink: ExecutionSink | None,
    ) -> ExecCommandData:
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
                data=serialize_command_data("exec", payload),
            )
        return payload

    def _status_payload(self, *, request: StatusRequest, session_id: str) -> KernelSessionData:
        if request.wait_for == "idle":
            access = self.executions.wait_for_session_access(
                project_root=request.project_root,
                session_id=session_id,
                timeout_s=request.timeout_s,
                target="idle",
            )
            return _kernel_session_data_from_access(access)
        if request.wait_for == "ready":
            access = self.executions.wait_for_session_access(
                project_root=request.project_root,
                session_id=session_id,
                timeout_s=request.timeout_s,
                target="ready",
            )
            return _kernel_session_data_from_access(access)
        return KernelSessionData.from_runtime_state(
            self.runtime.runtime_state(
                project_root=request.project_root,
                session_id=session_id,
            )
        )

    def _wait_payload(self, *, request: WaitRequest, session_id: str) -> KernelSessionData:
        access = self.executions.wait_for_session_access(
            project_root=request.project_root,
            session_id=session_id,
            timeout_s=request.timeout_s,
            target="usable",
        )
        return _kernel_session_data_from_access(access)

    def _vars_payload(self, *, request: VarsRequest, session_id: str) -> VarsCommandData:
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
            return VarsCommandData(
                values=display_values,
                access_metadata=helper_result.access_metadata,
            )
        return VarsCommandData(
            values=cast(list[VarDisplayEntry], values),
            access_metadata=helper_result.access_metadata,
        )

    def _inspect_payload(self, *, request: InspectRequest, session_id: str) -> InspectCommandData:
        helper_result = self.ops.inspect_var_result(
            project_root=request.project_root,
            session_id=session_id,
            name=request.name,
            execution_policy=_HELPER_EXECUTION_POLICY,
        )
        return InspectCommandData(
            payload=helper_result.payload,
            access_metadata=helper_result.access_metadata,
        )

    def _reload_payload(self, *, request: ReloadRequest, session_id: str) -> ReloadCommandData:
        helper_result = self.ops.reload_module_result(
            project_root=request.project_root,
            session_id=session_id,
            module_name=request.module_name,
            execution_policy=_HELPER_EXECUTION_POLICY,
        )
        return ReloadCommandData(
            payload=helper_result.payload,
            access_metadata=helper_result.access_metadata,
        )

    def _history_payload(self, *, request: HistoryRequest, session_id: str) -> HistoryCommandData:
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
        return HistoryCommandData(entries=list(selection.entries), full=request.full)

    def _interrupt_payload(self, *, request: InterruptRequest, session_id: str) -> InterruptPayload:
        self.runtime.interrupt(project_root=request.project_root, session_id=session_id)
        return {"interrupted": True}

    def _reset_payload(self, *, request: ResetRequest, session_id: str) -> ExecCommandData:
        managed = self.executions.reset_session(
            project_root=request.project_root,
            session_id=session_id,
            timeout_s=request.timeout_s,
        )
        payload = ExecCommandData(record=managed.record)
        if managed.record.status == "error":
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message="Reset failed",
                ename=managed.record.ename,
                evalue=managed.record.evalue,
                traceback=managed.record.traceback,
                data=serialize_command_data("reset", payload),
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

    def _runs_list_payload(self, *, request: RunsListRequest) -> RunsListCommandData:
        entries = self.executions.list_runs(
            project_root=request.project_root,
            session_id=request.session_id if request.session_id is not None else None,
            errors_only=request.errors,
        )
        if request.last is not None:
            entries = entries[-request.last :]
        return RunsListCommandData(
            runs=[RunListEntryData(payload=normalize_run_payload(entry)) for entry in entries]
        )

    def _run_lookup_payload(
        self,
        *,
        project_root: Path,
        run_reference: RunReference | None,
        timeout_s: float | None,
        event_sink: ExecutionSink | None,
        default_behavior: RunDefaultBehavior,
        skip_history: bool = False,
    ) -> RunLookupCommandData:
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
        run_payload = normalize_run_payload(run)
        payload = RunLookupCommandData(
            run=RunSnapshotData(
                payload=run_payload,
                include_output=not (observation is not None and skip_history),
                snapshot_stale=observation is not None
                and observation.completion_reason == "window_elapsed",
            ),
            status=cast(str | None, run_payload.get("status")),
        )
        if observation is not None:
            payload.completion_reason = observation.completion_reason
            payload.replayed_event_count = observation.replayed_event_count
            payload.emitted_event_count = observation.emitted_event_count
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
        project_override: Path | None,
        requested_session_id: str | None,
        semantics: CommandSemantics,
        handler: Callable[[str], CommandDataLike],
        response_session_id_resolver: Callable[[str, CommandDataLike], str] | None = None,
    ) -> CommandResponse:
        response_session_id = requested_session_id or DEFAULT_SESSION_ID

        try:
            context = self.session_targeting.resolve_command_context(
                project_root=project_root,
                requested_session_id=requested_session_id,
                semantics=semantics,
            )
            resolved_session_id = context.session_id
            response_session_id = resolved_session_id
            data = handler(resolved_session_id)
            if context.switched_session is not None:
                data = with_switched_session(data, context.switched_session)
            serialized_data = serialize_command_data(command_name, data)
            if response_session_id_resolver is not None:
                response_session_id = response_session_id_resolver(response_session_id, data)
            return success_response(
                command=command_name,
                project=str(project_root),
                session_id=response_session_id,
                data=data,
                suggestions=self.advisor.suggestions(
                    self._advice_context(
                        command_name=command_name,
                        response_status="ok",
                        data=serialized_data,
                        project_override=project_override,
                        session_id=resolved_session_id,
                        session_source=context.source,
                    )
                ),
                suggestion_actions=self.advisor.suggestion_actions(
                    self._advice_context(
                        command_name=command_name,
                        response_status="ok",
                        data=serialized_data,
                        project_override=project_override,
                        session_id=resolved_session_id,
                        session_source=context.source,
                    )
                ),
            )
        except AgentNBException as exc:
            error_session_id = response_session_id
            error_session_hint = exc.data.get("session_id")
            if isinstance(error_session_hint, str) and error_session_hint:
                error_session_id = error_session_hint
            error_session_source = "explicit" if requested_session_id is not None else None
            if error_session_source is None:
                session_source_hint = exc.data.get("session_source")
                if session_source_hint in {"explicit", "remembered", "sole_live", "default"}:
                    error_session_source = cast(ResolutionSource, session_source_hint)
            return error_response(
                command=command_name,
                project=str(project_root),
                session_id=error_session_id,
                code=exc.code,
                message=exc.message,
                ename=exc.ename,
                evalue=exc.evalue,
                traceback=exc.traceback,
                data=exc.data,
                suggestions=self.advisor.suggestions(
                    self._advice_context(
                        command_name=command_name,
                        response_status="error",
                        data=exc.data,
                        project_override=project_override,
                        session_id=error_session_id,
                        session_source=error_session_source,
                        error_code=exc.code,
                        error_name=exc.ename,
                        error_value=exc.evalue,
                    )
                ),
                suggestion_actions=self.advisor.suggestion_actions(
                    self._advice_context(
                        command_name=command_name,
                        response_status="error",
                        data=exc.data,
                        project_override=project_override,
                        session_id=error_session_id,
                        session_source=error_session_source,
                        error_code=exc.code,
                        error_name=exc.ename,
                        error_value=exc.evalue,
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
                    self._advice_context(
                        command_name=command_name,
                        response_status="error",
                        data={},
                        project_override=project_override,
                        session_id=response_session_id,
                        session_source="explicit" if requested_session_id is not None else None,
                        error_code="INTERNAL_ERROR",
                        error_name=type(exc).__name__,
                        error_value=str(exc),
                    )
                ),
                suggestion_actions=self.advisor.suggestion_actions(
                    self._advice_context(
                        command_name=command_name,
                        response_status="error",
                        data={},
                        project_override=project_override,
                        session_id=response_session_id,
                        session_source="explicit" if requested_session_id is not None else None,
                        error_code="INTERNAL_ERROR",
                        error_name=type(exc).__name__,
                        error_value=str(exc),
                    )
                ),
            )

    def _advice_context(
        self,
        *,
        command_name: str,
        response_status: str,
        data: Mapping[str, object],
        project_override: Path | None,
        session_id: str | None,
        session_source: ResolutionSource | None,
        error_code: str | None = None,
        error_name: str | None = None,
        error_value: str | None = None,
    ) -> AdviceContext:
        return AdviceContext(
            command_name=command_name,
            response_status=response_status,
            data=data,
            error_code=error_code,
            error_name=error_name,
            error_value=error_value,
            session_id=session_id,
            project_override=project_override,
            session_source=session_source,
        )


def _run_response_session_id(current_session_id: str, data: CommandDataLike) -> str:
    if isinstance(data, RunLookupCommandData):
        session_id = run_lookup_session_id(data)
        if isinstance(session_id, str) and session_id:
            return session_id
    if isinstance(data, Mapping):
        run = cast(Mapping[str, object], data).get("run")
        if isinstance(run, dict):
            run_payload = cast(Mapping[str, object], run)
            session_id = run_payload.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
    return current_session_id


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

    def build(self, managed: ManagedExecution) -> ExecCommandData:
        invocation = self.request.invocation
        payload = ExecCommandData(
            record=managed.record,
            no_truncate=invocation.no_truncate,
            source_kind=self.request.source_kind,
            source_path=str(self.request.source_path)
            if self.request.source_path is not None
            else None,
            background=invocation.is_background,
            ensured_started=invocation.ensure_started,
            started_new_session=managed.start_outcome.started_new_session,
            initial_runtime_state=managed.start_outcome.initial_runtime_state,
            session_restarted=managed.start_outcome.session_restarted,
        )
        if invocation.output_selector is not None:
            payload.selected_output = invocation.output_selector
            serialized_payload = serialize_command_data("exec", payload)
            payload.selected_text = selected_exec_output(
                serialized_payload,
                cast(str, invocation.output_selector),
            )
        session_python = self._session_python()
        if session_python is not None:
            payload.session_python = session_python
        namespace_delta = self._file_exec_namespace_delta(payload)
        if namespace_delta is not None:
            payload.namespace_delta = namespace_delta
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

    def _file_exec_namespace_delta(self, payload: ExecCommandData) -> NamespaceDeltaPayload | None:
        if self.request.source_kind != "file" or self.request.invocation.is_background:
            return None
        if payload.record.status == "error":
            return None
        serialized_payload = serialize_command_data("exec", payload)
        if self.namespace_before is None or not _exec_payload_is_empty(serialized_payload):
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


def _exec_payload_is_empty(payload: Mapping[str, object]) -> bool:
    for key in ("result", "stdout", "stderr", "selected_text"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return False
    namespace_delta = payload.get("namespace_delta")
    entries = (
        cast(Mapping[str, object], namespace_delta).get("entries")
        if isinstance(namespace_delta, dict)
        else None
    )
    return not entries


def _kernel_session_data_from_access(access: SessionAccessOutcome) -> KernelSessionData:
    assert access.status is not None
    return KernelSessionData.from_kernel_status(
        access.status,
        runtime_state=access.runtime_state,
        session_exists=access.status.alive,
        waited=access.waited,
        waited_for=access.waited_for,
        waited_ms=access.waited_ms,
        initial_runtime_state=access.initial_runtime_state,
    )


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
