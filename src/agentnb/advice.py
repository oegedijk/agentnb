from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .command_data import (
    CommandDataLike,
    DoctorCommandData,
    ExecCommandData,
    HistoryCommandData,
    KernelSessionData,
    ReloadCommandData,
    RunCancelCommandData,
    RunLookupCommandData,
    RunsListCommandData,
    SessionsListCommandData,
    VarsCommandData,
)
from .contracts import SuggestionAction
from .suggestions import SessionScopeSource, SuggestionOutputMode, SuggestionScope

_IMPORT_PACKAGE_NAME_OVERRIDES = {
    "sklearn": "scikit-learn",
}
_STDOUT_LIMIT = 200
_RESULT_LIMIT = 240


@dataclass(slots=True, frozen=True)
class AdviceContext:
    command_name: str
    response_status: str
    command_data: CommandDataLike | None = None
    data: Mapping[str, object] = field(default_factory=dict)
    error_code: str | None = None
    error_name: str | None = None
    error_value: str | None = None
    session_id: str | None = None
    project_override: Path | None = None
    session_source: SessionScopeSource | None = None
    output_mode: str = "json"


@dataclass(slots=True, frozen=True)
class _AdviceStep:
    text: str
    action: SuggestionAction | None = None


class AdvicePolicy:
    def suggestions(self, context: AdviceContext) -> list[str]:
        return [step.text for step in self._steps(context)]

    def suggestion_actions(self, context: AdviceContext) -> list[SuggestionAction]:
        return [step.action for step in self._steps(context) if step.action is not None]

    def _steps(self, context: AdviceContext) -> list[_AdviceStep]:
        command_name = context.command_name
        data = context.data
        command_data = context.command_data
        scope = _scope(context)

        if context.error_code == "AMBIGUOUS_SESSION":
            return [
                _run_step(
                    scope, "List sessions", "to see the live session names.", "sessions", "list"
                ),
                _retry_step(
                    scope,
                    "Retry with --session",
                    "to target one explicitly.",
                    command_name,
                    "--session",
                    "NAME",
                ),
            ]

        if context.error_code == "AMBIGUOUS_EXECUTION":
            return [
                _run_step(scope, "List runs", "to inspect matching run ids.", "runs", "list"),
                _retry_step(
                    scope,
                    "Show run",
                    "to target one explicitly.",
                    "runs",
                    "show",
                    "EXECUTION_ID",
                ),
            ]

        if context.error_code == "SESSION_BUSY":
            execution_id = data.get("active_execution_id")
            if isinstance(execution_id, str) and execution_id:
                return [
                    _run_step(
                        scope,
                        "Wait for run",
                        "to wait for the blocking run.",
                        "runs",
                        "wait",
                        execution_id,
                    ),
                    _run_step(
                        scope,
                        "Show run",
                        "to inspect the blocking run.",
                        "runs",
                        "show",
                        execution_id,
                    ),
                ]
            return [
                _run_step(
                    scope,
                    "Wait for session",
                    "to block until the session is idle, then retry.",
                    "wait",
                    session_scoped=True,
                )
            ]

        if context.error_code == "KERNEL_NOT_READY":
            return [
                _run_step(
                    scope,
                    "Wait for kernel",
                    "to wait for startup to finish.",
                    "wait",
                    session_scoped=True,
                ),
                _run_step(
                    scope,
                    "Check status",
                    "to inspect the current session state.",
                    "status",
                    session_scoped=True,
                ),
            ]

        if context.error_code in {"NO_KERNEL", "BACKEND_ERROR", "KERNEL_DEAD"}:
            return [
                _run_step(
                    scope,
                    "Start kernel",
                    "to start the kernel.",
                    "start",
                    session_scoped=True,
                ),
                _run_step(
                    scope,
                    "Run doctor",
                    "if startup has been failing.",
                    "doctor",
                    session_scoped=True,
                ),
            ]

        if command_name == "start":
            return []

        if command_name == "status":
            runtime_state = (
                command_data.runtime_state
                if isinstance(command_data, KernelSessionData)
                else data.get("runtime_state")
            )
            if runtime_state == "starting":
                return [
                    _run_step(
                        scope,
                        "Wait for kernel",
                        "to wait for startup to finish.",
                        "wait",
                        session_scoped=True,
                        with_action=False,
                    )
                ]
            alive = (
                command_data.alive
                if isinstance(command_data, KernelSessionData)
                else data.get("alive")
            )
            busy = (
                command_data.busy
                if isinstance(command_data, KernelSessionData)
                else data.get("busy")
            )
            if alive:
                if busy:
                    return [
                        _run_step(
                            scope,
                            "Wait for session",
                            "to wait until the session is usable.",
                            "wait",
                            session_scoped=True,
                            with_action=False,
                        )
                    ]
                return []
            return [
                _run_step(
                    scope,
                    "Start kernel",
                    "to start a project-scoped kernel.",
                    "start",
                    session_scoped=True,
                    with_action=False,
                ),
                _run_step(
                    scope,
                    "Run doctor",
                    "if startup has been failing.",
                    "doctor",
                    session_scoped=True,
                    with_action=False,
                ),
            ]

        if command_name == "wait":
            if context.response_status == "ok":
                return []
            return [
                _run_step(
                    scope,
                    "Check status",
                    "to inspect the current session state.",
                    "status",
                    session_scoped=True,
                    with_action=False,
                ),
                _run_step(
                    scope,
                    "Start kernel",
                    "if the target session is not running yet.",
                    "start",
                    session_scoped=True,
                    with_action=False,
                ),
            ]

        if command_name == "exec":
            if context.response_status == "ok":
                if _file_exec_truncated(command_data, data):
                    return _file_exec_truncation_steps(scope, data)
                if isinstance(command_data, ExecCommandData):
                    if command_data.background:
                        execution_id = command_data.record.execution_id or "EXECUTION_ID"
                        return [
                            _run_step(
                                scope,
                                "Wait for run",
                                "to wait for the final result.",
                                "runs",
                                "wait",
                                execution_id,
                            ),
                            _run_step(
                                scope,
                                "Show run",
                                "to inspect the current run record.",
                                "runs",
                                "show",
                                execution_id,
                            ),
                            _run_step(
                                scope,
                                "Cancel run",
                                "to stop the background run.",
                                "runs",
                                "cancel",
                                execution_id,
                            ),
                        ]
                elif data.get("background"):
                    execution_id = _execution_id(data)
                    return [
                        _run_step(
                            scope,
                            "Wait for run",
                            "to wait for the final result.",
                            "runs",
                            "wait",
                            execution_id,
                        ),
                        _run_step(
                            scope,
                            "Show run",
                            "to inspect the current run record.",
                            "runs",
                            "show",
                            execution_id,
                        ),
                        _run_step(
                            scope,
                            "Cancel run",
                            "to stop the background run.",
                            "runs",
                            "cancel",
                            execution_id,
                        ),
                    ]
                if _exec_output_is_empty(command_data, data):
                    execution_id = (
                        command_data.record.execution_id
                        if isinstance(command_data, ExecCommandData)
                        else _execution_id(data)
                    )
                    return [
                        _run_step(
                            scope,
                            "Inspect vars",
                            "to inspect namespace changes.",
                            "vars",
                            "--recent",
                            "5",
                            session_scoped=True,
                            with_action=False,
                        ),
                        _run_step(
                            scope,
                            "Review history",
                            "to review this execution.",
                            "history",
                            execution_id,
                            session_scoped=True,
                            with_action=False,
                        ),
                    ]
                return []

            if context.error_code == "INVALID_INPUT":
                if data.get("input_shape") == "exec_file_path":
                    file_path = str(data.get("source_path") or "PATH")
                    return [
                        _run_step(
                            scope,
                            "Use exec --file",
                            "to execute the file through `exec --file`.",
                            "exec",
                            "--file",
                            file_path,
                            session_scoped=True,
                        ),
                        _run_step(
                            scope,
                            "Use top-level file exec",
                            "to use the top-level file-execution hot path.",
                            file_path,
                            session_scoped=True,
                        ),
                    ]
                return []

            if context.error_code == "TIMEOUT":
                steps = [
                    _run_step(
                        scope,
                        "Review history",
                        "to review the latest failure.",
                        "history",
                        "@last-error",
                        session_scoped=True,
                        with_action=False,
                    )
                ]
                if data.get("interrupt_recommended"):
                    steps.append(
                        _run_step(
                            scope,
                            "Interrupt kernel",
                            "if execution may still be stuck.",
                            "interrupt",
                            session_scoped=True,
                            with_action=False,
                        )
                    )
                steps.append(
                    _run_step(
                        scope,
                        "Reset session",
                        "if the namespace needs a clean slate.",
                        "reset",
                        session_scoped=True,
                        with_action=False,
                    )
                )
                return steps

            if context.error_name == "ModuleNotFoundError":
                module = _extract_module_name(context.error_value)
                if module:
                    package = _package_name_for_import(module)
                    session_python = _session_python(data, command_data)
                    if session_python:
                        return [
                            _shell_step(
                                "Repair live session",
                                (
                                    "Repair the live session: run "
                                    f"`uv pip install --python {session_python} {package}` "
                                    "in your shell."
                                ),
                                "uv",
                                "pip",
                                "install",
                                "--python",
                                session_python,
                                package,
                            ),
                            _shell_step(
                                "Add dependency",
                                (
                                    f"For a durable project dependency, run `uv add {package}` "
                                    "in your shell."
                                ),
                                "uv",
                                "add",
                                package,
                            ),
                            _text_step("Then retry the execution."),
                        ]
                    return [
                        _shell_step(
                            "Install dependency",
                            (
                                f"Install the missing module: run `uv add {package}` "
                                "in your shell (not inside the session)."
                            ),
                            "uv",
                            "add",
                            package,
                        ),
                        _text_step("Then retry the execution."),
                    ]

            if _missing_pip_in_called_process(context):
                package = _extract_pip_install_target(context.error_value)
                session_python = _session_python(data, command_data)
                if package and session_python:
                    return [
                        _text_step(
                            "The selected interpreter does not provide pip inside the live session."
                        ),
                        _shell_step(
                            "Repair live session",
                            (
                                "Install the dependency from this project with "
                                "run `uv pip install --python "
                                f"{session_python} {package}` in your shell."
                            ),
                            "uv",
                            "pip",
                            "install",
                            "--python",
                            session_python,
                            package,
                        ),
                    ]
                if session_python:
                    install_hint = (
                        f"use `uv pip install --python {session_python} PACKAGE` in your shell."
                    )
                elif package:
                    install_hint = f"run `uv add {package}` in your shell (not inside the session)."
                else:
                    install_hint = "use `uv add PACKAGE` in your shell (not inside the session)."
                steps = [
                    _text_step(
                        "The selected interpreter does not provide pip inside the live session."
                    )
                ]
                if package:
                    steps.append(
                        _shell_step(
                            "Install dependency",
                            f"Install the dependency from this project with {install_hint}",
                            "uv",
                            "add",
                            package,
                        )
                    )
                else:
                    steps.append(
                        _text_step(f"Install the dependency from this project with {install_hint}")
                    )
                return steps

            if context.error_name == "NameError" and context.session_id:
                return [
                    _run_step(
                        scope,
                        "Inspect vars",
                        "to inspect the namespace.",
                        "vars",
                        session_scoped=True,
                        with_action=False,
                    ),
                    _run_step(
                        scope,
                        "List sessions",
                        "to see all live sessions.",
                        "sessions",
                        "list",
                        with_action=False,
                    ),
                    _run_step(
                        scope,
                        "Review history",
                        "to review the latest failure.",
                        "history",
                        "@last-error",
                        session_scoped=True,
                        with_action=False,
                    ),
                ]

            return [
                _run_step(
                    scope,
                    "Review history",
                    "to review the latest failure.",
                    "history",
                    "@last-error",
                    session_scoped=True,
                    with_action=False,
                ),
                _run_step(
                    scope,
                    "Interrupt kernel",
                    "if execution may still be stuck.",
                    "interrupt",
                    session_scoped=True,
                    with_action=False,
                ),
                _run_step(
                    scope,
                    "Reset session",
                    "if the namespace needs a clean slate.",
                    "reset",
                    session_scoped=True,
                    with_action=False,
                ),
            ]

        if command_name == "vars":
            if isinstance(command_data, VarsCommandData):
                has_vars = bool(command_data.values)
            else:
                has_vars = bool(data.get("vars"))
            if not has_vars:
                return [
                    _run_step(
                        scope,
                        "Execute code",
                        "to create some live state first.",
                        "...",
                        with_action=False,
                    )
                ]
            return []

        if command_name == "inspect":
            return []

        if command_name == "reload":
            stale_names = (
                command_data.payload.get("stale_names")
                if isinstance(command_data, ReloadCommandData)
                else data.get("stale_names")
            )
            if stale_names:
                return [
                    _run_step(
                        scope,
                        "Reset session",
                        "if stale objects are still causing issues.",
                        "reset",
                        session_scoped=True,
                        with_action=False,
                    )
                ]
            reloaded = (
                command_data.payload.get("reloaded_modules")
                if isinstance(command_data, ReloadCommandData)
                else data.get("reloaded_modules")
            )
            if isinstance(reloaded, list) and not reloaded:
                return [
                    _text_step("No project-local modules were found to reload."),
                    _text_step(
                        "To reload a specific module, run "
                        "`importlib.reload(module)` in the session."
                    ),
                ]
            return []

        if command_name == "history":
            entries = (
                command_data.entries
                if isinstance(command_data, HistoryCommandData)
                else data.get("entries")
            )
            if not entries:
                return [
                    _run_step(
                        scope,
                        "Execute code",
                        "to record the first execution step.",
                        "...",
                        with_action=False,
                    )
                ]
            return []

        if command_name == "interrupt":
            return [
                _retry_step(
                    scope,
                    "Retry execution",
                    "once the kernel is idle.",
                    "exec",
                    "...",
                    session_scoped=True,
                    with_action=False,
                ),
                _run_step(
                    scope,
                    "Reset session",
                    "if interrupted code left partial state behind.",
                    "reset",
                    session_scoped=True,
                    with_action=False,
                ),
            ]

        if command_name == "reset":
            return [
                _run_step(
                    scope,
                    "Rebuild state",
                    "to rebuild the state you need.",
                    "exec",
                    "...",
                )
            ]

        if command_name == "stop":
            return []

        if command_name == "doctor":
            if isinstance(command_data, DoctorCommandData):
                ready = command_data.ready
                kernel_alive = command_data.kernel_alive
                session_exists = command_data.session_exists
            else:
                ready = bool(data.get("ready"))
                kernel_alive = bool(data.get("kernel_alive"))
                session_exists = bool(data.get("session_exists"))
            if ready:
                if kernel_alive:
                    return [_text_step("Kernel is already running.")]
                if session_exists:
                    return [
                        _text_step("Session exists but kernel is not running."),
                        _run_step(
                            scope,
                            "Start kernel",
                            "to restart the kernel.",
                            "start",
                            session_scoped=True,
                            with_action=False,
                        ),
                    ]
                return [
                    _run_step(
                        scope,
                        "Start kernel",
                        "to start the kernel.",
                        "start",
                        session_scoped=True,
                        with_action=False,
                    )
                ]
            return [
                _run_step(
                    scope,
                    "Run doctor",
                    "in your shell.",
                    "doctor",
                    prefix="Run the install command shown by",
                    with_action=False,
                ),
                _text_step(
                    "Then restart with "
                    f"`{scope.render_command('--fresh', '...', include_output=False)}` "
                    f"or run `{scope.render_command('start', session_scoped=True)}` again."
                ),
                _text_step(
                    "Run `agentnb start --python /path/to/python --json` "
                    "to try a specific interpreter."
                ),
            ]

        if command_name == "sessions-list":
            has_sessions = (
                bool(command_data.sessions)
                if isinstance(command_data, SessionsListCommandData)
                else bool(data.get("sessions"))
            )
            if not has_sessions:
                return [
                    _run_step(
                        scope,
                        "Start kernel",
                        "to start the default session.",
                        "start",
                        session_scoped=True,
                        with_action=False,
                    ),
                    _run_step(
                        scope,
                        "Execute code",
                        "to start and execute in one step.",
                        "...",
                        with_action=False,
                    ),
                ]
            return []

        if command_name == "sessions-delete":
            return []

        if command_name == "runs-list":
            has_runs = (
                bool(command_data.runs)
                if isinstance(command_data, RunsListCommandData)
                else bool(data.get("runs"))
            )
            if not has_runs:
                return [
                    _run_step(
                        scope,
                        "Run in background",
                        "to create a persisted run record.",
                        "--background",
                        "...",
                        with_action=False,
                    )
                ]
            return []

        if command_name == "runs-show":
            if isinstance(command_data, RunLookupCommandData):
                run_payload = command_data.run.payload
                run_status = run_payload.get("status")
            else:
                run = data.get("run")
                run_payload = cast(Mapping[str, object], run) if isinstance(run, dict) else None
                run_status = run_payload.get("status") if run_payload is not None else None
            if _run_is_active(run_status):
                execution_id = _execution_id(run_payload)
                return [
                    _run_step(
                        scope,
                        "Follow run",
                        "to stream new events.",
                        "runs",
                        "follow",
                        execution_id,
                        with_action=False,
                    ),
                    _run_step(
                        scope,
                        "Wait for run",
                        "to wait for the final snapshot.",
                        "runs",
                        "wait",
                        execution_id,
                        with_action=False,
                    ),
                    _run_step(
                        scope,
                        "Cancel run",
                        "to stop the background run.",
                        "runs",
                        "cancel",
                        execution_id,
                        with_action=False,
                    ),
                ]
            return []

        if command_name == "runs-follow":
            if isinstance(command_data, RunLookupCommandData):
                run_payload = command_data.run.payload
                run_status = run_payload.get("status")
            else:
                run = data.get("run")
                run_payload = cast(Mapping[str, object], run) if isinstance(run, dict) else None
                run_status = run_payload.get("status") if run_payload is not None else None
            if _run_is_active(run_status):
                execution_id = _execution_id(run_payload)
                return [
                    _run_step(
                        scope,
                        "Wait for run",
                        "to wait for the final snapshot.",
                        "runs",
                        "wait",
                        execution_id,
                    ),
                    _run_step(
                        scope,
                        "Show run",
                        "to inspect the latest run snapshot.",
                        "runs",
                        "show",
                        execution_id,
                    ),
                    _run_step(
                        scope,
                        "Cancel run",
                        "to stop the background run.",
                        "runs",
                        "cancel",
                        execution_id,
                    ),
                ]
            return []

        if command_name == "runs-wait":
            return []

        if command_name == "runs-cancel":
            execution_id = (
                command_data.execution_id
                if isinstance(command_data, RunCancelCommandData)
                else _execution_id(data)
            )
            cancel_requested = (
                command_data.cancel_requested
                if isinstance(command_data, RunCancelCommandData)
                else bool(data.get("cancel_requested"))
            )
            status = (
                command_data.status
                if isinstance(command_data, RunCancelCommandData)
                else data.get("status")
            )
            session_outcome = (
                command_data.session_outcome
                if isinstance(command_data, RunCancelCommandData)
                else data.get("session_outcome")
            )
            session_id = (
                command_data.session_id
                if isinstance(command_data, RunCancelCommandData)
                else str(data.get("session_id") or "default")
            )
            if cancel_requested:
                if status == "ok":
                    return [
                        _run_step(
                            scope,
                            "Show run",
                            "to inspect the completed run.",
                            "runs",
                            "show",
                            execution_id,
                            with_action=False,
                        ),
                        _run_step(
                            scope,
                            "Wait for session",
                            "to confirm the session is ready.",
                            "wait",
                            "--session",
                            "NAME",
                            include_output=True,
                            with_action=False,
                        ),
                    ]
                if session_outcome == "preserved":
                    return [
                        _run_step(
                            scope,
                            "Wait for session",
                            "to confirm the session is ready for more work.",
                            "wait",
                            session_id=session_id,
                            with_action=False,
                        ),
                        _run_step(
                            scope,
                            "Show run",
                            "to inspect the cancelled run record.",
                            "runs",
                            "show",
                            execution_id,
                            with_action=False,
                        ),
                    ]
                if session_outcome == "stopped":
                    return [
                        _run_step(
                            scope,
                            "Start kernel",
                            "to start a fresh session explicitly.",
                            "start",
                            "--session",
                            "NAME",
                            with_action=False,
                        ),
                        _run_step(
                            scope,
                            "Execute code",
                            "to restart and execute in one step.",
                            "...",
                            with_action=False,
                        ),
                    ]
            return [
                _run_step(
                    scope,
                    "Show run",
                    "to inspect the persisted run snapshot.",
                    "runs",
                    "show",
                    execution_id,
                    with_action=False,
                )
            ]

        return []


