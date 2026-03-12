from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .compact import (
    compact_execution_payload,
    compact_history_entry,
    compact_inspect_payload,
    compact_run_entry,
    compact_traceback,
)
from .contracts import CommandResponse, ExecutionSink, error_response, success_response
from .errors import AgentNBException
from .execution import ExecutionService
from .ops import NotebookOps
from .runtime import KernelRuntime
from .session import DEFAULT_SESSION_ID

OutputSelector = str
StatusWaitFor = Literal["ready", "idle"]


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
    ensure_started: bool = False
    background: bool = False
    stream: bool = False
    output_selector: OutputSelector | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class StartRequest(SessionRequest):
    python_executable: Path | None = None
    auto_install: bool = False


@dataclass(slots=True, frozen=True, kw_only=True)
class StatusRequest(SessionRequest):
    wait_for: StatusWaitFor | None = None
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
    errors: bool = False
    latest: bool = False
    last: int | None = None
    include_internal: bool = False


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
    auto_fix: bool = False


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionsListRequest(ProjectRequest):
    pass


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionsDeleteRequest(ProjectRequest):
    session_name: str


@dataclass(slots=True, frozen=True, kw_only=True)
class RunsListRequest(SessionRequest):
    errors: bool = False
    last: int | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class RunLookupRequest(ProjectRequest):
    execution_id: str


@dataclass(slots=True, frozen=True, kw_only=True)
class RunsWaitRequest(RunLookupRequest):
    timeout_s: float = 30.0


