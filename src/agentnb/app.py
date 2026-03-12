from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .compact import compact_execution_payload, compact_traceback
from .contracts import CommandResponse, ExecutionSink, error_response, success_response
from .errors import AgentNBException
from .execution import ExecutionService
from .runtime import KernelRuntime
from .session import DEFAULT_SESSION_ID

OutputSelector = str


@dataclass(slots=True, frozen=True)
class ExecRequest:
    project_root: Path
    code: str
    session_id: str | None = None
    timeout_s: float = 30.0
    ensure_started: bool = False
    background: bool = False
    stream: bool = False
    output_selector: OutputSelector | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())


class AgentNBApp:
    def __init__(
        self,
        *,
        runtime: KernelRuntime | None = None,
        executions: ExecutionService | None = None,
    ) -> None:
        resolved_runtime = runtime or KernelRuntime()
        self.runtime = resolved_runtime
        self.executions = executions or ExecutionService(resolved_runtime)

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
            handler=lambda resolved_session_id: self._exec_payload(
                request=request,
                session_id=resolved_session_id,
                event_sink=event_sink,
            ),
        )

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

    def _validate_exec_request(self, request: ExecRequest) -> CommandResponse | None:
        if request.background and request.output_selector is not None:
            return self._input_error(
                request=request,
                message="Output selectors are not supported with --background.",
            )
        if request.stream and request.background:
            return self._input_error(
                request=request,
                message="--stream and --background cannot be used together.",
            )
        if request.stream and request.output_selector is not None:
            return self._input_error(
                request=request,
                message="Output selectors are not supported with --stream.",
            )
        return None

    def _input_error(self, *, request: ExecRequest, message: str) -> CommandResponse:
        return error_response(
            command="exec",
            project=str(request.project_root),
            session_id=request.session_id or DEFAULT_SESSION_ID,
            code="INVALID_INPUT",
            message=message,
            suggestions=suggestions_for_command(
                "exec",
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