def _scope(context: AdviceContext) -> SuggestionScope:
    return SuggestionScope(
        project_override=context.project_override,
        session_id=context.session_id,
        session_source=context.session_source,
        output_mode=cast(SuggestionOutputMode, context.output_mode),
    )


def _run_is_active(status: object) -> bool:
    return isinstance(status, str) and status in {"starting", "running"}


def _execution_id(data: Mapping[str, object] | None) -> str:
    if data is None:
        return "EXECUTION_ID"
    execution_id = data.get("execution_id")
    if isinstance(execution_id, str) and execution_id:
        return execution_id
    return "EXECUTION_ID"


def _extract_module_name(error_value: str | None) -> str | None:
    if not error_value:
        return None
    prefix = "No module named '"
    if error_value.startswith(prefix) and error_value.endswith("'"):
        name = error_value[len(prefix) : -1]
        return name.split(".")[0]
    return None


def _package_name_for_import(module_name: str) -> str:
    return _IMPORT_PACKAGE_NAME_OVERRIDES.get(module_name, module_name)


def _extract_pip_install_target(error_value: str | None) -> str | None:
    if not error_value:
        return None
    match = re.search(r"pip', 'install', '([^']+)'", error_value)
    if match is None:
        return None
    package = match.group(1).strip()
    return package or None