@dataclass(slots=True, frozen=True, kw_only=True)
class RunsFollowRequest(RunLookupRequest):
    timeout_s: float = 30.0


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
    ) -> None:
        resolved_runtime = runtime or KernelRuntime()
        self.runtime = resolved_runtime
        self.executions = executions or ExecutionService(resolved_runtime)
        self.ops = ops or NotebookOps(resolved_runtime)

    def start(self, request: StartRequest) -> CommandResponse:
        return self._handle_command(
            command_name="start",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=False,
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
            handler=lambda session_id: self._status_payload(request=request, session_id=session_id),
        )

    def vars(self, request: VarsRequest) -> CommandResponse:
        return self._handle_command(
            command_name="vars",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
            handler=lambda session_id: self._vars_payload(request=request, session_id=session_id),
        )

    def inspect(self, request: InspectRequest) -> CommandResponse:
        return self._handle_command(
            command_name="inspect",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
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
            handler=lambda session_id: self._reload_payload(request=request, session_id=session_id),
        )

    def history(self, request: HistoryRequest) -> CommandResponse:
        if request.latest and request.last is not None:
            return self._input_error(
                command_name="history",
                project_root=request.project_root,
                session_id=request.session_id,
                message="Use either --latest or --last, not both.",
            )

        return self._handle_command(
            command_name="history",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
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
            handler=lambda session_id: self._reset_payload(request=request, session_id=session_id),
        )

    def stop(self, request: StopRequest) -> CommandResponse:
        return self._handle_command(
            command_name="stop",
            project_root=request.project_root,
            requested_session_id=request.session_id,
            require_live_session=True,
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
            handler=lambda _: {
                "sessions": self.runtime.list_sessions(project_root=request.project_root)
            },
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
            handler=lambda _: {
                "run": self.executions.get_run(
                    project_root=request.project_root,
                    execution_id=request.execution_id,
                )
            },
        )

    def runs_wait(self, request: RunsWaitRequest) -> CommandResponse:
        return self._handle_command(
            command_name="runs-wait",
            project_root=request.project_root,
            requested_session_id=None,
            require_live_session=False,
            handler=lambda _: {
                "run": self.executions.wait_for_run(
                    project_root=request.project_root,
                    execution_id=request.execution_id,
                    timeout_s=request.timeout_s,
                )
            },
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
            handler=lambda _: {
                "run": self.executions.follow_run(
                    project_root=request.project_root,
                    execution_id=request.execution_id,
                    timeout_s=request.timeout_s,
                    event_sink=event_sink,
                )
            },
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
                execution_id=request.execution_id,
            ),
        )

    def _start_payload(self, *, request: StartRequest, session_id: str) -> dict[str, object]:
        status, started_new = self.runtime.start(
            project_root=request.project_root,
            session_id=session_id,
            python_executable=request.python_executable,
            auto_install=request.auto_install,
        )
        payload = status.to_dict()
        payload["started_new"] = started_new
        payload["auto_install"] = request.auto_install
        return payload

    def _exec_payload(
        self,
        *,
        request: ExecRequest,
        session_id: str,
        event_sink: ExecutionSink | None,
    ) -> dict[str, object]:
        if request.background:
            managed = self.executions.start_background_code(
                project_root=request.project_root,
                session_id=session_id,
                code=request.code,
                ensure_started=request.ensure_started,
            )
        else:
            managed = self.executions.execute_code(
                project_root=request.project_root,
                session_id=session_id,
                code=request.code,
                timeout_s=request.timeout_s,
                ensure_started=request.ensure_started,
                event_sink=event_sink if request.stream else None,
            )

        payload = compact_execution_payload(managed.record.to_execution_payload())
        if request.background:
            payload["background"] = True
        if request.ensure_started:
            payload["ensured_started"] = True
            payload["started_new_session"] = managed.started_new_session
        if request.output_selector is not None:
            payload["selected_output"] = request.output_selector
            payload["selected_text"] = select_exec_output(payload, request.output_selector)

        if not request.background and managed.record.status == "error":
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message="Execution failed",
                ename=managed.record.ename,
                evalue=managed.record.evalue,
                traceback=managed.record.traceback,
                data=payload,
            )
        return payload

    def _status_payload(self, *, request: StatusRequest, session_id: str) -> dict[str, object]:
        if request.wait_for == "idle":
            payload = self.runtime.wait_for_idle(
                project_root=request.project_root,
                session_id=session_id,
                timeout_s=request.timeout_s,
            ).to_dict()
            payload["waited"] = True
            payload["waited_for"] = "idle"
            return payload
        if request.wait_for == "ready":
            payload = self.runtime.wait_for_ready(
                project_root=request.project_root,
                session_id=session_id,
                timeout_s=request.timeout_s,
            ).to_dict()
            payload["waited"] = True
            payload["waited_for"] = "ready"
            return payload
        return self.runtime.status(
            project_root=request.project_root,
            session_id=session_id,
        ).to_dict()

    def _vars_payload(self, *, request: VarsRequest, session_id: str) -> dict[str, object]:
        values = self.ops.list_vars(project_root=request.project_root, session_id=session_id)
        if request.match_text:
            match_lower = request.match_text.lower()
            values = [item for item in values if match_lower in str(item["name"]).lower()]
        if request.recent is not None:
            values = values[-request.recent :]
        if not request.include_types:
            values = [{"name": item["name"], "repr": item["repr"]} for item in values]
        return {"vars": values}

    def _inspect_payload(self, *, request: InspectRequest, session_id: str) -> dict[str, object]:
        payload = self.ops.inspect_var(
            project_root=request.project_root,
            session_id=session_id,
            name=request.name,
        )
        return {"inspect": compact_inspect_payload(payload)}

    def _reload_payload(self, *, request: ReloadRequest, session_id: str) -> dict[str, object]:
        return self.ops.reload_module(
            project_root=request.project_root,
            session_id=session_id,
            module_name=request.module_name,
        )

    def _history_payload(self, *, request: HistoryRequest, session_id: str) -> dict[str, object]:
        entries = self.runtime.history(
            project_root=request.project_root,
            session_id=session_id,
            errors_only=request.errors,
            include_internal=request.include_internal,
        )
        entries = [compact_history_entry(entry) for entry in entries]
        if request.latest:
            entries = entries[-1:]
        elif request.last is not None:
            entries = entries[-request.last :]
        return {"entries": entries}

    def _interrupt_payload(
        self, *, request: InterruptRequest, session_id: str
    ) -> dict[str, object]:
        self.runtime.interrupt(project_root=request.project_root, session_id=session_id)
        return {"interrupted": True}

    def _reset_payload(self, *, request: ResetRequest, session_id: str) -> dict[str, object]:
        managed = self.executions.reset_session(
            project_root=request.project_root,
            session_id=session_id,
            timeout_s=request.timeout_s,
        )
        payload = compact_execution_payload(managed.record.to_execution_payload())
        if managed.record.status == "error":
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message="Reset failed",
                ename=managed.record.ename,
                evalue=managed.record.evalue,
                traceback=managed.record.traceback,
                data=payload,
            )
        return payload

    def _stop_payload(self, *, request: StopRequest, session_id: str) -> dict[str, object]:
        self.runtime.stop(project_root=request.project_root, session_id=session_id)
        return {"stopped": True}

    def _doctor_payload(self, *, request: DoctorRequest, session_id: str) -> dict[str, object]:
        return self.runtime.doctor(
            project_root=request.project_root,
            session_id=session_id,
            python_executable=request.python_executable,
            auto_fix=request.auto_fix,
        )

    def _runs_list_payload(self, *, request: RunsListRequest) -> dict[str, object]:
        entries = self.executions.list_runs(
            project_root=request.project_root,
            session_id=request.session_id if request.session_id is not None else None,
            errors_only=request.errors,
        )
        compacted = [compact_run_entry(entry) for entry in entries]
        if request.last is not None:
            compacted = compacted[-request.last :]
        return {"runs": compacted}

    def _validate_exec_request(self, request: ExecRequest) -> CommandResponse | None:
        if request.background and request.output_selector is not None:
            return self._input_error(
                command_name="exec",
                project_root=request.project_root,
                session_id=request.session_id,
                message="Output selectors are not supported with --background.",
            )
        if request.stream and request.background:
            return self._input_error(
                command_name="exec",
                project_root=request.project_root,
                session_id=request.session_id,
                message="--stream and --background cannot be used together.",
            )
        if request.stream and request.output_selector is not None:
            return self._input_error(
                command_name="exec",
                project_root=request.project_root,
                session_id=request.session_id,
                message="Output selectors are not supported with --stream.",
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
            suggestions=suggestions_for_command(
                command_name,
                "error",
                {},
                error_code="INVALID_INPUT",
            ),
        )

    def _handle_command(
        self,
        *,
        command_name: str,
        project_root: Path,
        requested_session_id: str | None,
        require_live_session: bool,
        handler: Callable[[str], dict[str, object]],
        response_session_id_resolver: Callable[[str, dict[str, object]], str] | None = None,
    ) -> CommandResponse:
        response_session_id = requested_session_id or DEFAULT_SESSION_ID

        try:
            resolved_session_id = self.runtime.resolve_session_id(
                project_root=project_root,
                requested_session_id=requested_session_id,
                require_live_session=require_live_session,
            )
            response_session_id = resolved_session_id
            data = handler(resolved_session_id)
            if response_session_id_resolver is not None:
                response_session_id = response_session_id_resolver(response_session_id, data)
            return success_response(
                command=command_name,
                project=str(project_root),
                session_id=response_session_id,
                data=data,
                suggestions=suggestions_for_command(command_name, "ok", data),
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
                traceback=compact_traceback(exc.traceback),
                data=cast(dict[str, object], exc.data),
                suggestions=suggestions_for_command(
                    command_name,
                    "error",
                    cast(dict[str, object], exc.data),
                    error_code=exc.code,
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
                suggestions=suggestions_for_command(
                    command_name,
                    "error",
                    {},
                    error_code="INTERNAL_ERROR",
                ),
            )


def _run_response_session_id(current_session_id: str, data: dict[str, object]) -> str:
    run = data.get("run")
    if isinstance(run, dict):
        run_payload = cast(dict[str, object], run)
        session_id = run_payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            return session_id
    return current_session_id


def suggestions_for_command(
    command_name: str,
    response_status: str,
    data: dict[str, object],
    *,
    error_code: str | None = None,
) -> list[str]:
    if error_code == "AMBIGUOUS_SESSION":
        return [
            "Run `agentnb sessions list --json` to see the live session names.",
            f"Retry with `agentnb {command_name} --session NAME --json` to target one explicitly.",
        ]
    if command_name == "start":
        return [
            'Run `agentnb exec "..." --json` to execute code in the live kernel.',
            "Run `agentnb vars --recent 5 --json` to inspect the newest namespace changes.",
            "Run `agentnb status --json` to confirm the kernel is still alive.",
        ]
    if command_name == "status":
        if data.get("alive"):
            return [
                'Run `agentnb exec "..." --json` to execute code.',
                "Run `agentnb vars --recent 5 --json` to inspect current variables.",
                "Run `agentnb stop --json` when the session is no longer needed.",
            ]
        return [
            "Run `agentnb start --json` to start a project-scoped kernel.",
            "Run `agentnb doctor --json` if startup has been failing.",
        ]
    if command_name == "exec":
        if response_status == "ok":
            if data.get("background"):
                return [
                    "Run `agentnb runs wait EXECUTION_ID --json` to wait for the final result.",
                    (
                        "Run `agentnb runs show EXECUTION_ID --json` "
                        "to inspect the current run record."
                    ),
                    (
                        "Run `agentnb runs cancel EXECUTION_ID --json` "
                        "to stop a long-running background run."
                    ),
                ]
            return [
                "Run `agentnb vars --recent 5 --json` to inspect the updated namespace.",
                "Run `agentnb inspect NAME --json` to inspect a specific variable.",
                "Run `agentnb history --json` to review prior executions.",
            ]
        return [
            "Run `agentnb history --errors --json` to review recent failures.",
            "Run `agentnb interrupt --json` if execution may still be stuck.",
            "Run `agentnb reset --json` if the namespace needs a clean slate.",
        ]
    if command_name == "vars":
        return [
            "Run `agentnb inspect NAME --json` for details on a variable.",
            "Run `agentnb vars --match TEXT --json` to filter noisy namespaces by name.",
            'Run `agentnb exec "..." --json` to add or modify live state.',
        ]
    if command_name == "inspect":
        return [
            "Run `agentnb vars --recent 5 --json` to inspect more of the namespace.",
            'Run `agentnb exec "..." --json` to probe or transform that value.',
        ]
    if command_name == "reload":
        return [
            'Run `agentnb exec "..." --json` to verify the reloaded module behavior.',
            "Run `agentnb reset --json` if stale state is still causing issues.",
        ]
    if command_name == "history":
        return [
            'Run `agentnb exec "..." --json` to continue iterating.',
            "Run `agentnb history --errors --json` to focus on failures only.",
        ]
    if command_name == "interrupt":
        return [
            'Retry with `agentnb exec "..." --json` once the kernel is idle.',
            "Run `agentnb reset --json` if interrupted code left partial state behind.",
        ]
    if command_name == "reset":
        return [
            'Run `agentnb exec "setup_code" --json` to rebuild required state.',
            "Run `agentnb vars --json` to confirm the namespace is clean.",
        ]
    if command_name == "stop":
        return [
            "Run `agentnb start --json` to create a fresh kernel later.",
        ]
    if command_name == "doctor":
        if data.get("ready"):
            return [
                "Run `agentnb start --json` to start the kernel.",
            ]
        return [
            "Run `agentnb doctor --fix --json` to attempt automatic fixes.",
            "Run `agentnb start --python /path/to/python --json` to try a specific interpreter.",
        ]
    if command_name == "sessions-list":
        if not data.get("sessions"):
            return [
                "Run `agentnb start --json` to start the default session.",
                (
                    'Run `agentnb exec --ensure-started --json "..."` '
                    "to start and execute in one step."
                ),
            ]
        return [
            "Run `agentnb start --session NAME --json` to start another named session.",
            "Run `agentnb status --session NAME --json` to inspect one session.",
        ]
    if command_name == "sessions-delete":
        return [
            "Run `agentnb sessions list --json` to confirm the remaining sessions.",
        ]
    if command_name == "runs-list":
        return [
            "Run `agentnb runs show EXECUTION_ID --json` to inspect one run in detail.",
            "Run `agentnb history --json` to review the semantic session history view.",
        ]
    if command_name == "runs-show":
        run = data.get("run")
        run_payload = cast(dict[str, object], run) if isinstance(run, dict) else None
        run_status = run_payload.get("status") if run_payload is not None else None
        if run_status == "running":
            return [
                (
                    "Run `agentnb runs follow EXECUTION_ID --json` "
                    "to stream new events until the run finishes."
                ),
                "Run `agentnb runs wait EXECUTION_ID --json` to block for the final snapshot.",
                "Run `agentnb runs cancel EXECUTION_ID --json` to stop the background run.",
            ]
        return [
            "Run `agentnb runs list --json` to inspect more recorded runs.",
            "Run `agentnb history --json` to review the session-level history view.",
        ]
    if command_name == "runs-follow":
        return [
            "Run `agentnb runs show EXECUTION_ID --json` to inspect the latest persisted snapshot.",
        ]
    if command_name == "runs-wait":
        return [
            "Run `agentnb runs show EXECUTION_ID --json` to inspect the completed run.",
        ]
    if command_name == "runs-cancel":
        if data.get("cancel_requested"):
            if data.get("session_outcome") == "preserved":
                session_id = data.get("session_id") or "default"
                return [
                    (
                        f"Run `agentnb status --session {session_id} --wait-idle --json` "
                        "to confirm the session is ready for more work."
                    ),
                    (
                        "Run `agentnb runs show EXECUTION_ID --json` "
                        "to inspect the cancelled run record."
                    ),
                ]
            if data.get("session_outcome") == "stopped":
                return [
                    (
                        "Run `agentnb start --session NAME --json` "
                        "to start a fresh session explicitly."
                    ),
                    (
                        'Run `agentnb exec --ensure-started "..." --json` '
                        "to restart and execute in one step."
                    ),
                ]
        return [
            "Run `agentnb runs show EXECUTION_ID --json` to inspect the persisted run snapshot.",
        ]
    return []


def select_exec_output(payload: dict[str, object], selector: OutputSelector) -> str:
    if selector == "result":
        result = payload.get("result")
        return "" if result is None else str(result)
    value = payload.get(selector)
    return "" if value is None else str(value)