def _missing_pip_in_called_process(context: AdviceContext) -> bool:
    if context.command_name != "exec" or context.error_name != "CalledProcessError":
        return False
    stderr = context.data.get("stderr")
    if isinstance(stderr, str) and "No module named pip" in stderr:
        return True
    return bool(context.error_value and "No module named pip" in context.error_value)


def _session_python(
    data: Mapping[str, object],
    command_data: CommandDataLike | None = None,
) -> str | None:
    if isinstance(command_data, ExecCommandData):
        return command_data.session_python
    value = data.get("session_python")
    if isinstance(value, str) and value:
        return value
    return None


def _exec_output_is_empty(
    command_data: CommandDataLike | None,
    data: Mapping[str, object],
) -> bool:
    if isinstance(command_data, ExecCommandData):
        for value in (
            command_data.record.result,
            command_data.record.stdout,
            command_data.record.stderr,
            command_data.selected_text,
        ):
            if isinstance(value, str) and value:
                return False
        return not (command_data.namespace_delta and command_data.namespace_delta.get("entries"))
    for key in ("result", "stdout", "stderr", "selected_text"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return False
    namespace_delta = data.get("namespace_delta")
    entries = (
        cast(Mapping[str, object], namespace_delta).get("entries")
        if isinstance(namespace_delta, dict)
        else None
    )
    return not entries


def _file_exec_truncated(command_data: CommandDataLike | None, data: Mapping[str, object]) -> bool:
    if isinstance(command_data, ExecCommandData):
        if command_data.source_kind != "file" or command_data.no_truncate:
            return False
        outcome = command_data.record.outcome()
        return any(
            isinstance(value, str) and len(value) > limit
            for value, limit in (
                (outcome.stdout, _STDOUT_LIMIT),
                (outcome.stderr, _STDOUT_LIMIT),
                (outcome.result, _RESULT_LIMIT),
            )
        )
    if data.get("source_kind") != "file":
        return False
    truncation_keys = ("stdout_truncated", "stderr_truncated", "result_truncated")
    return any(data.get(key) is True for key in truncation_keys)


def _file_exec_truncation_steps(
    scope: SuggestionScope,
    data: Mapping[str, object],
) -> list[_AdviceStep]:
    source_path = str(data.get("source_path") or "PATH")
    return [
        _run_step(
            scope,
            "Rerun without truncation",
            "to rerun the file without truncation.",
            "exec",
            "--no-truncate",
            "--file",
            source_path,
            session_scoped=True,
            include_output=False,
        ),
        _run_step(
            scope,
            "Inspect recent vars",
            "to inspect the newest live variables.",
            "vars",
            "--recent",
            "5",
            session_scoped=True,
        ),
    ]


def _run_step(
    scope: SuggestionScope,
    label: str,
    detail: str,
    *tokens: str,
    session_scoped: bool = False,
    include_output: bool = True,
    session_id: str | None = None,
    prefix: str = "Run",
    with_action: bool = True,
) -> _AdviceStep:
    command = scope.render_command(
        *tokens,
        session_scoped=session_scoped,
        include_output=include_output,
        session_id=session_id,
    )
    return _AdviceStep(
        text=f"{prefix} `{command}` {detail}",
        action=(
            scope.command_action(
                label,
                *tokens,
                session_scoped=session_scoped,
                include_output=include_output,
                session_id=session_id,
            )
            if with_action
            else None
        ),
    )


def _retry_step(
    scope: SuggestionScope,
    label: str,
    detail: str,
    *tokens: str,
    session_scoped: bool = False,
    with_action: bool = True,
) -> _AdviceStep:
    command = scope.render_command(*tokens, session_scoped=session_scoped)
    return _AdviceStep(
        text=f"Retry with `{command}` {detail}",
        action=(
            scope.command_action(label, *tokens, session_scoped=session_scoped)
            if with_action
            else None
        ),
    )


def _shell_step(label: str, text: str, command: str, *args: str) -> _AdviceStep:
    return _AdviceStep(text=text, action=_shell_action(label, command, *args))


def _text_step(text: str) -> _AdviceStep:
    return _AdviceStep(text=text)


def _shell_action(label: str, command: str, *args: str) -> SuggestionAction:
    return {
        "kind": "shell",
        "label": label,
        "command": command,
        "args": list(args),
    }
